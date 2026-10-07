from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from yoyo.copier.config import env

logger = logging.getLogger(__name__)

LIVE_BASE_URL = "https://fapi.binance.com"
TESTNET_BASE_URL = "https://demo-fapi.binance.com"


def binance_symbol(inst_id: str) -> str:
    return str(inst_id or "").upper().replace("-USDT-SWAP", "USDT").replace("-", "")


def inst_id_from_binance(symbol: str) -> str:
    text = str(symbol or "").upper()
    return f"{text[:-4]}-USDT-SWAP" if text.endswith("USDT") else text


def binance_position_side(side: str | None) -> str:
    return "LONG" if str(side or "").lower() == "long" else "SHORT"


class BinanceClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        secret_key: str | None = None,
        testnet: bool | None = None,
        base_url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else env.binance_api_key
        self.secret_key = secret_key if secret_key is not None else env.binance_secret_key
        self.testnet = env.binance_testnet if testnet is None else bool(testnet)
        self.host = (
            base_url
            or env.binance_base_url
            or (TESTNET_BASE_URL if self.testnet else LIVE_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout
        self.last_positions_error: str | None = None

    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        private: bool = False,
    ) -> Any:
        payload = {key: value for key, value in (params or {}).items() if value is not None}
        headers = {"X-MBX-APIKEY": self.api_key} if self.api_key else {}
        if private:
            if not self.configured():
                return {"code": -1, "msg": "Binance API keys not configured"}
            payload["timestamp"] = int(time.time() * 1000)
            payload["recvWindow"] = 5000
            query = urlencode(payload)
            payload["signature"] = hmac.new(
                self.secret_key.encode(), query.encode(), hashlib.sha256
            ).hexdigest()
        try:
            response = httpx.request(
                method.upper(),
                f"{self.host}{path}",
                params=payload,
                headers=headers,
                timeout=self.timeout,
                trust_env=False,
            )
            data = response.json() if response.content else {}
            if response.status_code >= 400 and isinstance(data, dict):
                data.setdefault("status_code", response.status_code)
            return data
        except Exception as exc:
            logger.exception("Binance request failed: %s %s", method, path)
            return {"code": -1, "msg": str(exc)}

    def health_check(self) -> dict[str, Any]:
        data = self._request("GET", "/fapi/v2/account", private=True)
        if not isinstance(data, dict) or data.get("code"):
            error = str(data.get("msg") if isinstance(data, dict) else data)
            return {
                "ok": False,
                "error": _friendly_error(error),
                "raw_error": error,
                "ip_restricted": is_ip_restricted_error(error),
                "request_ip": request_ip_from_error(error),
            }
        return {"ok": True, "balance": self._balance_summary(data)}

    def get_balance_usdt(self) -> float:
        return self.get_balance_summary()["available_usdt"]

    def get_balance_summary(self) -> dict[str, float]:
        data = self._request("GET", "/fapi/v2/account", private=True)
        return self._balance_summary(data) if isinstance(data, dict) and not data.get("code") else _empty_balance()

    def _balance_summary(self, data: dict[str, Any]) -> dict[str, float]:
        return {
            "total_usdt": _float(data.get("totalWalletBalance")),
            "available_usdt": _float(data.get("availableBalance")),
            "unrealized_pnl": _float(data.get("totalUnrealizedProfit")),
        }

    def get_positions(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/fapi/v2/positionRisk", private=True)
        if not isinstance(data, list):
            self.last_positions_error = str(
                data.get("msg") or data.get("code") or data
                if isinstance(data, dict)
                else data
            )[:500]
            return []
        self.last_positions_error = None
        rows = []
        for row in data:
            amount = _float(row.get("positionAmt"))
            if amount == 0:
                continue
            rows.append(
                {
                    **row,
                    "instId": inst_id_from_binance(str(row.get("symbol") or "")),
                    "posSide": "long" if amount > 0 else "short",
                    "pos": str(abs(amount)),
                    "avgPx": str(row.get("entryPrice") or ""),
                    "markPx": str(row.get("markPrice") or ""),
                    "liqPx": str(row.get("liquidationPrice") or ""),
                    "notionalUsd": str(abs(_float(row.get("notional")))),
                    "upl": str(row.get("unRealizedProfit") or "0"),
                }
            )
        return rows

    def get_open_orders(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/fapi/v1/openOrders", private=True)
        if not isinstance(data, list):
            return []
        return [
            {
                **row,
                "instId": inst_id_from_binance(str(row.get("symbol") or "")),
                "ordId": str(row.get("orderId") or ""),
                "sz": str(row.get("origQty") or ""),
                "px": str(row.get("price") or ""),
            }
            for row in data
            if str(row.get("type") or "") not in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
        ]

    def get_open_triggers(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/fapi/v1/openAlgoOrders", private=True)
        if not isinstance(data, list):
            return []
        return [
            {
                **row,
                "instId": inst_id_from_binance(str(row.get("symbol") or "")),
                "ordId": str(row.get("algoId") or ""),
                "triggerPx": str(row.get("triggerPrice") or ""),
                "slTriggerPx": str(row.get("triggerPrice") or "") if row.get("orderType") == "STOP_MARKET" else "",
                "tpTriggerPx": str(row.get("triggerPrice") or "") if row.get("orderType") == "TAKE_PROFIT_MARKET" else "",
            }
            for row in data
            if str(row.get("orderType") or "") in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
        ]

    def get_instrument(self, inst_id: str) -> dict[str, Any]:
        data = self._request("GET", "/fapi/v1/exchangeInfo")
        if not isinstance(data, dict):
            return {}
        return next(
            (row for row in data.get("symbols") or [] if row.get("symbol") == binance_symbol(inst_id)),
            {},
        )

    def get_max_leverage(self, inst_id: str, fallback: int = 125) -> int:
        data = self._request(
            "GET",
            "/fapi/v1/leverageBracket",
            params={"symbol": binance_symbol(inst_id)},
            private=True,
        )
        try:
            rows = data if isinstance(data, list) else []
            brackets = rows[0].get("brackets") if rows else []
            leverages = [
                int(float(row.get("initialLeverage") or 0))
                for row in brackets
                if isinstance(row, dict)
            ]
            maximum = max(leverages or [0])
            if maximum > 0:
                return min(max(maximum, 1), 125)
        except (TypeError, ValueError, IndexError):
            pass
        return min(max(int(fallback or 125), 1), 125)

    def get_ticker(self, inst_id: str) -> float:
        data = self._request("GET", "/fapi/v1/ticker/price", params={"symbol": binance_symbol(inst_id)})
        return _float(data.get("price")) if isinstance(data, dict) else 0.0

    def set_leverage(self, inst_id: str, leverage: int, td_mode: str = "cross") -> dict[str, Any]:
        symbol = binance_symbol(inst_id)
        if str(td_mode).lower() == "cross":
            self._request(
                "POST",
                "/fapi/v1/marginType",
                params={"symbol": symbol, "marginType": "CROSSED"},
                private=True,
            )
        return self._request(
            "POST",
            "/fapi/v1/leverage",
            params={"symbol": symbol, "leverage": leverage},
            private=True,
        )

    def place_order(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/fapi/v1/order", params=params, private=True)

    def place_algo_order(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/fapi/v1/algoOrder", params=params, private=True)

    def close_position(self, inst_id: str, close_pct: float = 100.0) -> dict[str, Any]:
        position = next((row for row in self.get_positions() if row.get("instId") == inst_id), None)
        if not position:
            return {"ok": False, "skipped": True, "error": "交易所当前无对应持仓"}
        amount = _float(position.get("positionAmt"))
        quantity = abs(amount) * min(max(close_pct, 0), 100) / 100
        position_side = binance_position_side("long" if amount > 0 else "short")
        response = self.place_order(
            {
                "symbol": binance_symbol(inst_id),
                "side": "SELL" if amount > 0 else "BUY",
                "positionSide": position_side,
                "type": "MARKET",
                "quantity": _fmt(quantity),
            }
        )
        return {
            "ok": isinstance(response, dict) and response.get("orderId") is not None,
            "response": response,
            "closed_size": quantity,
            "close_pct": close_pct,
        }

    def cancel_open_orders(self, inst_id: str) -> dict[str, Any]:
        orders = [row for row in self.get_open_orders() if row.get("instId") == inst_id]
        if not orders:
            return {"ok": False, "skipped": True, "error": "交易所当前无对应挂单"}
        response = self._request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            params={"symbol": binance_symbol(inst_id)},
            private=True,
        )
        return {"ok": response.get("code") == 200, "response": response, "canceled_count": len(orders)}

    def update_stop_loss(self, inst_id: str, trigger_px: float) -> dict[str, Any]:
        return self._replace_trigger(inst_id, trigger_px, "STOP_MARKET")

    def update_take_profit(self, inst_id: str, trigger_px: float) -> dict[str, Any]:
        return self._replace_trigger(inst_id, trigger_px, "TAKE_PROFIT_MARKET")

    def _replace_trigger(self, inst_id: str, trigger_px: float, order_type: str) -> dict[str, Any]:
        position = next((row for row in self.get_positions() if row.get("instId") == inst_id), None)
        if not position:
            return {"ok": False, "skipped": True, "error": "交易所当前无对应持仓"}
        amount = _float(position.get("positionAmt"))
        position_side = binance_position_side("long" if amount > 0 else "short")
        existing = [
            row
            for row in self.get_open_triggers()
            if row.get("instId") == inst_id and row.get("orderType") == order_type
        ]
        response = self.place_algo_order(
            {
                "algoType": "CONDITIONAL",
                "symbol": binance_symbol(inst_id),
                "side": "SELL" if amount > 0 else "BUY",
                "positionSide": position_side,
                "type": order_type,
                "triggerPrice": _fmt(trigger_px),
                "quantity": _fmt(abs(amount)),
                "workingType": "MARK_PRICE",
            }
        )
        ok = isinstance(response, dict) and (
            response.get("orderId") is not None or response.get("algoId") is not None
        )
        canceled = []
        if ok:
            for row in existing:
                canceled.append(
                    self._request(
                        "DELETE",
                        "/fapi/v1/algoOrder",
                        params={"algoId": row.get("algoId")},
                        private=True,
                    )
                )
        return {"ok": ok, "response": response, "canceled_count": len(canceled), "trigger_px": trigger_px}


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fmt(value: float) -> str:
    return f"{value:.12f}".rstrip("0").rstrip(".")


def _empty_balance() -> dict[str, float]:
    return {"total_usdt": 0.0, "available_usdt": 0.0, "unrealized_pnl": 0.0}


def request_ip_from_error(error: str) -> str | None:
    match = re.search(r"request ip:\s*([0-9a-fA-F:.]+)", str(error), re.I)
    return match.group(1) if match else None


def is_ip_restricted_error(error: str) -> bool:
    text = str(error).lower()
    return "invalid api-key, ip, or permissions" in text or "request ip:" in text


def _friendly_error(error: str) -> str:
    if is_ip_restricted_error(error):
        ip = request_ip_from_error(error)
        suffix = f" 当前出口 IP: {ip}" if ip else ""
        return f"Binance IP 白名单拒绝。{suffix}。请把该 IP 加入币安 API 白名单，或在 Shadowrocket/TUN 中为 Binance API 配置稳定直连/固定出口。"
    return error

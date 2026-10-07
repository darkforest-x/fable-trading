from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from yoyo.copier.config import env

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v4"
LIVE_BASE_URL = "https://fx-api.gateio.ws/api/v4"
TESTNET_BASE_URL = "https://api-testnet.gateapi.io/api/v4"


def gate_contract(inst_id: str, settle: str = "usdt") -> str:
    """Convert BTC-USDT-SWAP / BTC to Gate futures contract format BTC_USDT."""
    text = str(inst_id or "").strip().upper()
    for suffix in ("-USDT-SWAP", "-USD-SWAP", "/USDT", "USDT", "-SWAP"):
        text = text.replace(suffix, "")
    quote = settle.strip().upper() or "USDT"
    return f"{text}_{quote}"


def inst_id_from_gate(contract: str) -> str:
    text = str(contract or "").strip().upper()
    if text.endswith("_USDT"):
        return f"{text[:-5]}-USDT-SWAP"
    if text.endswith("_USD"):
        return f"{text[:-4]}-USD-SWAP"
    return text.replace("_", "-")


class GateClient:
    """Small synchronous Gate APIv4 futures client."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        secret_key: str | None = None,
        settle: str | None = None,
        testnet: bool | None = None,
        base_url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else env.gate_api_key
        self.secret_key = secret_key if secret_key is not None else env.gate_secret_key
        self.settle = (settle or env.gate_settle or "usdt").strip().lower()
        self.testnet = env.gate_testnet if testnet is None else bool(testnet)
        configured_base = base_url or env.gate_base_url
        if not configured_base:
            configured_base = TESTNET_BASE_URL if self.testnet else LIVE_BASE_URL
        self.host = configured_base.rstrip("/")
        self.prefix = API_PREFIX
        if self.host.endswith(API_PREFIX):
            self.host = self.host[: -len(API_PREFIX)]
        self.timeout = timeout
        self.last_positions_error: str | None = None

    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    def _sign_headers(self, method: str, path: str, query: str, body: str) -> dict[str, str]:
        ts = str(time.time())
        body_hash = hashlib.sha512(body.encode("utf-8")).hexdigest()
        sign_payload = "\n".join([method.upper(), f"{self.prefix}{path}", query, body_hash, ts])
        sign = hmac.new(
            self.secret_key.encode("utf-8"),
            sign_payload.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        return {"KEY": self.api_key, "Timestamp": ts, "SIGN": sign}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        private: bool = False,
    ) -> Any:
        query = urlencode([(k, str(v)) for k, v in (params or {}).items() if v is not None])
        payload = ""
        if body is not None:
            payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if private:
            if not self.configured():
                return {"label": "NOT_CONFIGURED", "message": "Gate API keys not configured"}
            headers.update(self._sign_headers(method, path, query, payload))

        url = f"{self.host}{self.prefix}{path}"
        if query:
            url = f"{url}?{query}"

        try:
            resp = httpx.request(
                method.upper(),
                url,
                content=payload.encode("utf-8") if payload else None,
                headers=headers,
                timeout=self.timeout,
                trust_env=False,
            )
            data = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                if isinstance(data, dict):
                    data.setdefault("status_code", resp.status_code)
                    return data
                return {"label": "HTTP_ERROR", "message": resp.text, "status_code": resp.status_code}
            return data
        except Exception as e:
            logger.exception("Gate request failed: %s %s", method, path)
            return {"label": "REQUEST_FAILED", "message": str(e)}

    def get_balance_usdt(self) -> float:
        return self.get_balance_summary().get("available_usdt", 0.0)

    def health_check(self) -> dict[str, Any]:
        data = self._request("GET", f"/futures/{self.settle}/accounts", private=True)
        if not isinstance(data, dict):
            return {"ok": False, "error": "unexpected Gate account response"}
        if data.get("label"):
            return {
                "ok": False,
                "error": str(data.get("message") or data.get("label") or "Gate account unavailable"),
            }
        return {
            "ok": True,
            "balance": {
                "total_usdt": _as_float(data.get("total")),
                "available_usdt": _as_float(data.get("available")),
                "unrealized_pnl": _as_float(data.get("unrealised_pnl")),
            },
        }

    def get_balance_summary(self) -> dict[str, float]:
        out = {"total_usdt": 0.0, "available_usdt": 0.0, "unrealized_pnl": 0.0}
        data = self._request("GET", f"/futures/{self.settle}/accounts", private=True)
        if not isinstance(data, dict) or data.get("label"):
            return out
        out["total_usdt"] = _as_float(data.get("total"))
        out["available_usdt"] = _as_float(data.get("available"))
        out["unrealized_pnl"] = _as_float(data.get("unrealised_pnl"))
        return out

    def get_instrument(self, inst_id: str) -> dict[str, Any]:
        contract = gate_contract(inst_id, self.settle)
        data = self._request("GET", f"/futures/{self.settle}/contracts/{contract}")
        return data if isinstance(data, dict) and not data.get("label") else {}

    def get_ticker(self, inst_id: str) -> float:
        contract = gate_contract(inst_id, self.settle)
        data = self._request(
            "GET",
            f"/futures/{self.settle}/tickers",
            params={"contract": contract},
        )
        if isinstance(data, list) and data:
            row = data[0]
            return _as_float(row.get("mark_price") or row.get("last") or row.get("last_price"))
        return 0.0

    def set_leverage(self, inst_id: str, lever: int, mgn_mode: str = "cross") -> dict[str, Any]:
        contract = gate_contract(inst_id, self.settle)
        is_cross = str(mgn_mode or "").lower() == "cross"
        params = (
            {"leverage": "0", "cross_leverage_limit": str(lever)}
            if is_cross
            else {"leverage": str(lever)}
        )
        data = self._request(
            "POST",
            f"/futures/{self.settle}/positions/{contract}/leverage",
            params=params,
            private=True,
        )
        return data if isinstance(data, dict) else {"data": data}

    def place_order(self, params: dict[str, Any]) -> dict[str, Any]:
        data = self._request(
            "POST",
            f"/futures/{self.settle}/orders",
            body=params,
            private=True,
        )
        return data if isinstance(data, dict) else {"data": data}

    def place_price_order(self, params: dict[str, Any]) -> dict[str, Any]:
        data = self._request(
            "POST",
            f"/futures/{self.settle}/price_orders",
            body=params,
            private=True,
        )
        return data if isinstance(data, dict) else {"data": data}

    def get_open_orders(self) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            f"/futures/{self.settle}/orders",
            params={"status": "open"},
            private=True,
        )
        if not isinstance(data, list):
            return []
        orders: list[dict[str, Any]] = []
        for row in data:
            inst_id = inst_id_from_gate(str(row.get("contract") or ""))
            size = abs(_as_float(row.get("size")))
            price = _as_float(row.get("price"))
            multiplier = _as_float(self.get_instrument(inst_id).get("quanto_multiplier")) or 1.0
            orders.append(
                {
                    **row,
                    "instId": inst_id,
                    "ordId": str(row.get("id") or ""),
                    "side": "buy" if _as_float(row.get("size")) > 0 else "sell",
                    "sz": str(size),
                    "px": str(row.get("price") or ""),
                    "notionalUsd": str(size * price * multiplier),
                }
            )
        return orders

    def get_open_triggers(self) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            f"/futures/{self.settle}/price_orders",
            params={"status": "open"},
            private=True,
        )
        if not isinstance(data, list):
            return []
        return [
            {
                **row,
                "instId": inst_id_from_gate(str((row.get("initial") or {}).get("contract") or "")),
                "triggerPx": str((row.get("trigger") or {}).get("price") or ""),
                "ordId": str(row.get("id") or row.get("id_string") or ""),
            }
            for row in data
        ]

    def close_position(self, inst_id: str, close_pct: float = 100.0) -> dict[str, Any]:
        target = next((row for row in self.get_positions() if row.get("instId") == inst_id), None)
        if not target:
            return {"ok": False, "skipped": True, "error": "交易所当前无对应持仓"}
        position_size = _as_float(target.get("size"))
        close_size = max(1, int(abs(position_size) * min(max(close_pct, 0), 100) / 100))
        signed_size = -close_size if position_size > 0 else close_size
        response = self.place_order(
            {
                "contract": gate_contract(inst_id, self.settle),
                "size": signed_size,
                "price": "0",
                "tif": "ioc",
                "text": "t-close-signal",
                "reduce_only": True,
            }
        )
        ok = isinstance(response, dict) and not response.get("label") and response.get("id") is not None
        return {
            "ok": ok,
            "response": response,
            "closed_size": close_size,
            "close_pct": close_pct,
        }

    def cancel_open_orders(self, inst_id: str) -> dict[str, Any]:
        orders = [row for row in self.get_open_orders() if row.get("instId") == inst_id]
        responses = [
            self._request(
                "DELETE",
                f"/futures/{self.settle}/orders/{row.get('ordId')}",
                private=True,
            )
            for row in orders
            if row.get("ordId")
        ]
        ok = bool(orders) and all(not isinstance(row, dict) or not row.get("label") for row in responses)
        return {
            "ok": ok,
            "skipped": not orders,
            "error": "交易所当前无对应挂单" if not orders else None,
            "canceled_count": len(responses),
            "responses": responses,
        }

    def cancel_trigger_order(self, order_id: str) -> dict[str, Any]:
        response = self._request(
            "DELETE",
            f"/futures/{self.settle}/price_orders/{order_id}",
            private=True,
        )
        ok = not isinstance(response, dict) or not response.get("label")
        return {"ok": ok, "response": response, "order_id": order_id}

    def update_stop_loss(self, inst_id: str, trigger_px: float) -> dict[str, Any]:
        return self._replace_exit_trigger(inst_id, trigger_px, "sl")

    def update_take_profit(self, inst_id: str, trigger_px: float) -> dict[str, Any]:
        return self._replace_exit_trigger(inst_id, trigger_px, "tp")

    def _replace_exit_trigger(self, inst_id: str, trigger_px: float, kind: str) -> dict[str, Any]:
        position = next((row for row in self.get_positions() if row.get("instId") == inst_id), None)
        if not position:
            return {"ok": False, "skipped": True, "error": "交易所当前无对应持仓"}
        size = _as_float(position.get("size"))
        avg = _as_float(position.get("entry_price") or position.get("avgPx"))
        if size == 0:
            return {"ok": False, "skipped": True, "error": "交易所当前无对应持仓"}
        existing = []
        for row in self.get_open_triggers():
            if row.get("instId") != inst_id:
                continue
            current = _as_float(row.get("triggerPx"))
            is_loss_side = current < avg if size > 0 else current > avg
            if (kind == "sl") != is_loss_side:
                continue
            existing.append(row)
        close_size = -int(abs(size)) if size > 0 else int(abs(size))
        is_above_mark = trigger_px > (_as_float(position.get("mark_price")) or avg)
        response = self.place_price_order(
            {
                "initial": {
                    "contract": gate_contract(inst_id, self.settle),
                    "size": close_size,
                    "price": "0",
                    "close": False,
                    "tif": "ioc",
                    "text": "t-update-exit",
                    "reduce_only": True,
                },
                "trigger": {
                    "strategy_type": 0,
                    "price_type": 0,
                    "price": str(trigger_px),
                    "rule": 1 if is_above_mark else 2,
                },
            }
        )
        ok = isinstance(response, dict) and not response.get("label") and response.get("id") is not None
        canceled = []
        if ok:
            for row in existing:
                order_id = str(row.get("ordId") or "")
                if order_id:
                    canceled.append(
                        self._request(
                            "DELETE",
                            f"/futures/{self.settle}/price_orders/{order_id}",
                            private=True,
                        )
                    )
        return {
            "ok": ok,
            "response": response,
            "canceled_count": len(canceled),
            "trigger_px": trigger_px,
            "kind": kind,
        }

    def get_positions(self) -> list[dict[str, Any]]:
        data = self._request("GET", f"/futures/{self.settle}/positions", private=True)
        if not isinstance(data, list):
            self.last_positions_error = str(
                data.get("message") or data.get("label") or data
                if isinstance(data, dict)
                else data
            )[:500]
            return []
        self.last_positions_error = None
        positions: list[dict[str, Any]] = []
        for row in data:
            size = _as_float(row.get("size"))
            if size == 0:
                continue
            side = "long" if size > 0 else "short"
            positions.append(
                {
                    **row,
                    "instId": inst_id_from_gate(str(row.get("contract") or "")),
                    "posSide": side,
                    "pos": str(abs(size)),
                    "avgPx": str(row.get("entry_price") or ""),
                    "markPx": str(row.get("mark_price") or ""),
                    "liqPx": str(row.get("liq_price") or ""),
                    "notionalUsd": str(abs(_as_float(row.get("value")))),
                    "marginMode": str(row.get("pos_margin_mode") or ""),
                    "initialMargin": str(row.get("initial_margin") or ""),
                    "upl": str(row.get("unrealised_pnl") or "0"),
                }
            )
        return positions


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

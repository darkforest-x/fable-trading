from __future__ import annotations

import logging
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from yoyo.copier.config import env

logger = logging.getLogger(__name__)


class OkxClient:
    def __init__(self) -> None:
        flag = "1" if env.okx_demo else "0"
        try:
            from okx.Account import AccountAPI
            from okx.MarketData import MarketAPI
            from okx.PublicData import PublicAPI
            from okx.Trade import TradeAPI

            self.account = AccountAPI(
                env.okx_api_key, env.okx_secret_key, env.okx_passphrase, False, flag
            )
            self.trade = TradeAPI(
                env.okx_api_key, env.okx_secret_key, env.okx_passphrase, False, flag
            )
            self.market = MarketAPI(flag=flag)
            self.public = PublicAPI(flag=flag)
        except Exception:
            logger.exception("OKX SDK initialization failed")
            self.account = self.trade = self.market = self.public = None
        self.last_positions_error: str | None = None

    def get_balance_usdt(self) -> float:
        return self.get_balance_summary()["available_usdt"]

    def health_check(self) -> dict[str, Any]:
        if not self.account:
            return {"ok": False, "error": "OKX client unavailable"}
        try:
            response = self.account.get_account_balance(ccy="USDT")
            if str(response.get("code") or "0") != "0":
                return {"ok": False, "error": str(response.get("msg") or "OKX account unavailable")}
            return {"ok": True, "balance": _balance_summary(response)}
        except Exception as e:
            logger.exception("OKX health check failed")
            return {"ok": False, "error": str(e)}

    def get_balance_summary(self) -> dict[str, float]:
        if not self.account:
            return _empty_balance()
        try:
            return _balance_summary(self.account.get_account_balance(ccy="USDT"))
        except Exception:
            logger.exception("OKX balance failed")
        return _empty_balance()

    def get_positions(self) -> list[dict[str, Any]]:
        if not self.account:
            self.last_positions_error = "OKX client unavailable"
            return []
        try:
            response = self.account.get_positions(instType="SWAP")
            if str(response.get("code") or "0") != "0":
                self.last_positions_error = str(response.get("msg") or response)[:500]
                return []
            self.last_positions_error = None
            return list(response.get("data") or [])
        except Exception as e:
            logger.exception("OKX positions failed")
            self.last_positions_error = str(e)[:500]
            return []

    def get_open_orders(self) -> list[dict[str, Any]]:
        if not self.trade:
            return []
        try:
            response = self.trade.get_order_list(instType="SWAP")
            if str(response.get("code") or "0") != "0":
                return []
            return list(response.get("data") or [])
        except Exception:
            logger.exception("OKX open orders failed")
            return []

    def get_open_triggers(self) -> list[dict[str, Any]]:
        if not self.trade:
            return []
        rows: list[dict[str, Any]] = []
        try:
            for ord_type in ("conditional", "oco"):
                response = self.trade.order_algos_list(ordType=ord_type)
                if str(response.get("code") or "0") != "0":
                    continue
                rows.extend(list(response.get("data") or []))
            return rows
        except Exception:
            logger.exception("OKX open trigger orders failed")
            return []

    def close_position(self, inst_id: str, close_pct: float = 100.0) -> dict[str, Any]:
        target = next((row for row in self.get_positions() if row.get("instId") == inst_id), None)
        if not target:
            return {"ok": False, "skipped": True, "error": "交易所当前无对应持仓"}
        pos = _float(target.get("pos"))
        close_size = _round_size_down(
            abs(pos) * min(max(close_pct, 0), 100) / 100,
            self.get_instrument(inst_id),
        )
        if close_size <= 0:
            return {"ok": False, "skipped": True, "error": "平仓数量小于交易所最小下单单位"}
        side = "sell" if pos > 0 else "buy"
        response = self.place_order(
            instId=inst_id,
            tdMode="cross",
            side=side,
            ordType="market",
            sz=str(close_size),
            reduceOnly="true",
        )
        ok = str(response.get("code") or "-1") == "0"
        return {
            "ok": ok,
            "response": response,
            "closed_size": close_size,
            "close_pct": close_pct,
        }

    def cancel_open_orders(self, inst_id: str) -> dict[str, Any]:
        orders = [row for row in self.get_open_orders() if row.get("instId") == inst_id]
        responses = [
            self.trade.cancel_order(instId=inst_id, ordId=str(row.get("ordId") or ""))
            for row in orders
            if row.get("ordId")
        ]
        ok = bool(orders) and all(str(row.get("code") or "-1") == "0" for row in responses)
        return {
            "ok": ok,
            "skipped": not orders,
            "error": "交易所当前无对应挂单" if not orders else None,
            "canceled_count": len(responses),
            "responses": responses,
        }

    def update_stop_loss(self, inst_id: str, trigger_px: float) -> dict[str, Any]:
        return self._replace_exit_trigger(inst_id, trigger_px, "sl")

    def update_take_profit(self, inst_id: str, trigger_px: float) -> dict[str, Any]:
        return self._replace_exit_trigger(inst_id, trigger_px, "tp")

    def _replace_exit_trigger(self, inst_id: str, trigger_px: float, kind: str) -> dict[str, Any]:
        position = next((row for row in self.get_positions() if row.get("instId") == inst_id), None)
        if not position:
            return {"ok": False, "skipped": True, "error": "交易所当前无对应持仓"}
        pos = _float(position.get("pos"))
        if pos == 0:
            return {"ok": False, "skipped": True, "error": "交易所当前无对应持仓"}
        existing = [
            row
            for row in self.get_open_triggers()
            if row.get("instId") == inst_id and row.get(f"{kind}TriggerPx")
        ]
        params = {
            "instId": inst_id,
            "tdMode": "cross",
            "side": "sell" if pos > 0 else "buy",
            "ordType": "conditional",
            "sz": str(abs(pos)),
            f"{kind}TriggerPx": str(trigger_px),
            f"{kind}OrdPx": "-1",
            "reduceOnly": "true",
        }
        response = self.place_algo_order(**params)
        ok = str(response.get("code") or "-1") == "0"
        canceled = []
        if ok:
            for row in existing:
                algo_id = str(row.get("algoId") or row.get("ordId") or "")
                if algo_id and self.trade:
                    canceled.append(
                        self.trade.cancel_algo_order([{"instId": inst_id, "algoId": algo_id}])
                    )
        return {
            "ok": ok,
            "response": response,
            "canceled_count": len(canceled),
            "trigger_px": trigger_px,
            "kind": kind,
        }

    def get_instrument(self, inst_id: str) -> dict[str, Any]:
        if not self.public:
            return {}
        try:
            data = self.public.get_instruments(instType="SWAP", instId=inst_id).get("data") or []
            return data[0] if data else {}
        except Exception:
            return {}

    def get_ticker(self, inst_id: str) -> float:
        if not self.market:
            return 0.0
        try:
            data = self.market.get_ticker(instId=inst_id).get("data") or []
            return _float(data[0].get("last")) if data else 0.0
        except Exception:
            return 0.0

    def set_leverage(self, inst_id: str, leverage: int, td_mode: str = "cross") -> dict[str, Any]:
        if not self.account:
            return {"code": "-1", "msg": "OKX client unavailable"}
        return self.account.set_leverage(instId=inst_id, lever=str(leverage), mgnMode=td_mode)

    def place_order(self, **params: Any) -> dict[str, Any]:
        if not self.trade:
            return {"code": "-1", "msg": "OKX client unavailable"}
        return self.trade.place_order(**params)

    def place_algo_order(self, **params: Any) -> dict[str, Any]:
        if not self.trade:
            return {"code": "-1", "msg": "OKX client unavailable"}
        return self.trade.place_algo_order(**params)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _round_size_down(size: float, instrument: dict[str, Any]) -> float:
    try:
        lot = Decimal(str(instrument.get("lotSz") or "1"))
        minimum = Decimal(str(instrument.get("minSz") or lot))
        raw = Decimal(str(size))
        if raw <= 0 or lot <= 0:
            return 0.0
        rounded = (raw // lot) * lot
        if rounded < minimum:
            return 0.0
        if rounded == rounded.to_integral_value():
            return int(rounded)
        return float(rounded)
    except (InvalidOperation, TypeError, ValueError):
        return math.floor(size)


def _empty_balance() -> dict[str, float]:
    return {"total_usdt": 0.0, "available_usdt": 0.0, "unrealized_pnl": 0.0}


def _balance_summary(response: dict[str, Any]) -> dict[str, float]:
    out = _empty_balance()
    data = response.get("data") or []
    if not data:
        return out
    row = data[0]
    details = row.get("details") or []
    usdt = next((item for item in details if item.get("ccy") == "USDT"), {})
    out["total_usdt"] = _float(row.get("totalEq") or usdt.get("eq"))
    out["available_usdt"] = _float(usdt.get("availBal") or usdt.get("availEq"))
    out["unrealized_pnl"] = _float(usdt.get("upl"))
    return out

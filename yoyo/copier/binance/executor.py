from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.binance.client import BinanceClient, binance_position_side, binance_symbol
from yoyo.copier.exchange import selected_leverage
from yoyo.copier.price_normalizer import normalize_decimal_shorthand_to_mark
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.store.sqlite import Database

BINANCE_CHANNEL_EXCHANGE_LEVERAGE_MAP_KEY = "binance_channel_exchange_leverage_map"


class BinanceExecutor:
    def __init__(self, db: Database, risk: RiskEngine, client: BinanceClient | None = None) -> None:
        self.db = db
        self.risk = risk
        self.client = client or BinanceClient()

    def open_with_sl_tp(self, result: IntentResult, message_id: int, signal_id: int) -> dict[str, Any]:
        inst_id = result.inst_id()
        if not inst_id:
            return {"ok": False, "error": "no inst_id"}
        from yoyo.copier.runtime_config import get_okx

        cfg = get_okx(self.db)
        message = self.db.get_message(message_id) or {}
        channel_id = str(message.get("channel_id") or "")
        notional_leverage = min(
            selected_leverage(self.db, str(message.get("channel_id") or ""), fallback=cfg["default_leverage"]),
            self.risk.get_max_leverage(),
        )
        exchange_leverage = _selected_exchange_leverage(
            self.db,
            self.client,
            channel_id,
            inst_id,
            notional_leverage,
        )
        if not self.risk.is_dry_run():
            health = self.client.health_check()
            if not health.get("ok") and health.get("ip_restricted"):
                return {
                    "ok": False,
                    "skipped": True,
                    "error": str(health.get("error") or "Binance IP 白名单拒绝"),
                    "response": health,
                }
        mark = self.client.get_ticker(inst_id)
        price_scale_adjusted = normalize_decimal_shorthand_to_mark(
            result,
            mark,
            max_deviation=self.risk.get_max_entry_deviation_pct(),
        )
        is_market = "market" in str(result.entry_note or "").lower()
        px = mark if is_market else _entry_price(result, mark)
        instrument = self.client.get_instrument(inst_id)
        balance_summary = self.client.get_balance_summary()
        total_balance = float(balance_summary.get("total_usdt") or 0)
        available_balance = float(balance_summary.get("available_usdt") or 0)
        notional = total_balance * self.risk.get_position_pct() * notional_leverage
        margin = notional / max(exchange_leverage, 1)
        quantity = _quantity(instrument, notional, px)
        side = "BUY" if result.side == "long" else "SELL"
        position_side = binance_position_side(result.side)
        plan = {
            "exchange": "binance",
            "inst_id": inst_id,
            "side": side.lower(),
            "px": px,
            "sz": quantity,
            "sl": result.stop_loss,
            "tp": result.take_profit,
            "leverage": exchange_leverage,
            "notional_leverage": notional_leverage,
            "margin_usdt": margin,
            "position_pct": self.risk.get_position_pct(),
            "notional_usdt": notional,
            "td_mode": "cross",
            "position_side": position_side,
            "balance": total_balance,
            "available_balance": available_balance,
            "entry_note": result.entry_note,
            "price_scale_adjusted": price_scale_adjusted,
        }
        if self.risk.is_dry_run():
            self._save(message_id, signal_id, plan, "dry_run", None, json.dumps(plan, ensure_ascii=False))
            return {"ok": True, "dry_run": True, "plan": plan}

        self.client.set_leverage(inst_id, exchange_leverage, "cross")
        params = {
            "symbol": binance_symbol(inst_id),
            "side": side,
            "positionSide": position_side,
            "type": "MARKET" if is_market else "LIMIT",
            "quantity": quantity,
        }
        if not is_market:
            params.update({"price": _fmt(px), "timeInForce": "GTC"})
        response = self.client.place_order(params)
        ok = isinstance(response, dict) and response.get("orderId") is not None
        if ok and result.stop_loss:
            self.client.place_algo_order(
                {
                    "algoType": "CONDITIONAL",
                    "symbol": binance_symbol(inst_id),
                    "side": "SELL" if side == "BUY" else "BUY",
                    "positionSide": position_side,
                    "type": "STOP_MARKET",
                    "triggerPrice": _fmt(result.stop_loss),
                    "quantity": quantity,
                    "workingType": "MARK_PRICE",
                }
            )
        if ok and result.take_profit:
            self.client.place_algo_order(
                {
                    "algoType": "CONDITIONAL",
                    "symbol": binance_symbol(inst_id),
                    "side": "SELL" if side == "BUY" else "BUY",
                    "positionSide": position_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "triggerPrice": _fmt(result.take_profit),
                    "quantity": quantity,
                    "workingType": "MARK_PRICE",
                }
            )
        self._save(
            message_id,
            signal_id,
            plan,
            "live" if ok else "error",
            response.get("orderId"),
            json.dumps({"plan": plan, "response": response}, ensure_ascii=False),
        )
        return {"ok": ok, "response": response, "plan": plan}

    def _save(self, message_id: int, signal_id: int, plan: dict[str, Any], status: str, order_id: Any, response: str) -> None:
        self.db.insert_order(
            message_id=message_id,
            signal_id=signal_id,
            inst_id=plan["inst_id"],
            side=plan["side"],
            ord_type="market" if "market" in str(plan.get("entry_note") or "") else "limit",
            sz=str(plan["sz"]),
            px=str(plan["px"]),
            okx_ord_id=str(order_id) if order_id else None,
            sl_trigger=str(plan["sl"]) if plan["sl"] else None,
            tp_trigger=str(plan["tp"]) if plan["tp"] else None,
            status=status,
            response_json=response,
        )


def _entry_price(result: IntentResult, mark: float) -> float:
    if result.entry_low is not None and result.entry_high is not None:
        return (result.entry_low + result.entry_high) / 2
    return float(result.entry_low or result.entry_high or mark)


def _quantity(instrument: dict[str, Any], notional: float, px: float) -> str:
    try:
        raw = Decimal(str(notional)) / Decimal(str(px))
        filters = instrument.get("filters") or []
        lot = next((row for row in filters if row.get("filterType") == "LOT_SIZE"), {})
        step = Decimal(str(lot.get("stepSize") or "0.001"))
        minimum = Decimal(str(lot.get("minQty") or step))
        value = max((raw // step) * step, minimum)
        return _fmt(float(value))
    except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return "0.001"


def _fmt(value: float) -> str:
    return f"{value:.12f}".rstrip("0").rstrip(".")


def _selected_exchange_leverage(
    db: Database,
    client: BinanceClient,
    channel_id: str,
    inst_id: str,
    fallback: int,
) -> int:
    raw = db.get_setting(BINANCE_CHANNEL_EXCHANGE_LEVERAGE_MAP_KEY)
    if raw:
        try:
            data = json.loads(raw)
            configured = data.get(str(channel_id)) if isinstance(data, dict) else None
            if str(configured or "").strip().lower() in {"max", "auto", "highest"}:
                return client.get_max_leverage(inst_id, fallback=125)
            configured_int = int(configured or 0)
            if configured_int > 0:
                return min(max(configured_int, 1), 125)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return client.get_max_leverage(inst_id, fallback=125)

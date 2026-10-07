from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.exchange import selected_leverage, should_use_market_entry
from yoyo.copier.okx.client import OkxClient
from yoyo.copier.price_normalizer import normalize_decimal_shorthand_to_mark
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.store.sqlite import Database


OKX_MARGIN_BUFFER_PCT = 0.98
OKX_CHANNEL_NOTIONAL_LEVERAGE_MAP_KEY = "okx_channel_notional_leverage_map"


class OkxExecutor:
    def __init__(self, db: Database, risk: RiskEngine, client: OkxClient | None = None) -> None:
        self.db = db
        self.risk = risk
        self.client = client or OkxClient()

    def open_with_sl_tp(self, result: IntentResult, message_id: int, signal_id: int) -> dict[str, Any]:
        inst_id = result.inst_id()
        if not inst_id:
            return {"ok": False, "error": "no inst_id"}

        from yoyo.copier.runtime_config import get_okx

        cfg = get_okx(self.db)
        message = self.db.get_message(message_id) or {}
        channel_id = str(message.get("channel_id") or "")
        leverage = min(
            selected_leverage(
                self.db,
                channel_id,
                fallback=result.leverage_hint or cfg["default_leverage"],
            ),
            self.risk.get_max_leverage(),
        )
        td_mode = str(cfg.get("td_mode") or "cross")
        mark = self.client.get_ticker(inst_id)
        price_scale_adjusted = normalize_decimal_shorthand_to_mark(
            result,
            mark,
            max_deviation=self.risk.get_max_entry_deviation_pct(),
        )
        is_market = should_use_market_entry(result, mark, str(message.get("content") or ""))
        px = mark if is_market else _entry_price(result, mark, str(cfg.get("entry_policy") or "mid"))
        deviation_error = None if is_market else _entry_deviation_error(px, mark, self.risk.get_max_entry_deviation_pct())
        if deviation_error:
            return {"ok": False, "skipped": True, "error": deviation_error}
        instrument = self.client.get_instrument(inst_id)
        balance = self.client.get_balance_usdt()
        position_pct = self.risk.get_position_pct()
        notional_leverage = min(_selected_notional_leverage(self.db, channel_id, leverage), leverage)
        notional = _usable_notional(balance, position_pct, notional_leverage)
        margin = notional / max(leverage, 1)
        sz = _contracts(instrument, notional, px)
        side = "buy" if result.side == "long" else "sell"

        plan = {
            "exchange": "okx",
            "inst_id": inst_id,
            "side": side,
            "px": px,
            "sz": str(sz),
            "sl": result.stop_loss,
            "tp": result.take_profit,
            "leverage": leverage,
            "notional_leverage": notional_leverage,
            "margin_usdt": margin,
            "position_pct": position_pct,
            "notional_usdt": notional,
            "td_mode": td_mode,
            "balance": balance,
            "margin_buffer_pct": OKX_MARGIN_BUFFER_PCT,
            "ord_type": "market" if is_market else "limit",
            "entry_note": result.entry_note,
            "price_scale_adjusted": price_scale_adjusted,
        }
        if self.risk.is_dry_run():
            self._save(message_id, signal_id, plan, "dry_run", None, json.dumps(plan, ensure_ascii=False))
            self.db.audit("dry_run_order", json.dumps(plan), message_id=message_id)
            return {"ok": True, "dry_run": True, "plan": plan}

        self.client.set_leverage(inst_id, leverage, td_mode)
        order_params = dict(
            instId=inst_id,
            tdMode=td_mode,
            side=side,
            ordType="market" if is_market else "limit",
            sz=str(sz),
        )
        if not is_market:
            order_params["px"] = str(px)
        response = self.client.place_order(**order_params)
        ok = str(response.get("code", "-1")) == "0"
        rows = response.get("data") or []
        ord_id = str(rows[0].get("ordId")) if ok and rows else None
        if ok and result.stop_loss and result.take_profit:
            self.client.place_algo_order(
                instId=inst_id,
                tdMode=td_mode,
                side="sell" if side == "buy" else "buy",
                ordType="oco",
                sz=str(sz),
                tpTriggerPx=str(result.take_profit),
                tpOrdPx="-1",
                slTriggerPx=str(result.stop_loss),
                slOrdPx="-1",
                reduceOnly="true",
            )
        self._save(
            message_id,
            signal_id,
            plan,
            "live" if ok else "error",
            ord_id,
            json.dumps({"plan": plan, "response": response}, ensure_ascii=False),
        )
        return {"ok": ok, "response": response, "plan": plan}

    def _save(
        self,
        message_id: int,
        signal_id: int,
        plan: dict[str, Any],
        status: str,
        order_id: str | None,
        response: str,
    ) -> None:
        self.db.insert_order(
            message_id=message_id,
            signal_id=signal_id,
            inst_id=plan["inst_id"],
            side=plan["side"],
            ord_type=str(plan.get("ord_type") or "limit"),
            sz=plan["sz"],
            px=str(plan["px"]),
            okx_ord_id=order_id,
            sl_trigger=str(plan["sl"]) if plan["sl"] else None,
            tp_trigger=str(plan["tp"]) if plan["tp"] else None,
            status=status,
            response_json=response,
        )


def _entry_price(result: IntentResult, mark: float, policy: str) -> float:
    if result.entry_low is not None and result.entry_high is not None:
        if policy == "low":
            return min(result.entry_low, result.entry_high)
        if policy == "high":
            return max(result.entry_low, result.entry_high)
        return (result.entry_low + result.entry_high) / 2
    return float(result.entry_low or result.entry_high or mark)


def _entry_deviation_error(px: float, mark: float, maximum: float) -> str | None:
    if px <= 0 or mark <= 0 or maximum <= 0:
        return None
    deviation = abs(px - mark) / mark
    if deviation > maximum:
        return f"挂单价与市价偏离 {deviation:.1%}，超过风控上限 {maximum:.0%}"
    return None


def _usable_notional(balance: float, position_pct: float, notional_leverage: int) -> float:
    """OKX needs a small available-balance buffer for fees and frozen trigger orders."""
    return max(0.0, float(balance) * float(position_pct) * OKX_MARGIN_BUFFER_PCT * max(notional_leverage, 1))


def _selected_notional_leverage(db: Database, channel_id: str, fallback: int) -> int:
    raw = db.get_setting(OKX_CHANNEL_NOTIONAL_LEVERAGE_MAP_KEY)
    if raw:
        try:
            data = json.loads(raw)
            configured = int(data.get(str(channel_id)) or 0) if isinstance(data, dict) else 0
            if configured > 0:
                return configured
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return max(1, int(fallback or 1))


def _contracts(instrument: dict[str, Any], notional: float, px: float) -> int:
    try:
        ct_val = Decimal(str(instrument.get("ctVal") or "1"))
        price = Decimal(str(px))
        raw = Decimal(str(notional)) / (ct_val * price)
        lot = Decimal(str(instrument.get("lotSz") or "1"))
        size = math.floor(float(raw / lot)) * float(lot)
        minimum = float(instrument.get("minSz") or lot)
        return max(1, int(max(size, minimum)))
    except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return 1

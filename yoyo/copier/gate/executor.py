from __future__ import annotations

import json
import logging
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.config import app_config
from yoyo.copier.exchange import selected_leverage, should_use_market_entry
from yoyo.copier.gate.client import GateClient, gate_contract
from yoyo.copier.price_normalizer import normalize_decimal_shorthand_to_mark
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.store.sqlite import Database

logger = logging.getLogger(__name__)

GATE_CHANNEL_EXCHANGE_LEVERAGE_MAP_KEY = "gate_channel_exchange_leverage_map"


class GateExecutor:
    def __init__(self, db: Database, risk: RiskEngine, client: GateClient | None = None) -> None:
        self.db = db
        self.risk = risk
        self.client = client or GateClient()

    def _entry_price(self, result: IntentResult, mark: float) -> float:
        from yoyo.copier.runtime_config import get_okx

        policy = get_okx(self.db)["entry_policy"]
        low = result.entry_low
        high = result.entry_high
        if low is not None and high is not None:
            if policy == "low":
                return min(low, high)
            if policy == "high":
                return max(low, high)
            return (low + high) / 2
        if low is not None:
            return low
        if high is not None:
            return high
        return mark

    def _calc_size(self, contract_info: dict[str, Any], notional_usdt: float, px: float) -> int:
        notional = Decimal(str(max(notional_usdt, 0)))
        price = _decimal(px, "0")
        multiplier = _decimal(contract_info.get("quanto_multiplier"), "1")
        if multiplier <= 0:
            multiplier = Decimal("1")
        if price <= 0:
            return int(contract_info.get("order_size_min") or 1)

        raw_contracts = notional / (price * multiplier)
        min_size = int(contract_info.get("order_size_min") or 1)
        max_size = int(contract_info.get("order_size_max") or 0)
        size = int(math.floor(float(raw_contracts)))
        size = max(size, min_size)
        if max_size > 0:
            size = min(size, max_size)
        return max(size, 1)

    def _round_price(self, contract_info: dict[str, Any], px: float) -> str:
        price = _decimal(px, "0")
        step = _decimal(contract_info.get("order_price_round"), "0")
        if price <= 0:
            return "0"
        if step <= 0:
            return _fmt_decimal(price)
        rounded = (price / step).to_integral_value(rounding=ROUND_HALF_UP) * step
        return _fmt_decimal(rounded)

    def _trigger_order(
        self,
        *,
        contract: str,
        close_size: int,
        trigger_px: float,
        rule: int,
    ) -> dict[str, Any]:
        body = {
            "initial": {
                "contract": contract,
                "size": close_size,
                "price": "0",
                "close": False,
                "tif": "ioc",
                "text": "api",
                "reduce_only": True,
            },
            "trigger": {
                "strategy_type": 0,
                "price_type": 0,
                "price": _fmt_decimal(_decimal(trigger_px, "0")),
                "rule": rule,
            },
        }
        return self.client.place_price_order(body)

    def open_with_sl_tp(
        self,
        result: IntentResult,
        message_id: int,
        signal_id: int,
    ) -> dict[str, Any]:
        inst_id = result.inst_id()
        if not inst_id:
            return {"ok": False, "error": "no inst_id"}

        contract = gate_contract(inst_id, self.client.settle)
        inst = self.client.get_instrument(inst_id)
        mark = self.client.get_ticker(inst_id) or self._entry_price(result, 0)
        price_scale_adjusted = normalize_decimal_shorthand_to_mark(
            result,
            mark,
            max_deviation=self.risk.get_max_entry_deviation_pct(),
        )
        message = self.db.get_message(message_id) or {}
        is_market = should_use_market_entry(result, mark, str(message.get("content") or ""))
        px = mark if is_market else self._entry_price(result, mark)
        deviation_error = None if is_market else _entry_deviation_error(px, mark, self.risk.get_max_entry_deviation_pct())
        if deviation_error:
            return {"ok": False, "skipped": True, "error": deviation_error}

        from yoyo.copier.runtime_config import get_okx

        cfg = get_okx(self.db)
        channel_id = str(message.get("channel_id") or "")
        notional_leverage = min(
            selected_leverage(
                self.db,
                channel_id,
                fallback=result.leverage_hint or cfg["default_leverage"],
            ),
            self.risk.get_max_leverage(),
        )
        exchange_leverage = _selected_exchange_leverage(
            self.db,
            channel_id,
            inst,
            notional_leverage,
        )
        mgn_mode = cfg.get("td_mode", app_config.okx.td_mode)
        balance_summary = self.client.get_balance_summary()
        total_balance = float(balance_summary.get("total_usdt") or 0)
        available_balance = float(balance_summary.get("available_usdt") or 0)
        position_pct = self.risk.get_position_pct()
        notional = total_balance * position_pct * notional_leverage
        margin = notional / max(exchange_leverage, 1)
        size_abs = self._calc_size(inst, notional, px) if inst else 1
        signed_size = -size_abs if result.side == "short" else size_abs
        side = "sell" if result.side == "short" else "buy"
        pos_side = "short" if result.side == "short" else "long"
        order_px = self._round_price(inst, px) if inst else str(px)

        order_params = {
            "contract": contract,
            "size": signed_size,
            "iceberg": 0,
            "price": "0" if is_market else order_px,
            "tif": "ioc" if is_market else "gtc",
            "text": _client_text(message_id),
        }

        sl = result.stop_loss
        tp = result.take_profit
        close_size = -signed_size

        plan = {
            "exchange": "gate",
            "inst_id": inst_id,
            "contract": contract,
            "side": side,
            "pos_side": pos_side,
            "px": float(order_px) if _is_number(order_px) else px,
            "sz": str(size_abs),
            "gate_size": signed_size,
            "sl": sl,
            "tp": tp,
            "leverage": exchange_leverage,
            "notional_leverage": notional_leverage,
            "margin_usdt": margin,
            "position_pct": position_pct,
            "notional_usdt": notional,
            "td_mode": mgn_mode,
            "gate_position_leverage": str(exchange_leverage),
            "balance": total_balance,
            "available_balance": available_balance,
            "ord_type": "market" if is_market else "limit",
            "entry_note": result.entry_note,
            "price_scale_adjusted": price_scale_adjusted,
        }

        if self.risk.is_dry_run():
            self.db.insert_order(
                message_id=message_id,
                signal_id=signal_id,
                inst_id=inst_id,
                side=side,
                ord_type=str(plan.get("ord_type") or "limit"),
                sz=str(size_abs),
                px=order_px,
                okx_ord_id=None,
                sl_trigger=str(sl) if sl else None,
                tp_trigger=str(tp) if tp else None,
                status="dry_run",
                response_json=json.dumps(plan, ensure_ascii=False),
            )
            self.db.audit("dry_run_order", json.dumps(plan), message_id=message_id)
            return {"ok": True, "dry_run": True, "plan": plan}

        leverage_resp = self.client.set_leverage(inst_id, exchange_leverage, mgn_mode)
        order_resp = self.client.place_order(order_params)
        ok = _gate_order_ok(order_resp)
        ord_id = str(order_resp.get("id")) if ok else None
        trigger_responses: dict[str, Any] = {}

        if ok and tp:
            tp_rule = 2 if result.side == "short" else 1
            trigger_responses["tp"] = self._trigger_order(
                contract=contract,
                close_size=close_size,
                trigger_px=tp,
                rule=tp_rule,
            )
        if ok and sl:
            sl_rule = 1 if result.side == "short" else 2
            trigger_responses["sl"] = self._trigger_order(
                contract=contract,
                close_size=close_size,
                trigger_px=sl,
                rule=sl_rule,
            )

        response = {
            "code": "0" if ok else "-1",
            "data": [{"ordId": ord_id}] if ord_id else [],
            "gate": {
                "leverage": leverage_resp,
                "order": order_resp,
                "triggers": trigger_responses,
            },
        }
        if not ok:
            response["msg"] = order_resp.get("message") or order_resp.get("label") or "Gate order failed"

        self.db.insert_order(
            message_id=message_id,
            signal_id=signal_id,
            inst_id=inst_id,
            side=side,
            ord_type=str(plan.get("ord_type") or "limit"),
            sz=str(size_abs),
            px=order_px,
            okx_ord_id=ord_id,
            sl_trigger=str(sl) if sl else None,
            tp_trigger=str(tp) if tp else None,
            status="live" if ok else "error",
            response_json=json.dumps({"plan": plan, "response": response}, ensure_ascii=False),
        )
        return {"ok": ok, "response": response, "plan": plan}


def _client_text(message_id: int) -> str:
    suffix = str(message_id)[-12:]
    return f"t-cp{suffix}"


def _decimal(value: Any, fallback: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(fallback)


def _fmt_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _gate_order_ok(resp: dict[str, Any]) -> bool:
    return isinstance(resp, dict) and not resp.get("label") and resp.get("id") is not None


def _entry_deviation_error(px: float, mark: float, maximum: float) -> str | None:
    if px <= 0 or mark <= 0 or maximum <= 0:
        return None
    deviation = abs(px - mark) / mark
    if deviation > maximum:
        return f"挂单价与市价偏离 {deviation:.1%}，超过风控上限 {maximum:.0%}"
    return None


def _selected_exchange_leverage(
    db: Database,
    channel_id: str,
    instrument: dict[str, Any],
    fallback: int,
) -> int:
    raw = db.get_setting(GATE_CHANNEL_EXCHANGE_LEVERAGE_MAP_KEY)
    if raw:
        try:
            data = json.loads(raw)
            configured = data.get(str(channel_id)) if isinstance(data, dict) else None
            if str(configured or "").strip().lower() in {"max", "auto", "highest"}:
                return _instrument_max_leverage(instrument, fallback)
            configured_int = int(configured or 0)
            if configured_int > 0:
                return min(max(configured_int, 1), 200)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return _instrument_max_leverage(instrument, fallback)


def _instrument_max_leverage(instrument: dict[str, Any], fallback: int) -> int:
    try:
        configured = int(float(instrument.get("leverage_max") or 0))
        if configured > 0:
            return min(max(configured, 1), 200)
    except (TypeError, ValueError):
        pass
    return min(max(int(fallback or 1), 1), 200)

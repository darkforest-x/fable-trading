"""Positions / pending / history rows shown on the workbench and the Telegram snapshot.

Ported from the retired React admin (``admin/web/src/pages/Dashboard.tsx``) so the
derivation lives in one tested place instead of being repeated in each frontend.
Two deliberate changes from the React version:

* Active channels come from the routing map (``exchange_channel_map``) instead of a
  hard-coded list, so a newly routed channel shows up without a frontend edit.
* Channel display names stay a fixed table; unknown channels show their id.

Pure functions over rows already read from SQLite: nothing here calls an exchange.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

CHANNEL_NAMES: dict[str, str] = {
    "1226095564073205780": "Woods",
    "1131521990814089276": "Arthur行情分析",
    "1356581750914027590": "比特币飞扬",
    "988830102957736027": "ChartPrime",
    "1226097931904352378": "Tareeq",
}

MAX_CHANNEL_CARDS = 24  # paper mode routes every watched channel
MAX_PENDING = 3
MAX_HISTORY = 6


def channel_name(channel_id: Any, fallback: Any = "") -> str:
    key = str(channel_id or "")
    return CHANNEL_NAMES.get(key) or str(fallback or "") or key or "-"


def _payload(order: Mapping[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(str(order.get("response_json") or "{}"))
    except (TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _record(value: Any, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    nested = value.get(key)
    return nested if isinstance(nested, dict) else {}


def _plan_amount(order: Mapping[str, Any], key: str) -> float | None:
    plan = _record(_payload(order), "plan")
    try:
        value = float(plan.get(key))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def is_pending_order(order: Mapping[str, Any]) -> bool:
    """A live order that the exchange still reports as resting on the book."""
    if str(order.get("status") or "") != "live":
        return False
    response = _record(_payload(order), "response")
    gate_order = _record(_record(response, "gate"), "order")
    raw_status = str(gate_order.get("status") or response.get("status") or "").lower()
    if raw_status in {"open", "new"}:
        return True
    left_raw = gate_order.get("left")
    if left_raw is None:
        left_raw = response.get("origQty")
    try:
        left = float(left_raw or 0)
    except (TypeError, ValueError):
        left = 0.0
    return left > 0 and raw_status != "finished"


def order_row(order: Mapping[str, Any]) -> dict[str, Any]:
    pending = is_pending_order(order)
    return {
        "id": order.get("id"),
        "message_id": order.get("message_id"),
        "channel_id": str(order.get("channel_id") or ""),
        "trader": channel_name(order.get("channel_id"), order.get("author")),
        "inst_id": order.get("inst_id"),
        "side": order.get("side"),
        "ord_type": order.get("ord_type"),
        "px": order.get("px"),
        "sl_trigger": order.get("sl_trigger"),
        "tp_trigger": order.get("tp_trigger"),
        "status": order.get("status"),
        "pending": pending,
        # A "live" row that is no longer resting is only the local record of a fill.
        "local_record": str(order.get("status") or "") == "live" and not pending,
        "paper": _record(_payload(order), "plan").get("exchange") == "paper",
        "notional_usdt": _plan_amount(order, "notional_usdt"),
        "margin_usdt": _plan_amount(order, "margin_usdt"),
        "created_at": order.get("created_at"),
    }


def build_orders_card(
    channel_cards: Iterable[Mapping[str, Any]],
    orders: Iterable[Mapping[str, Any]],
    routed_channels: Iterable[str],
    enabled_channels: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    routed = [str(c) for c in routed_channels]
    order_index = {c: i for i, c in enumerate(routed)}
    enabled = {str(k): bool(v) for k, v in (enabled_channels or {}).items()}
    # A routed channel the owner switched off keeps its route (for restore) but is not shown.
    cards = [dict(c) for c in channel_cards
             if str(c.get("channel_id")) in order_index and enabled.get(str(c.get("channel_id")), True)]
    cards.sort(key=lambda c: (-float(c.get("account_total_usdt") or 0),
                              order_index.get(str(c.get("channel_id")), 99)))
    cards = cards[:MAX_CHANNEL_CARDS]
    active = {str(c.get("channel_id")) for c in cards}
    order_list = [dict(o) for o in orders]

    channels = [{
        "channel_id": str(c.get("channel_id")),
        "name": channel_name(c.get("channel_id"), c.get("channel_name")),
        "exchange": c.get("exchange"),
        "leverage": c.get("leverage"),
        "account_ok": bool(c.get("account_ok")),
        "account_total_usdt": float(c.get("account_total_usdt") or 0),
        "account_unrealized_pnl": float(c.get("account_unrealized_pnl") or 0),
        "account_positions": int(c.get("account_positions") or 0),
        "open_signals": int(c.get("open_signals") or 0),
        "executed": int(c.get("executed") or 0),
        "last_message_at": c.get("last_message_at"),
        "monitor_enabled": enabled.get(str(c.get("channel_id")), True),
    } for c in cards]
    has_position = {c["channel_id"]: c["account_positions"] > 0 for c in channels}

    positions = []
    for channel in channels:
        if not has_position[channel["channel_id"]]:
            continue
        reference = next((o for o in order_list
                          if str(o.get("channel_id") or "") == channel["channel_id"]
                          and str(o.get("status") or "") == "live" and not is_pending_order(o)), None)
        ref = order_row(reference) if reference else None
        positions.append({
            "channel_id": channel["channel_id"],
            "trader": channel["name"],
            "inst_id": ref["inst_id"] if ref else None,
            "side": ref["side"] if ref else None,
            # Without a reference order the account balance is the only size we know.
            "notional_usdt": ref["notional_usdt"] if ref else channel["account_total_usdt"],
            "margin_usdt": ref["margin_usdt"] if ref else None,
            "unrealized_pnl": channel["account_unrealized_pnl"],
            "sl_trigger": ref["sl_trigger"] if ref else None,
            "tp_trigger": ref["tp_trigger"] if ref else None,
        })

    in_scope = [o for o in order_list if str(o.get("channel_id") or "") in active]
    pending = [order_row(o) for o in in_scope if is_pending_order(o)][:MAX_PENDING]
    history = []
    for o in in_scope:
        if str(o.get("status") or "") == "live":
            if is_pending_order(o) or has_position.get(str(o.get("channel_id") or "")):
                continue
        history.append(order_row(o))
        if len(history) >= MAX_HISTORY:
            break

    return {
        "channels": channels,
        "position_count": sum(c["account_positions"] for c in channels),
        "positions": positions,
        "pending": pending,
        "history": history,
    }

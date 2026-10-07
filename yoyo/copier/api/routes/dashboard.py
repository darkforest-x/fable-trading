from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends

from yoyo.copier.api.auth import verify_token
from yoyo.copier.api.deps import get_db, get_okx
from yoyo.copier.discord_channels import channel_names, trade_blocked_channels
from yoyo.copier.exchange import (
    PAPER,
    exchange_label,
    exchange_mode_label,
    get_channel_exchange_map,
    get_channel_leverage_map,
    selected_exchange,
    selected_leverage,
)
from yoyo.copier.monitor_switches import get_switches
from yoyo.copier.okx_cache import get_balance_cached, get_positions_cached
from yoyo.copier.paper.book import PaperBook
from yoyo.copier.orders_card import CHANNEL_NAMES, build_orders_card
from yoyo.copier.runtime_config import get_full_config
from yoyo.copier.store.sqlite import Database

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _load_exchange_snapshot(db: Database, exchange_name: str) -> dict[str, Any]:
    raw = db.get_setting(f"health_exchange_state_{exchange_name}")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                balance = data.get("balance") if isinstance(data.get("balance"), dict) else {}
                return {
                    "ok": bool(data.get("ok")),
                    "balance": balance,
                    "positions": int(data.get("positions") or 0),
                }
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Fallback to the lighter Telegram panel cache when health monitor has not run yet.
    raw = db.get_setting(f"telegram_panel_exchange_snapshot_{exchange_name}")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("balance"), dict):
                return {"ok": True, "balance": data["balance"], "positions": 0}
        except (json.JSONDecodeError, TypeError):
            pass
    return {"ok": False, "balance": {}, "positions": 0}


def _channel_cards(db: Database) -> list[dict[str, Any]]:
    exchange_map = get_channel_exchange_map(db)
    leverage_map = get_channel_leverage_map(db)
    with db._connect() as conn:
        rows = conn.execute(
            """SELECT m.channel_id, COUNT(DISTINCT m.id) messages,
            SUM(CASE WHEN s.intent='open' THEN 1 ELSE 0 END) open_signals,
            SUM(CASE WHEN m.status='executed' THEN 1 ELSE 0 END) executed,
            MAX(m.created_at) last_message_at
            FROM messages m LEFT JOIN signals s ON s.message_id=m.id GROUP BY m.channel_id"""
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            latest = conn.execute(
                "SELECT author,content FROM messages WHERE channel_id=? ORDER BY id DESC LIMIT 1",
                (row["channel_id"],),
            ).fetchone()
            channel_id = str(row["channel_id"])
            exchange_name = exchange_map.get(channel_id, selected_exchange(db, channel_id))
            if exchange_name == PAPER:
                # Each paper-routed channel has its own local account; read it directly.
                paper = PaperBook(db).balance(channel_id)
                snapshot = {"ok": True, "balance": paper, "positions": paper["positions"]}
            else:
                snapshot = _load_exchange_snapshot(db, exchange_name)
            balance = snapshot.get("balance") or {}
            result.append(
                {
                    **dict(row),
                    "channel_name": channel_id,
                    "exchange": exchange_label(exchange=exchange_name),
                    "exchange_mode": exchange_mode_label(exchange=exchange_name),
                    "mapped_exchange": exchange_name,
                    # Unmapped channels trade at the default leverage, not 1x.
                    "leverage": leverage_map.get(channel_id) or selected_leverage(db, channel_id),
                    "account_ok": bool(snapshot.get("ok")),
                    "account_total_usdt": float(balance.get("total_usdt") or 0),
                    "account_available_usdt": float(balance.get("available_usdt") or 0),
                    "account_unrealized_pnl": float(balance.get("unrealized_pnl") or 0),
                    "account_positions": int(snapshot.get("positions") or 0),
                    "last_author": latest["author"] if latest else "",
                    "last_preview": latest["content"][:300] if latest else "",
                }
            )
        return result


def _account_totals(db: Database) -> dict[str, Any]:
    exchange_names = list(dict.fromkeys(get_channel_exchange_map(db).values()))
    snapshots = [_load_exchange_snapshot(db, name) for name in exchange_names]
    balances = [snapshot.get("balance") or {} for snapshot in snapshots]
    return {
        "total_usdt": sum(float(balance.get("total_usdt") or 0) for balance in balances),
        "available_usdt": sum(float(balance.get("available_usdt") or 0) for balance in balances),
        "unrealized_pnl": sum(float(balance.get("unrealized_pnl") or 0) for balance in balances),
        "positions": sum(int(snapshot.get("positions") or 0) for snapshot in snapshots),
    }


def _activity(db: Database) -> list[dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=6)).date()
    with db._connect() as conn:
        rows = conn.execute(
            """SELECT substr(created_at,1,10) day, COUNT(*) count
            FROM messages WHERE created_at>=? GROUP BY day ORDER BY day""",
            (cutoff.isoformat(),),
        ).fetchall()
    return [dict(row) for row in rows]


def _paper_order_count(db: Database) -> int:
    with db._connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM orders WHERE okx_ord_id LIKE 'paper-%'").fetchone()[0])


@router.get("/orders-card")
def orders_card(
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    """Positions / pending / history rows from local snapshots; no exchange call."""
    enabled = {str(c.get("channel_id")): bool(c.get("enabled"))
               for c in get_switches(db).get("channels") or [] if isinstance(c, dict)}
    names = {**channel_names(db), **CHANNEL_NAMES}
    cards = [{**c, "channel_name": names.get(str(c["channel_id"]), c["channel_name"])} for c in _channel_cards(db)]
    card = build_orders_card(
        cards,
        db.list_orders(limit=100),
        get_channel_exchange_map(db).keys(),
        enabled,
    )
    cfg = get_full_config(db)
    return {
        **card,
        "totals": {**db.dashboard_stats(), "orders_paper": _paper_order_count(db)},
        "channel_names": names,
        "trade_blocked_channels": sorted(trade_blocked_channels(db)),
        "dry_run": cfg["risk"]["dry_run"],
        "kill_switch": cfg["risk"]["kill_switch"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/summary")
def summary(
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    cfg = get_full_config(db)
    stats = db.dashboard_stats()
    balance = get_balance_cached(get_okx())
    return {
        "kill_switch": cfg["risk"]["kill_switch"],
        "dry_run": cfg["risk"]["dry_run"],
        "user_listener_enabled": False,
        "messages_today": stats["total_messages"],
        "balance_usdt": balance.get("available_usdt", 0),
        "exchange": exchange_label(db),
    }


@router.get("/stats")
def stats(
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    cfg = get_full_config(db)
    base = db.dashboard_stats()
    balance = get_balance_cached(get_okx(), force=True)
    positions = get_positions_cached(get_okx(), force=True)
    account_totals = _account_totals(db)
    messages, _ = db.list_messages(page=1, per_page=10)
    live_orders = max(0, base["orders_total"] - base["orders_dry_run"])
    return {
        "running": True,
        "kill_switch": cfg["risk"]["kill_switch"],
        "dry_run": cfg["risk"]["dry_run"],
        "okx_demo": cfg["okx_demo"],
        "exchange": exchange_label(db),
        "exchange_mode": exchange_mode_label(db),
        "balance_total_usdt": account_totals["total_usdt"] or balance.get("total_usdt", 0),
        "balance_available_usdt": account_totals["available_usdt"] or balance.get("available_usdt", 0),
        "unrealized_pnl": account_totals["unrealized_pnl"],
        "positions_count": account_totals["positions"] or len(positions),
        "positions": positions,
        **base,
        "noise_signals": db.intent_breakdown().get("noise", 0),
        "messages_today": base["total_messages"],
        "today_executed": base["executed"],
        "today_open_signals": base["open_signals"],
        "today_orders": base["orders_total"],
        "signal_act_rate_pct": round(live_orders / base["open_signals"] * 100, 1)
        if base["open_signals"]
        else 0,
        "avg_open_confidence": 0,
        "user_listener_enabled": False,
        "deepseek_model": cfg["deepseek"]["model"],
        "position_pct": cfg["risk"]["position_pct"],
        "min_confidence": cfg["risk"]["min_confidence"],
        "default_leverage": cfg["okx"]["default_leverage"],
        "symbols_whitelist": cfg["risk"]["symbols_whitelist"],
        "channel_cards": _channel_cards(db),
        "recent_open_signals": db.recent_open_signals(limit=8),
        "activity_by_day": _activity(db),
        "intent_breakdown": db.intent_breakdown(),
        "recent_messages": messages,
    }

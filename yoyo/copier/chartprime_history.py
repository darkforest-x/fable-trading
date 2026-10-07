from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from yoyo.copier.config import ROOT

CHANNEL_ID = "988830102957736027"
CHANNEL_URL = "https://discord.com/channels/748196867879469219/988830102957736027"
TRADES_PATH = ROOT / "data" / "chartprime_trades_2026.json"


def load_chartprime_trades() -> list[dict[str, Any]]:
    if not TRADES_PATH.exists():
        return []
    try:
        payload = json.loads(TRADES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    trades = payload.get("trades") or []
    return [dict(t) for t in trades if isinstance(t, dict)]


def chartprime_summary() -> dict[str, Any]:
    trades = load_chartprime_trades()
    wins = [t for t in trades if t.get("result") == "win_tp"]
    losses = [t for t in trades if t.get("result") == "loss_sl"]
    unknown = [t for t in trades if t.get("result") == "unknown"]
    settled = [t for t in trades if t.get("result") != "unknown"]
    long_count = sum(1 for t in trades if t.get("side") == "long")
    short_count = sum(1 for t in trades if t.get("side") == "short")
    avg_tp = sum(int(t.get("tp_hits") or 0) for t in trades) / len(trades) if trades else 0
    pnl = sum(float(t.get("net_pnl_100u_5x") or 0) for t in settled)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "unknown": len(unknown),
        "settled": len(settled),
        "win_rate": (len(wins) / len(settled) * 100) if settled else 0,
        "first_tp_rate": (len(wins) / len(trades) * 100) if trades else 0,
        "avg_tp": avg_tp,
        "max_tp": max([int(t.get("tp_hits") or 0) for t in trades], default=0),
        "long_count": long_count,
        "short_count": short_count,
        "net_pnl_100u_5x": pnl,
    }


def chartprime_compound_summary(initial_equity: float = 500.0) -> dict[str, Any]:
    equity = initial_equity
    peak = initial_equity
    max_drawdown = 0.0
    settled_trades = 0
    wiped_out = False
    for trade in sorted(load_chartprime_trades(), key=lambda item: str(item.get("time") or "")):
        if trade.get("result") == "unknown":
            continue
        settled_trades += 1
        return_rate = float(trade.get("net_pnl_100u_5x") or 0) / 100.0
        equity = max(0.0, equity * (1 + return_rate))
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        if equity <= 0:
            wiped_out = True
            break
    return {
        "initial_equity": initial_equity,
        "final_equity": equity,
        "net_pnl": equity - initial_equity,
        "return_pct": (equity / initial_equity - 1) * 100 if initial_equity else 0.0,
        "max_drawdown_pct": max_drawdown,
        "settled_trades": settled_trades,
        "wiped_out": wiped_out,
    }


def chartprime_monthly_summary() -> list[dict[str, Any]]:
    months: dict[str, list[dict[str, Any]]] = {}
    for trade in load_chartprime_trades():
        month = str(trade.get("time") or "")[:7]
        if not month:
            continue
        months.setdefault(month, []).append(trade)
    out = []
    for month, trades in sorted(months.items()):
        wins = sum(1 for t in trades if t.get("result") == "win_tp")
        losses = sum(1 for t in trades if t.get("result") == "loss_sl")
        unknown = sum(1 for t in trades if t.get("result") == "unknown")
        settled = wins + losses
        out.append(
            {
                "month": month,
                "trades": len(trades),
                "wins": wins,
                "losses": losses,
                "unknown": unknown,
                "win_rate": wins / settled * 100 if settled else 0,
            }
        )
    return out

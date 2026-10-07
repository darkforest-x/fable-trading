"""Read-only paper account summary for the workbench (no exchange calls)."""
from __future__ import annotations

import math
import statistics
from typing import Any

from fastapi import APIRouter, Depends

from yoyo.copier.api.auth import verify_token
from yoyo.copier.api.deps import get_db
from yoyo.copier.discord_channels import channel_names
from yoyo.copier.exchange import PAPER, get_channel_exchange_map
from yoyo.copier.orders_card import CHANNEL_NAMES
from yoyo.copier.paper.book import FEE_RATE, PaperBook
from yoyo.copier.paper.client import paper_risk_pct
from yoyo.copier.paper.engine import POLL_SECONDS
from yoyo.copier.store.sqlite import Database

router = APIRouter(prefix="/paper", tags=["paper"])


def _trade(book: PaperBook, p: dict[str, Any], names: dict[str, str], sizing: dict[int, str]) -> dict[str, Any]:
    net = p["realized_pnl"] - p["fees"]
    return {"id": p["id"], "channel_id": p["account"], "trader": names.get(p["account"], p["account"]),
            "inst_id": p["inst_id"], "side": p["side"], "entry_px": p["entry_px"], "exit_px": p["exit_px"],
            "qty": p["qty"], "notional_usdt": p["qty"] * p["entry_px"], "leverage": p["leverage"],
            "initial_sl": p["initial_sl"], "sl": p["sl"], "tp": p["tp"], "net_pnl": net, "fees": p["fees"],
            "r": book.r_multiple(p), "risk_usdt": risk_usdt(p), "close_reason": p["close_reason"], "opened_at": p["opened_at"],
            "closed_at": p["closed_at"], "sizing": sizing.get(p["message_id"], "legacy_margin")}


R_BUCKETS = ((-math.inf, -1.0, "≤-1R"), (-1.0, 0.0, "-1~0R"), (0.0, 1.0, "0~1R"), (1.0, 2.0, "1~2R"),
             (2.0, 5.0, "2~5R"), (5.0, math.inf, "≥5R"))


def risk_usdt(p: dict[str, Any], qty: float | None = None) -> float | None:
    """1R in USDT: distance from entry to the initial stop times the position size."""
    if not p.get("initial_sl"):
        return None
    return abs(p["entry_px"] - p["initial_sl"]) * (p["qty"] if qty is None else qty)


def r_stats(book: PaperBook, closed: list[dict[str, Any]], equity: float,
            sizing: dict[int, str] | None = None) -> dict[str, Any]:
    """R-multiple statistics of closed paper trades (net of fees), oldest first.

    R itself does not depend on position size, so trades opened under the
    pre-2026-10-07 margin sizing stay in every R figure. Only the average 1R
    amount is a sizing fact, so it reads fixed-risk trades alone (owner default,
    2026-10-07); ``legacy_trades`` counts the excluded ones.
    """
    rows = sorted(closed, key=lambda p: (p["closed_at"] or "", p["id"]))
    rs = [r for r in (book.r_multiple(p) for p in rows) if r is not None]
    fixed = [p for p in rows if sizing is None or sizing.get(p["message_id"]) == "fixed_risk"]
    risks = [x for x in (risk_usdt(p) for p in fixed) if x]
    wins, losses = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    streak = worst_streak = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        worst_streak = max(worst_streak, streak)
    curve, peak, max_dd = 0.0, 0.0, 0.0
    for r in rs:
        curve += r
        peak = max(peak, curve)
        max_dd = max(max_dd, peak - curve)
    buckets = {label: sum(1 for r in rs if lo <= r < hi) for lo, hi, label in R_BUCKETS}
    return {
        "trades": len(rs), "win_rate": len(wins) / len(rs) if rs else None,
        "mean_r": statistics.fmean(rs) if rs else None, "median_r": statistics.median(rs) if rs else None,
        "sum_r": sum(rs) if rs else None, "best_r": max(rs) if rs else None, "worst_r": min(rs) if rs else None,
        "avg_win_r": statistics.fmean(wins) if wins else None, "avg_loss_r": statistics.fmean(losses) if losses else None,
        "profit_factor_r": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "max_losing_streak": worst_streak, "max_drawdown_r": max_dd,
        "avg_risk_usdt": statistics.fmean(risks) if risks else None,
        "avg_risk_pct": (statistics.fmean(risks) / equity) if risks and equity > 0 else None,
        "legacy_trades": len(rows) - len(fixed), "buckets": buckets,
    }


@router.get("/summary")
def summary(db: Database = Depends(get_db), _: str = Depends(verify_token)):
    book = PaperBook(db)
    sizing = book.sizing_by_message()
    names = {**channel_names(db), **CHANNEL_NAMES}
    routed = [cid for cid, ex in get_channel_exchange_map(db).items() if ex == PAPER]
    closed = book.closed_positions(limit=500)
    accounts = []
    for cid in sorted(set(routed) | set(book.accounts()), key=lambda c: names.get(c, c)):
        bal = book.balance(cid)
        mine = [p for p in closed if p["account"] == cid]
        rs = [r for r in (book.r_multiple(p) for p in mine) if r is not None]
        wins = sum(1 for p in mine if p["realized_pnl"] - p["fees"] > 0)
        stats = r_stats(book, mine, book.starting_equity(), sizing)
        accounts.append({"channel_id": cid, "trader": names.get(cid, cid), "routed": cid in routed, "r_stats": stats,
                         "equity": bal["equity"], "total_usdt": bal["total_usdt"],
                         "unrealized_pnl": bal["unrealized_pnl"], "open_positions": bal["positions"],
                         "closed_trades": len(mine), "wins": wins, "sum_r": sum(rs) if rs else None,
                         "net_pnl": sum(p["realized_pnl"] - p["fees"] for p in mine)})
    open_rows = []
    for p in book.open_positions():
        mark = book.mark(p["inst_id"])
        upl = book.unrealized(p)
        one_r = risk_usdt(p, p["open_qty"])
        equity = book.balance(p["account"])["equity"]
        open_rows.append({**_trade(book, p, names, sizing), "mark": mark, "unrealized_pnl": upl,
                          "open_qty": p["open_qty"], "liquidation_px": book.liquidation_px(p),
                          "risk_usdt": one_r, "risk_pct_of_equity": (one_r / equity) if one_r and equity > 0 else None,
                          "current_r": (upl / one_r) if one_r else None,
                          "stop_distance_pct": abs(p["entry_px"] - p["initial_sl"]) / p["entry_px"] if p["initial_sl"] else None})
    pending = [{"id": o["id"], "channel_id": o["account"], "trader": names.get(o["account"], o["account"]),
                "inst_id": o["inst_id"], "side": o["side"], "px": o["px"], "notional_usdt": o["qty"] * o["px"],
                "mark": book.mark(o["inst_id"]), "sl": o["sl"], "tp": o["tp"], "created_at": o["created_at"]}
               for o in book.pending_orders()]
    return {"starting_equity": book.starting_equity(), "fee_rate_per_side": FEE_RATE, "poll_seconds": POLL_SECONDS,
            "r_stats_all": r_stats(book, closed, book.starting_equity(), sizing),
            "risk_pct": paper_risk_pct(db),
            "sizing_note": (f"按固定风险开仓：每笔 1R = 该频道账户已实现权益的 {paper_risk_pct(db):.0%}，仓位 = 1R ÷ 入场到止损的距离。"
                            "仓位超过该频道杠杆允许的上限时按上限开仓，这笔实际风险会小于设定值并标为“已封顶”；没有止损的信号不开仓。"
                            "10 月 7 日改口径之前开出的仓位标“旧口径”：R 照常计入统计，平均 1R 金额只算固定风险的交易。"),
            "accounts": accounts, "open": open_rows, "pending": pending,
            "closed": [_trade(book, p, names, sizing) for p in closed[:100]]}

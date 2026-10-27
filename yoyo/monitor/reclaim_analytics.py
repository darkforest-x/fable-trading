"""Read-only analytics from the complete Monday paper ledger.

Cash metrics recognize exit tranches at their timestamps; whole-trade metrics
use final-close timestamps and the original entry notional. Legacy closed rows
without tranches contribute one synthetic accounting event (never a new fill).
No strategy, order, fee or historical record is changed by these calculations.
"""
from collections import defaultdict
import math

DAY = 86_400_000


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def trade_stats(rows):
    valid = [p for p in rows if finite(p.get("pnl"))]
    wins = [p["pnl"] for p in valid if p["pnl"] > 0]
    losses = [p["pnl"] for p in valid if p["pnl"] < 0]
    return dict(closed_count=len(rows), measured_count=len(valid), wins=len(wins), losses=len(losses),
                breakeven=len(valid)-len(wins)-len(losses),
                win_rate=len(wins)/len(valid)*100 if valid else None,
                profit_factor=sum(wins)/-sum(losses) if losses else None,
                profit_factor_state="finite" if losses else "no_losses" if wins else "empty",
                average_pnl=sum(p["pnl"] for p in valid)/len(valid) if valid else None)


def compress_curve(points, limit=1000):
    """Preserve endpoints and local cash-equity extrema; stats use full points."""
    if len(points) <= limit:
        return points
    size = math.ceil((len(points)-2)/((limit-2)//2))
    result = [points[0]]
    for offset in range(1, len(points)-1, size):
        bucket = points[offset:min(len(points)-1, offset+size)]
        extrema = {min(range(len(bucket)), key=lambda i: bucket[i]["equity"]),
                   max(range(len(bucket)), key=lambda i: bucket[i]["equity"])}
        result.extend(bucket[i] for i in sorted(extrema))
    return result+[points[-1]]


def build_analytics(positions, exits, account, quotes, now_ms, days=0, connected=False):
    """Use complete saved positions/exits and current public quotes, without IO."""
    track = account["track"]
    start_ms = max(account["started_ms"], now_ms-days*DAY) if days else account["started_ms"]
    funded = [p for p in positions if p["track"] == track and p.get("taken") == 1
              and finite(p.get("notional")) and p["notional"] > 0]
    indexed = {p["id"]: p for p in funded}
    grouped = defaultdict(list)
    for e in exits:
        if e["position_id"] in indexed:
            grouped[e["position_id"]].append(e)
    events, invalid = [], 0
    for p in funded:
        rows = grouped[p["id"]]
        if not rows and p["stage"] == "closed":
            rows = [dict(exit_ms=p.get("exit_ms"), notional=p["notional"], pnl=p.get("pnl"),
                         fee_bp=p.get("fee_bp"), reason=p.get("reason"))]
        for e in rows:
            stamp = e.get("exit_ms")
            if not finite(stamp) or not finite(e.get("pnl")):
                invalid += 1
                continue
            if stamp > now_ms:
                continue
            fee = e["notional"]*e["fee_bp"]/10000 if finite(e.get("notional")) and finite(e.get("fee_bp")) else None
            events.append(dict(time_ms=stamp, pnl=e["pnl"], fee=fee, symbol=p["symbol"], side=p["side"],
                               reason=e.get("reason") or "unknown"))
    events.sort(key=lambda e: e["time_ms"])
    window = [e for e in events if e["time_ms"] >= start_ms]
    closed = [p for p in funded if p["stage"] == "closed" and finite(p.get("exit_ms")) and start_ms <= p["exit_ms"] <= now_ms]
    opened = [p for p in funded if p["stage"] != "closed"]
    initial = account["start_equity"]
    # Blow-up refills (allin3) are cash added after the exit of the same timestamp: not profit, and the low stays visible.
    refills = [r for r in account.get("refills") or [] if finite(r.get("time_ms")) and finite(r.get("amount")) and r["time_ms"] <= now_ms]
    baseline = (initial+sum(e["pnl"] for e in events if e["time_ms"] < start_ms)
                +sum(r["amount"] for r in refills if r["time_ms"] < start_ms))
    equity, peak, max_dd, max_dd_pct, added = baseline, baseline, 0., 0., 0.
    curve = [dict(time_ms=start_ms, equity=equity, pnl=0.)]
    simultaneous = defaultdict(float)
    for e in window:
        simultaneous[e["time_ms"]] += e["pnl"]
    steps = sorted([(stamp, 0, delta) for stamp, delta in simultaneous.items()]
                   + [(r["time_ms"], 1, r["amount"]) for r in refills if r["time_ms"] >= start_ms])
    for stamp, refill, delta in steps:
        equity += delta
        added += delta if refill else 0.
        peak = max(peak, equity)
        drawdown = peak-equity
        max_dd = max(max_dd, drawdown)
        max_dd_pct = max(max_dd_pct, drawdown/peak*100 if peak > 0 else 0)
        point = dict(time_ms=stamp+refill, equity=equity, pnl=equity-baseline-added)
        if curve[-1]["time_ms"] == point["time_ms"]:
            curve[-1] = point
        else:
            curve.append(point)
    if curve[-1]["time_ms"] != now_ms:
        curve.append(dict(time_ms=now_ms, equity=equity, pnl=equity-baseline-added))
    missing_quotes, unrealized, exposure = 0, 0., 0.
    for p in opened:
        remaining = p.get("remaining_notional")
        if not finite(remaining):
            remaining = p["notional"]
        exposure += remaining
        q = quotes.get(p["symbol"], {})
        if (not connected or not finite(q.get("price")) or q["price"] <= 0
                or not finite(q.get("received_ms")) or not 0 <= now_ms-q["received_ms"] <= 90_000):
            missing_quotes += 1
        else:
            unrealized += p["side"]*(q["price"]-p["entry"])/p["entry"]*remaining
    def total_fees(rows):
        return sum(e["fee"] for e in rows) if all(e["fee"] is not None for e in rows) else None
    sides = [dict(side=side, **trade_stats([p for p in closed if p["side"] == side]),
                  realized_pnl=sum(e["pnl"] for e in window if e["side"] == side)) for side in [1, -1]]
    symbols = sorted(set(e["symbol"] for e in window) | {p["symbol"] for p in closed})
    by_symbol = [dict(symbol=s, **trade_stats([p for p in closed if p["symbol"] == s]),
                      realized_pnl=sum(e["pnl"] for e in window if e["symbol"] == s)) for s in symbols]
    by_symbol.sort(key=lambda s: (-abs(s["realized_pnl"]), s["symbol"]))
    by_reason = []
    for reason in sorted(set(e["reason"] for e in window)):
        rows = [e for e in window if e["reason"] == reason]
        by_reason.append(dict(reason=reason, exit_count=len(rows), realized_pnl=sum(e["pnl"] for e in rows), fees=total_fees(rows)))
    summary = dict(**trade_stats(closed), realized_pnl=sum(e["pnl"] for e in window), settled_fees=total_fees(window),
                   exit_count=len(window), open_count=len(opened), open_notional=exposure,
                   unrealized_pnl=None if missing_quotes else unrealized, missing_quotes=missing_quotes,
                   max_drawdown=max_dd, max_drawdown_pct=max_dd_pct, period_start_equity=baseline,
                   current_realized_equity=account["equity"], refill_count=sum(r["time_ms"] >= start_ms for r in refills),
                   refilled=added)
    return dict(paper_only=True, track=str(track), days=days, now_ms=now_ms, start_ms=start_ms, summary=summary,
                curve=compress_curve(curve), curve_points=len(curve), by_side=sides, by_symbol=by_symbol, by_reason=by_reason,
                coverage=dict(invalid_events=invalid, missing_trade_pnl=len(closed)-summary["measured_count"],
                              missing_fees=sum(e["fee"] is None for e in window),
                              ledger_difference=account["equity"]-(initial+sum(e["pnl"] for e in events)+sum(r["amount"] for r in refills))))

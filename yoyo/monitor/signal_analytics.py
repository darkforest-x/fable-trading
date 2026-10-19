"""Read-only signal-cohort analytics, separate from fills and account P&L.

Card cohorts use the original close for Beijing calendar periods; closed
history uses the saved exit record time. Closed-bar performance is a reference projection;
YOLO confirmation reuses its original event's projection and is counted once.
The V13.1 retest cohort (``confirmation="retest"``, the signal center since
2026-10-07) carries its own projection and starts at its forward cutover.
No future feature computation, strategy changes, or outbox writes occur here.
"""
from __future__ import annotations

import math
import statistics
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from yoyo.contracts.signal_universe import public_policy, exclusion_reason

from yoyo.monitor import (FRESH_MS, MODEL_KIND, MODEL_PROTOCOL, SIGNAL_KIND, SIGNAL_PROTOCOL,
                          SHORT_SIGNAL_PROTOCOL, TIMEFRAMES, LEGACY_SIGNAL_PROTOCOL,
                          LEGACY_SIGNAL_KIND, LEGACY_MODEL_PROTOCOL, BARK_ARM_KEY)
from yoyo.monitor.v130_policy import POLICY as V130_POLICY, SIGNAL_KIND as V130_KIND, SIGNAL_PROTOCOL as V130_PROTOCOL

TZ = ZoneInfo("Asia/Shanghai")
OUTCOMES = {"active", "profit", "loss", "breakeven", "unknown"}
CLOSED = {"profit", "loss", "breakeven"}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def search_key(value):
    return "".join(c for c in unicodedata.normalize("NFKC", str(value)).upper() if c.isalnum())


def period_start(now, period):
    if period == "all":
        return None
    day = datetime.fromtimestamp(now / 1000, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        day -= timedelta(days=day.weekday())
    return int(day.timestamp() * 1000)


def performance(row):
    p = row.get("performance")
    if not isinstance(p, dict):
        return "unknown", None
    status = p.get("status")
    if status not in OUTCOMES or status == "unknown":
        return "unknown", None
    value = p.get("current_r") if status == "active" else p.get("exit_r")
    # Missing realized R cannot borrow current/peak R or be counted as zero.
    if finite(value) and status in ("profit", "loss", "breakeven"):
        expected = "profit" if value > 1e-9 else "loss" if value < -1e-9 else "breakeven"
        if status != expected:
            return "unknown", None
    return status, float(value) if finite(value) else None


def summarize(rows):
    result = dict(total=len(rows), long=0, short=0, active=0, profit=0, loss=0,
                  breakeven=0, unknown=0, closed=0, measured_closed=0,
                  measured_active=0, missing_r=0, realized_r=None, floating_r=None,
                  win_rate=None, average_r=None)
    realized, floating, wins = [], [], 0
    for row in rows:
        if row.get("side") in ("long", "short"):
            result[row["side"]] += 1
        status, value = performance(row)
        result[status] += 1
        if value is None:
            result["missing_r"] += 1
        if status == "active" and value is not None:
            floating.append(value)
        elif status in ("profit", "loss", "breakeven"):
            result["closed"] += 1
            if value is not None:
                realized.append(value)
                wins += value > 1e-9
    result.update(measured_closed=len(realized), measured_active=len(floating),
                  realized_r=math.fsum(realized) if realized else None,
                  floating_r=math.fsum(floating) if floating else None,
                  win_rate=wins / len(realized) if realized else None,
                  average_r=math.fsum(realized) / len(realized) if realized else None)
    return result


def closed_summary(rows):
    """Describe all selected closed projections before pagination, in net R.

    Uses only status, exit_r, entry_time_ms and exit_time_ms from the saved
    performance projection. No prices, costs or returns are reconstructed.
    Drawdown aggregates simultaneous exits; streaks are unavailable when
    simultaneous exits have mixed signs or any R/exit timestamp is missing.
    This equal-risk R sum is not account equity or a compounded return.
    """
    rows = [row for row in rows if performance(row)[0] in CLOSED]
    values = [performance(row)[1] for row in rows]
    measured = [v for v in values if v is not None]
    wins = [v for v in measured if v > 1e-9]
    losses = [v for v in measured if v < -1e-9]
    durations, exits = [], {}
    for row, value in zip(rows, values):
        p = row["performance"]
        entry, end = p.get("entry_time_ms"), p.get("exit_time_ms")
        if finite(entry) and finite(end) and 0 < entry <= end:
            durations.append(end - entry)
        if finite(end) and end > 0 and value is not None:
            exits.setdefault(end, []).append(value)
    complete_order = bool(rows) and sum(map(len, exits.values())) == len(rows)
    drawdown = max_win = max_loss = None
    ambiguous = any(len({1 if v > 1e-9 else -1 if v < -1e-9 else 0 for v in group}) > 1
                    for group in exits.values())
    if complete_order:
        balance = peak = drawdown = 0.0
        for end in sorted(exits):
            balance = math.fsum((balance, *exits[end]))
            peak = max(peak, balance)
            drawdown = max(drawdown, peak - balance)
        if not ambiguous:
            current_win = current_loss = max_win = max_loss = 0
            for end in sorted(exits):
                for value in exits[end]:
                    current_win = current_win + 1 if value > 1e-9 else 0
                    current_loss = current_loss + 1 if value < -1e-9 else 0
                    max_win, max_loss = max(max_win, current_win), max(max_loss, current_loss)
    mean = lambda values: math.fsum(values) / len(values) if values else None
    return dict(total=len(rows), measured=len(measured), missing_r=len(rows) - len(measured),
                wins=len(wins), losses=len(losses), breakeven=len(measured) - len(wins) - len(losses),
                win_rate=len(wins) / len(measured) if measured else None,
                net_r=math.fsum(measured) if measured else None, average_r=mean(measured),
                median_r=statistics.median(measured) if measured else None,
                profit_factor=math.fsum(wins) / -math.fsum(losses) if losses else None,
                payoff_ratio=mean(wins) / -mean(losses) if wins and losses else None,
                average_win_r=mean(wins), average_loss_r=mean(losses),
                best_r=max(measured) if measured else None, worst_r=min(measured) if measured else None,
                max_drawdown_r=drawdown, max_consecutive_wins=max_win, max_consecutive_losses=max_loss,
                average_holding_ms=mean(durations), measured_holding=len(durations),
                missing_exit_time=sum(not finite(row['performance'].get('exit_time_ms')) or
                                      row['performance']['exit_time_ms'] <= 0 for row in rows),
                ambiguous_exit_order=ambiguous,
                first_exit_ms=min(exits) if exits else None, last_exit_ms=max(exits) if exits else None)


def closed_curve(rows):
    """Aggregate saved exits at identical timestamps before charting net R.

    Reads only status, exit_r and exit_time_ms, across the complete filtered
    cohort. An incomplete series is unavailable, never silently zero-filled.
    The cumulative values are equal-risk signal references, not equity.
    """
    rows = [r for r in rows if performance(r)[0] in CLOSED]
    if not rows:
        return dict(status="empty", points=[])
    exits = {}
    for row in rows:
        value, end = performance(row)[1], row["performance"].get("exit_time_ms")
        if value is None or not finite(end) or end <= 0:
            return dict(status="incomplete", points=[])
        exits.setdefault(end, []).append(value)
    balance = peak = 0.0
    points = []
    for end in sorted(exits):
        balance = math.fsum((balance, *exits[end]))
        peak = max(peak, balance)
        points.append(dict(exit_ms=end, net_r=math.fsum(exits[end]), cumulative_r=balance,
                           drawdown_r=peak - balance, closed=len(exits[end])))
    return dict(status="ready", points=points)


def closed_statistics(rows):
    """Return complete filtered history and parallel breakdowns, never a page."""
    reasons = {"initial_stop": "初始止损", "trailing_stop": "跟随保护", "raw_opposite_next_open": "反向信号退出"}
    result = closed_summary(rows)
    result["curve"] = closed_curve(rows)
    result["by_timeframe"] = [dict(label=tf, **closed_summary([r for r in rows if r.get("timeframe") == tf]))
                              for tf in TIMEFRAMES]
    result["by_side"] = [dict(label=label, **closed_summary([r for r in rows if r.get("side") == side]))
                         for side, label in (("long", "多头"), ("short", "空头"))]
    result["by_exit_reason"] = [dict(label=label, **closed_summary([
        r for r in rows if (r.get("performance") or {}).get("exit_reason") == reason]))
        for reason, label in reasons.items()]
    result["by_exit_reason"].append(dict(label="其他 / 未记录", **closed_summary([
        r for r in rows if (r.get("performance") or {}).get("exit_reason") not in reasons])))
    return result


def ledger(store, *, now, source="live", confirmation="raw", period="all", timeframe=None,
           side=None, search="", outcome="all", sort="newest", offset=0, limit=24):
    if (source not in ("live", "warmup", "replay", "legacy") or confirmation not in ("raw", "yolo", "retest")
            or (confirmation == "retest" and source != "live")
            or period not in ("all", "today", "week") or timeframe not in (None, *TIMEFRAMES)
            or side not in (None, "long", "short") or outcome not in ("all", "closed", *OUTCOMES)
            or sort not in ("newest", "oldest", "r_desc", "r_asc")):
        raise ValueError("unsupported ledger filter")
    cutoff = None
    own = confirmation == "retest"
    if own:
        # V13.1 has its own forward boundary; nothing before it is journaled.
        receipt = store.get_meta("notification_policy:" + V130_POLICY, {})
        cutoff = receipt.get("activated_ms") if isinstance(receipt, dict) else None
        if type(cutoff) is not int or cutoff < 0:
            raise RuntimeError("V13.1 正在初始化，等待首次同步完成。")
    elif source in ("live", "warmup"):
        receipt = store.get_meta(BARK_ARM_KEY, {})
        cutoff = receipt.get("activated_ms") if isinstance(receipt, dict) else None
        if type(cutoff) is not int or cutoff < 0:
            raise RuntimeError("V12.8 正在初始化，等待首次同步完成。")
    legacy = source == "legacy"
    raw_kind = LEGACY_SIGNAL_KIND if legacy else SIGNAL_KIND
    raw_protocol = LEGACY_SIGNAL_PROTOCOL if legacy else SIGNAL_PROTOCOL
    model_protocol = LEGACY_MODEL_PROTOCOL if legacy else MODEL_PROTOCOL
    if own:
        rows = [r for r in store.list_events(source="live", kind=V130_KIND, protocol=V130_PROTOCOL,
                                             confirmation="retest", summary=True, complete=True)
                if finite(r.get("bar_close_ms")) and r["bar_close_ms"] > cutoff]
    else:
        rows = store.list_events(source="replay" if source == "replay" else "live",
                                 protocol=(raw_protocol, model_protocol),
                                 display_scope=source if source in ("live", "warmup") else None,
                                 display_cutoff_ms=cutoff, summary=True, complete=True)
    raw = {}
    for r in ([] if own else rows):
        if (r.get("kind") == raw_kind and r.get("protocol") == raw_protocol
                and r.get("confirmation") in ("raw", "raw_yolo")):
            # Old combined rows cannot create a second raw observation or
            # constitute a current-protocol YOLO proof by their name alone.
            canonical_id = store.event_id(dict(r, confirmation="raw"))
            if canonical_id not in raw or r.get("confirmation") == "raw":
                raw[canonical_id] = r
    selected = rows if own else list(raw.values()) if confirmation == "raw" else [r for r in rows if
        r.get("kind") == MODEL_KIND and r.get("protocol") == model_protocol and r.get("confirmation") == "yolo"]
    start, query = period_start(now, period), search_key(search)
    unique = {}
    for row in selected:
        original = row.get("indicator") if row.get("confirmation") == "yolo" else row
        original = original if isinstance(original, dict) else row
        origin_id = (store.event_id(dict(row, confirmation="raw")) if confirmation == "raw"
                     else row.get("source_event_id") or original.get("id"))
        if not origin_id and row.get("confirmation") == "yolo":
            origin_id = store.event_id(original)
        key = origin_id or row["id"]
        origin = raw.get(key, original)
        origin_close = origin.get("bar_close_ms")
        if not finite(origin_close) or origin_close > now or (outcome != "closed" and start is not None and origin_close < start):
            continue
        if (timeframe and row.get("timeframe") != timeframe) or (side and row.get("side") != side):
            continue
        if query and query not in search_key(row.get("symbol", "")):
            continue
        # Embedded confirmation-time snapshots are stale when their journal
        # origin is missing; retain the event for inspection with unknown R.
        projection = row.get("performance") if own else raw[key].get("performance") if key in raw else None
        row = dict(row, performance=projection, origin_close_ms=origin_close,
                   display_scope=source, is_fresh=source == "live" and 0 <= now - row["bar_close_ms"] <= FRESH_MS)
        if own:
            reason = exclusion_reason(row['symbol'], timeframe=row['timeframe'], side=row['side'])
            row.update(entry_eligible=reason is None, entry_exclusion=reason,
                       is_fresh=row['is_fresh'] and reason is None)
        status, value = performance(row)
        if isinstance(row["performance"], dict):
            row["performance"] = dict(row["performance"], status=status)
            for field in ("current_r", "exit_r", "peak_r"):
                if not finite(row["performance"].get(field)):
                    row["performance"][field] = None
        else:
            row["performance"] = None
        if outcome == "closed":
            if status not in CLOSED:
                continue
            exit_time = (row.get("performance") or {}).get("exit_time_ms")
            if finite(exit_time) and exit_time > now:
                continue
            if start is not None and (not finite(exit_time) or exit_time < start):
                continue
        elif outcome != "all" and status != outcome:
            continue
        row.update(outcome_status=status, sort_r=value)
        unique.setdefault(key, row)
    items = list(unique.values())
    stats = summarize(items)
    history = closed_statistics(items) if outcome == "closed" else None
    by_timeframe = [dict(timeframe=tf, **summarize([r for r in items if r.get("timeframe") == tf])) for tf in TIMEFRAMES]
    if sort in ("r_desc", "r_asc"):
        sign = -1 if sort == "r_desc" else 1
        items.sort(key=lambda r: (r["sort_r"] is None, sign * (r["sort_r"] or 0), -r["origin_close_ms"], r["id"]))
    elif outcome == "closed":
        sign = -1 if sort == "newest" else 1
        def history_order(row):
            end = row["performance"].get("exit_time_ms")
            valid = finite(end) and end > 0
            return (not valid, sign * end if valid else 0, row["id"])
        items.sort(key=history_order)
    else:
        items.sort(key=lambda r: (r["origin_close_ms"], r["id"]), reverse=sort == "newest")
    offset, limit = max(0, int(offset)), max(1, min(2000, int(limit)))
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset,
            "entry_universe": public_policy(),
            "has_more": offset + limit < len(items), "stats": stats, "by_timeframe": by_timeframe,
            "closed_statistics": history,
            "as_of_ms": now, "period_start_ms": start, "period": period, "source": source,
            "basis": "signal_close_reference", "date_basis": "exit_record_time" if outcome == "closed" else "original_signal_close",
            "timezone": "Asia/Shanghai", "sort": sort}

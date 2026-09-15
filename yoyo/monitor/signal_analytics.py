"""Read-only signal-cohort analytics, separate from fills and account P&L.

The immutable original close assigns an observation to a Beijing calendar
period. Current closed-bar performance is a mutable reference projection;
YOLO confirmation reuses its original event's projection and is counted once.
No future feature computation, strategy changes, or outbox writes occur here.
"""
from __future__ import annotations

import math
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from yoyo.monitor import FRESH_MS, MODEL_KIND, MODEL_PROTOCOL, SIGNAL_KIND, SIGNAL_PROTOCOL, SHORT_SIGNAL_PROTOCOL, TIMEFRAMES

TZ = ZoneInfo("Asia/Shanghai")
OUTCOMES = {"active", "profit", "loss", "breakeven", "unknown"}


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


def ledger(store, *, now, source="live", confirmation="raw", period="all", timeframe=None,
           side=None, search="", outcome="all", sort="newest", offset=0, limit=24):
    if (source not in ("live", "warmup", "replay") or confirmation not in ("raw", "yolo")
            or period not in ("all", "today", "week") or timeframe not in (None, *TIMEFRAMES)
            or side not in (None, "long", "short") or outcome not in ("all", *OUTCOMES)
            or sort not in ("newest", "oldest", "r_desc", "r_asc")):
        raise ValueError("unsupported ledger filter")
    cutoff = None
    if source != "replay":
        receipt = store.get_meta("notification_policy:v9_bark_arm", {})
        cutoff = receipt.get("activated_ms") if isinstance(receipt, dict) else None
        if type(cutoff) is not int or cutoff < 0:
            raise RuntimeError("未找到 V9 首次启用时间，暂不混合展示实时与预热历史。")
    rows = store.list_events(source="replay" if source == "replay" else "live",
                             display_scope=None if source == "replay" else source,
                             display_cutoff_ms=cutoff, summary=True, complete=True)
    raw = {}
    for r in rows:
        if (r.get("kind") == SIGNAL_KIND and r.get("protocol") == SIGNAL_PROTOCOL
                and r.get("confirmation") in ("raw", "raw_yolo")):
            # Old combined rows cannot create a second raw observation or
            # constitute a current-protocol YOLO proof by their name alone.
            canonical_id = store.event_id(dict(r, confirmation="raw"))
            if canonical_id not in raw or r.get("confirmation") == "raw":
                raw[canonical_id] = r
    selected = list(raw.values()) if confirmation == "raw" else [r for r in rows if
        r.get("kind") == MODEL_KIND and r.get("protocol") == MODEL_PROTOCOL and r.get("confirmation") == "yolo"]
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
        if not finite(origin_close) or origin_close > now or (start is not None and origin_close < start):
            continue
        if (timeframe and row.get("timeframe") != timeframe) or (side and row.get("side") != side):
            continue
        if query and query not in search_key(row.get("symbol", "")):
            continue
        # Embedded confirmation-time snapshots are stale when their journal
        # origin is missing; retain the event for inspection with unknown R.
        row = dict(row, performance=raw[key].get("performance") if key in raw else None, origin_close_ms=origin_close,
                   display_scope=source, is_fresh=source == "live" and 0 <= now - row["bar_close_ms"] <= FRESH_MS)
        status, value = performance(row)
        if isinstance(row["performance"], dict):
            row["performance"] = dict(row["performance"], status=status)
            for field in ("current_r", "exit_r", "peak_r"):
                if not finite(row["performance"].get(field)):
                    row["performance"][field] = None
        else:
            row["performance"] = None
        if outcome != "all" and status != outcome:
            continue
        row.update(outcome_status=status, sort_r=value)
        unique.setdefault(key, row)
    items = list(unique.values())
    stats = summarize(items)
    by_timeframe = [dict(timeframe=tf, **summarize([r for r in items if r.get("timeframe") == tf])) for tf in TIMEFRAMES]
    if sort in ("r_desc", "r_asc"):
        sign = -1 if sort == "r_desc" else 1
        items.sort(key=lambda r: (r["sort_r"] is None, sign * (r["sort_r"] or 0), -r["origin_close_ms"], r["id"]))
    else:
        items.sort(key=lambda r: (r["origin_close_ms"], r["id"]), reverse=sort == "newest")
    offset, limit = max(0, int(offset)), max(1, min(2000, int(limit)))
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset,
            "has_more": offset + limit < len(items), "stats": stats, "by_timeframe": by_timeframe,
            "as_of_ms": now, "period_start_ms": start, "period": period, "source": source,
            "basis": "signal_close_reference", "date_basis": "original_signal_close",
            "timezone": "Asia/Shanghai", "sort": sort}

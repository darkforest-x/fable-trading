"""Read-only API adapter for the 突破 / 突破+spike book; sqlite only, no pandas."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from yoyo.monitor.spike_lines_worker import DATABASE

KINDS = ("joint", "break")
TIMEFRAMES = {"joint": ("15m", "30m", "1H", "4H"), "break": ("15m", "1H", "4H", "1Dutc")}
PERIOD_MS = {"15m": 900_000, "30m": 1_800_000, "1H": 3_600_000, "4H": 14_400_000, "1Dutc": 86_400_000}


class LinesUnavailable(RuntimeError):
    pass


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise LinesUnavailable("lines_not_started")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)


def status(path: Path, now_ms: int | None = None) -> dict:
    try:
        db = _connect(path)
        try:
            counts = db.execute("SELECT kind,timeframe,COUNT(*) FROM events GROUP BY kind,timeframe").fetchall()
            since = (now_ms or 0) - 86_400_000
            recent = dict(db.execute("SELECT kind,COUNT(*) FROM events WHERE bar_close_ms>=? GROUP BY kind",
                                     (since,)).fetchall())
            meta = {r[0]: json.loads(r[1]) for r in db.execute("SELECT key,payload FROM meta")}
        finally:
            db.close()
    except sqlite3.Error as error:
        raise LinesUnavailable("lines_book_invalid") from error
    by_kind: dict = {k: {} for k in KINDS}
    for kind, timeframe, n in counts:
        by_kind.setdefault(kind, {})[timeframe] = int(n)
    return {"configured": True, "counts": by_kind, "recent_24h": {k: int(recent.get(k, 0)) for k in KINDS},
            "activation": meta.get("activation"), "scan": meta.get("scan")}


def classify(event: dict, activated_ms: int | None, now_ms: int) -> dict:
    """Add display state: 实时 (seen within one bar of its close after activation), 补录, 启动前."""
    period = PERIOD_MS.get(event.get("timeframe"), 0)
    late = event["detected_at_ms"] - event["bar_close_ms"]
    if activated_ms is None or event["bar_close_ms"] <= activated_ms:
        event["display_state"] = "history"
    elif late <= period + 300_000:
        event["display_state"] = "live"
    else:
        event["display_state"] = "late"
    event["detect_delay_ms"] = late
    event["is_fresh"] = event["display_state"] == "live" and 0 <= now_ms - event["bar_close_ms"] <= max(period, 1_800_000)
    return event


def events(path: Path, *, kind: str, now_ms: int, timeframe: str | None = None, search: str = "",
           limit: int = 300) -> list[dict]:
    if kind not in KINDS or (timeframe is not None and timeframe not in TIMEFRAMES[kind]):
        raise ValueError("unsupported kind or timeframe")
    where, values = ["kind=?"], [kind]
    if timeframe is not None:
        where.append("timeframe=?")
        values.append(timeframe)
    if search:
        where.append("symbol LIKE ?")
        values.append("%" + search.upper().replace("%", "").replace("_", "") + "%")
    try:
        db = _connect(path)
        try:
            rows = db.execute("SELECT payload FROM events WHERE " + " AND ".join(where)
                              + " ORDER BY bar_close_ms DESC, symbol LIMIT ?", values + [limit]).fetchall()
            row = db.execute("SELECT payload FROM meta WHERE key='activation'").fetchone()
        finally:
            db.close()
    except sqlite3.Error as error:
        raise LinesUnavailable("lines_book_invalid") from error
    activated = json.loads(row[0]).get("activated_ms") if row else None
    return [classify(json.loads(r[0]), activated, now_ms) for r in rows]


def database(runtime: Path) -> Path:
    return Path(runtime) / DATABASE


def ledger(path: Path, *, kind: str, now_ms: int, period: str = "all", timeframe: str | None = None,
           scope: str = "all", search: str = "", outcome: str = "all", sort: str = "newest",
           limit: int = 1000) -> dict:
    """The 信号中心 ledger for joint positions: same summarize/performance rules, same periods."""
    from yoyo.monitor.signal_analytics import OUTCOMES, performance, period_start, search_key, summarize
    if (kind not in KINDS or period not in ("all", "today", "week") or scope not in ("all", "live")
            or (timeframe is not None and timeframe not in TIMEFRAMES[kind])
            or outcome not in ("all", *OUTCOMES) or sort not in ("newest", "oldest", "r_desc", "r_asc")):
        raise ValueError("unsupported ledger filter")
    rows = events(path, kind=kind, now_ms=now_ms, limit=100_000)
    start, query = period_start(now_ms, period), search_key(search)
    items = []
    for row in rows:
        if start is not None and row["bar_close_ms"] < start:
            continue
        if (timeframe and row["timeframe"] != timeframe) or (scope == "live" and row["display_state"] != "live"):
            continue
        if query and query not in search_key(row["symbol"]):
            continue
        row.setdefault("side", "long")
        status, value = performance(row)
        if outcome != "all" and status != outcome:
            continue
        row.update(outcome_status=status, sort_r=value)
        items.append(row)
    stats = summarize(items)
    by_timeframe = [dict(timeframe=tf, **summarize([r for r in items if r["timeframe"] == tf]))
                    for tf in TIMEFRAMES[kind]]
    if sort in ("r_desc", "r_asc"):
        sign = -1 if sort == "r_desc" else 1
        items.sort(key=lambda r: (r["sort_r"] is None, sign * (r["sort_r"] or 0), -r["bar_close_ms"], r["id"]))
    else:
        items.sort(key=lambda r: (r["bar_close_ms"], r["id"]), reverse=sort == "newest")
    return {"items": items[:limit], "total": len(items), "stats": stats, "by_timeframe": by_timeframe,
            "as_of_ms": now_ms, "period": period, "scope": scope,
            "basis": "v11_2_box_joint_next_open_serial_net_of_round_trip_cost"}

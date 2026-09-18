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

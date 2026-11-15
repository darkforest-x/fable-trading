"""Read-only projection of persisted Monday 1.618 touch scores.

The discipline service runs beside the monitor on the same Mac. It reads the
monitor's existing signal ledger directly in SQLite read-only mode so this
authenticated iPhone feed does not need a new monitor route or process restart.
"""
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo


DEFAULT_RUNTIME = Path.home() / "Library/Application Support/Fable/ImpulseMonitor"
DATABASE = "monday-reclaim-paper.sqlite3"
TIMEZONE = ZoneInfo("Asia/Shanghai")
THRESHOLD = 0.30
MAX_LIMIT = 100


class ScoredTouchesUnavailable(RuntimeError):
    """The shared monitor ledger is missing, unreadable, or has an unknown schema."""


def _runtime_path(runtime=None):
    configured = runtime or os.environ.get("FABLE_IMPULSE_RUNTIME") or DEFAULT_RUNTIME
    return Path(configured) / DATABASE


def _day_start_ms(now_ms):
    local = datetime.fromtimestamp(now_ms / 1000, TIMEZONE)
    return int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def read_scored_touches(offset=0, limit=100, *, runtime=None, now_ms=None):
    """Return today's complete set and one older page from the existing signals ledger.

    Touches are selected by the timestamp of the first touched minute candle; the
    score is persisted after that touch's 5m candle closes. Scores are the original
    unrounded `signals.score` values; the threshold is inclusive at 0.30.
    """
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit must be between 1 and 100")

    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    day_start_ms = _day_start_ms(now_ms)
    path = _runtime_path(runtime)
    if not path.is_file():
        raise ScoredTouchesUnavailable("评分历史账本暂不可用")

    # as_uri quotes spaces and other path characters. mode=ro prevents SQLite
    # from creating a missing database and the query-only pragma rejects writes.
    db = None
    try:
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        eligible = ("status='scored' AND score IS NOT NULL AND score>=? AND score<=1 "
                    "AND side IN (-1,1) AND week IS NOT NULL AND symbol IS NOT NULL "
                    "AND candle_ms IS NOT NULL")
        fields = ("id,week,symbol,CASE WHEN side=1 THEN 'lower' ELSE 'upper' END AS side,"
                  "candle_ms,score")
        params = (THRESHOLD,)
        today_total = db.execute(
            f"SELECT COUNT(*) n FROM signals WHERE {eligible} AND candle_ms>=? AND candle_ms<=?",
            (*params, day_start_ms, now_ms),
        ).fetchone()["n"]
        today_rows = [dict(row) for row in db.execute(
            f"SELECT {fields} FROM signals WHERE {eligible} AND candle_ms>=? AND candle_ms<=? "
            "ORDER BY candle_ms DESC,id DESC",
            (*params, day_start_ms, now_ms),
        )]
        history_total = db.execute(
            f"SELECT COUNT(*) n FROM signals WHERE {eligible} AND candle_ms<?",
            (*params, day_start_ms),
        ).fetchone()["n"]
        history_rows = [dict(row) for row in db.execute(
            f"SELECT {fields} FROM signals WHERE {eligible} AND candle_ms<? "
            "ORDER BY candle_ms DESC,id DESC LIMIT ? OFFSET ?",
            (*params, day_start_ms, limit, offset),
        )]
    except (OSError, sqlite3.Error):
        raise ScoredTouchesUnavailable("评分历史账本暂不可用") from None
    finally:
        if db is not None:
            db.close()

    return dict(schema="monday_reclaim_paper.scored_history.v1", now_ms=now_ms,
                timezone="Asia/Shanghai", day_start_ms=day_start_ms, threshold=THRESHOLD,
                today=dict(total=today_total, rows=today_rows),
                history=dict(offset=offset, limit=limit, total=history_total,
                             has_more=offset+len(history_rows) < history_total, rows=history_rows))

"""Calendar and durable ledger for the monthly first-week 1.618 monitor.

The range is Pine ``month_first_week_1618_v1_5.pine``'s first Monday 00:00
through the following Monday 00:00 in New York. H1 range bars are required
to tile that calendar interval exactly; live touches come from the monitor's
existing OKX candle1m stream and are never gated on a later H1 close.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

ZONE_NAME = "America/New_York"
ZONE = ZoneInfo(ZONE_NAME)
UTC = timezone.utc
HOUR_MS = 3_600_000
MINUTE_MS = 60_000
FRESH_MS = 90_000
VERSION = "month_first_week_1618.v1"
EVENT_TYPE = "month_first_week_touch"
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,32}-USDT-SWAP$")


def month_window(stamp_ms: int) -> dict:
    """Return the NY-local first complete Monday week containing ``stamp_ms``.

    Calendar date arithmetic is used before converting to epoch milliseconds,
    so the returned UTC interval can be 167, 168, or 169 hours across DST.
    """
    if type(stamp_ms) is not int or stamp_ms < 0:
        raise ValueError("stamp_ms must be a non-negative integer")
    local = datetime.fromtimestamp(stamp_ms / 1000, UTC).astimezone(ZONE)
    first = date(local.year, local.month, 1)
    first_monday = first + timedelta(days=(7 - first.weekday()) % 7)
    start_local = datetime.combine(first_monday, time.min, ZONE)
    ready_local = datetime.combine(first_monday + timedelta(days=7), time.min, ZONE)
    if local.month == 12:
        next_month = date(local.year + 1, 1, 1)
    else:
        next_month = date(local.year, local.month + 1, 1)
    expiry_local = datetime.combine(next_month, time.min, ZONE)
    start_ms = int(start_local.timestamp() * 1000)
    ready_ms = int(ready_local.timestamp() * 1000)
    expires_ms = int(expiry_local.timestamp() * 1000)
    return {
        "month": f"{local.year:04d}-{local.month:02d}",
        "first_monday": first_monday.isoformat(),
        "start_ms": start_ms,
        "ready_ms": ready_ms,
        "expires_ms": expires_ms,
        "expected_1h_bars": (ready_ms - start_ms) // HOUR_MS,
        "complete": stamp_ms >= ready_ms,
    }


def expected_hour_opens(window: Mapping) -> tuple[int, ...]:
    start_ms, ready_ms = int(window["start_ms"]), int(window["ready_ms"])
    if ready_ms <= start_ms or (ready_ms - start_ms) % HOUR_MS:
        raise ValueError("month week is not aligned to hourly source bars")
    return tuple(range(start_ms, ready_ms, HOUR_MS))


def make_anchor(rows: Iterable[Mapping], window: Mapping, now_ms: int) -> dict:
    """Build an anchor only from the exact complete, confirmed NY-week H1 grid.

    ``rows`` use the monitor client schema (``t``, ``h``, ``l``). No source
    candle ending after ``now_ms`` is admitted.
    """
    expected = expected_hour_opens(window)
    if now_ms < window["ready_ms"]:
        return {**dict(window), "state": "developing", "high": None, "low": None,
                "upper": None, "lower": None, "missing_hours": len(expected), "history_gaps": 0,
                "coverage_complete": False}
    by_time = {}
    for row in rows:
        try:
            stamp = int(row["t"])
            high, low = float(row["h"]), float(row["l"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (stamp not in expected or stamp + HOUR_MS > now_ms or not math.isfinite(high)
                or not math.isfinite(low) or high <= 0 or low <= 0 or high < low):
            continue
        by_time[stamp] = (high, low)
    missing = [stamp for stamp in expected if stamp not in by_time]
    if missing:
        return {**dict(window), "state": "incomplete", "high": None, "low": None,
                "upper": None, "lower": None, "missing_hours": len(missing),
                "first_missing_ms": missing[0], "last_missing_ms": missing[-1],
                "history_gaps": 0, "coverage_complete": False}
    high = max(values[0] for values in by_time.values())
    low = min(values[1] for values in by_time.values())
    if high <= low:
        return {**dict(window), "state": "invalid_range", "high": high, "low": low,
                "upper": None, "lower": None, "missing_hours": 0, "history_gaps": 0,
                "coverage_complete": False}
    span = high - low
    return {**dict(window), "state": "ready", "high": high, "low": low,
            "upper": high + 0.618 * span, "lower": low - 0.618 * span,
            "missing_hours": 0, "history_gaps": 0, "coverage_complete": True}


def closed_hour_touches(rows: Iterable[Mapping], anchor: Mapping, now_ms: int) -> list[dict]:
    """Return the first source-H1 observation of each post-ready touch.

    This is restart/gap recovery only. The caller stores these as ``history``
    events, which are visible in state but can never enter a notification queue.
    """
    if anchor.get("state") not in {"ready", "reconciling", "coverage_incomplete"}:
        return []
    found = {}
    for row in sorted(rows, key=lambda item: int(item.get("t", -1))):
        try:
            stamp = int(row["t"])
            high, low = float(row["h"]), float(row["l"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not anchor["ready_ms"] <= stamp < anchor["expires_ms"] or stamp + HOUR_MS > now_ms:
            continue
        for side, hit, extreme in (
            ("upper", high >= anchor["upper"], high),
            ("lower", low <= anchor["lower"], low),
        ):
            if hit and side not in found:
                found[side] = {"side": side, "candle_ms": stamp,
                               "occurred_ms": stamp, "price": float(anchor[side]),
                               "candle_extreme": extreme}
    return list(found.values())


class MonthFirstWeekBook:
    """Own the strategy's isolated anchors, first-touch ledger and send outbox."""

    def __init__(self, runtime):
        self.path = Path(runtime) / "month-first-week-1618.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS anchors (
                    month TEXT NOT NULL, symbol TEXT NOT NULL, state TEXT NOT NULL,
                    start_ms INTEGER NOT NULL, ready_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
                    high REAL, low REAL, upper REAL, lower REAL,
                    missing_hours INTEGER NOT NULL DEFAULT 0,
                    history_gaps INTEGER NOT NULL DEFAULT 0,
                    coverage_complete INTEGER NOT NULL DEFAULT 0,
                    armed_sides TEXT NOT NULL DEFAULT '[]',
                    updated_ms INTEGER NOT NULL, error TEXT,
                    PRIMARY KEY(month,symbol));
                CREATE TABLE IF NOT EXISTS touches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL,
                    month TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
                    level REAL NOT NULL, price REAL NOT NULL, candle_extreme REAL,
                    occurred_ms INTEGER NOT NULL, candle_ms INTEGER NOT NULL,
                    created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
                    fresh_until_ms INTEGER NOT NULL, source TEXT NOT NULL,
                    touch_confirmed INTEGER NOT NULL,
                    candle_confirmed INTEGER NOT NULL,
                    UNIQUE(month,symbol,side));
                CREATE INDEX IF NOT EXISTS touches_month_idx ON touches(month,id);
                CREATE TABLE IF NOT EXISTS subscriptions (
                    channel TEXT PRIMARY KEY CHECK(channel IN ('telegram','bark')),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    activated_ms INTEGER NOT NULL DEFAULT 0,
                    generation INTEGER NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO subscriptions(channel) VALUES ('telegram'),('bark');
                CREATE TABLE IF NOT EXISTS notices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL, channel TEXT NOT NULL,
                    generation INTEGER NOT NULL, status TEXT NOT NULL,
                    due_ms INTEGER NOT NULL, created_ms INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT, message_id INTEGER, server_timestamp INTEGER,
                    UNIQUE(event_id,channel));
                CREATE INDEX IF NOT EXISTS notices_pending_idx ON notices(channel,status,due_ms,id);
                CREATE TABLE IF NOT EXISTS quote_cursors (
                    symbol TEXT PRIMARY KEY, candle_ms INTEGER NOT NULL, received_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS worker_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), last_cycle_ms INTEGER NOT NULL DEFAULT 0,
                    error TEXT, updated_ms INTEGER NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO worker_state(id) VALUES (1);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(anchors)")}
            if "first_missing_ms" not in columns:
                db.execute("ALTER TABLE anchors ADD COLUMN first_missing_ms INTEGER")
            if "last_missing_ms" not in columns:
                db.execute("ALTER TABLE anchors ADD COLUMN last_missing_ms INTEGER")
            if "armed_sides" not in columns:
                db.execute("ALTER TABLE anchors ADD COLUMN armed_sides TEXT NOT NULL DEFAULT '[]'")
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        """A process death after claiming a send is ambiguous; never resend it."""
        with self.db() as db:
            db.execute("UPDATE notices SET status='unknown',error='interrupted_delivery' WHERE status='sending'")

    def save_anchor(self, anchor: Mapping, symbol: str, now_ms: int, *, state=None,
                    missing_hours=None, history_gaps=None, coverage_complete=None, error=None):
        if not SYMBOL_RE.fullmatch(symbol):
            raise ValueError("invalid OKX swap symbol")
        value = dict(anchor)
        state = state or value.get("state", "incomplete")
        missing_hours = value.get("missing_hours", 0) if missing_hours is None else missing_hours
        history_gaps = value.get("history_gaps", 0) if history_gaps is None else history_gaps
        coverage_complete = value.get("coverage_complete", False) if coverage_complete is None else coverage_complete
        error = error if error is not None else value.get("error") or value.get("recovery_error")
        with self.db() as db:
            self._save_anchor_db(db, value, symbol, now_ms, state=state,
                                 missing_hours=missing_hours, history_gaps=history_gaps,
                                 coverage_complete=coverage_complete, error=error)

    @staticmethod
    def _save_anchor_db(db, value, symbol, now_ms, *, state=None, missing_hours=None,
                        history_gaps=None, coverage_complete=None, error=None):
        state = state or value.get("state", "incomplete")
        missing_hours = value.get("missing_hours", 0) if missing_hours is None else missing_hours
        history_gaps = value.get("history_gaps", 0) if history_gaps is None else history_gaps
        coverage_complete = value.get("coverage_complete", False) if coverage_complete is None else coverage_complete
        error = error if error is not None else value.get("error") or value.get("recovery_error")
        armed_sides = value.get("armed_sides", [])
        if not isinstance(armed_sides, (list, tuple)) or any(side not in {"upper", "lower"} for side in armed_sides):
            raise ValueError("invalid armed sides")
        db.execute("""INSERT INTO anchors(month,symbol,state,start_ms,ready_ms,expires_ms,high,low,upper,lower,
                    missing_hours,history_gaps,coverage_complete,armed_sides,updated_ms,error,first_missing_ms,last_missing_ms)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(month,symbol) DO UPDATE SET state=excluded.state,high=excluded.high,low=excluded.low,
                    upper=excluded.upper,lower=excluded.lower,missing_hours=excluded.missing_hours,
                    history_gaps=excluded.history_gaps,coverage_complete=excluded.coverage_complete,armed_sides=excluded.armed_sides,
                    updated_ms=excluded.updated_ms,error=excluded.error,first_missing_ms=excluded.first_missing_ms,
                    last_missing_ms=excluded.last_missing_ms""",
                   (value["month"], symbol, state, value["start_ms"], value["ready_ms"], value["expires_ms"],
                    value.get("high"), value.get("low"), value.get("upper"), value.get("lower"),
                    int(missing_hours), int(history_gaps), int(bool(coverage_complete)), json.dumps(sorted(set(armed_sides))), now_ms, error,
                    value.get("first_missing_ms"), value.get("last_missing_ms")))

    def anchor(self, month: str, symbol: str):
        with self.db() as db:
            row = db.execute("SELECT * FROM anchors WHERE month=? AND symbol=?", (month, symbol)).fetchone()
            return self._anchor_dict(row) if row else None

    def anchors(self, month: str):
        with self.db() as db:
            return [self._anchor_dict(row) for row in db.execute(
                "SELECT * FROM anchors WHERE month=? ORDER BY symbol", (month,))]

    def chart_ranges(self, symbol: str, now_ms: int):
        """Closed first-week prices are valid even while post-week touches are being recovered."""
        with self.db() as db:
            return [dict(row) for row in db.execute("""
                SELECT month,start_ms,ready_ms,expires_ms,high,low,upper,lower
                FROM anchors WHERE symbol=? AND ready_ms<=?
                  AND high>low AND low>0 AND upper IS NOT NULL AND lower IS NOT NULL
                ORDER BY month
            """, (symbol, now_ms))]

    @staticmethod
    def _anchor_dict(row):
        result = dict(row)
        result["coverage_complete"] = bool(result["coverage_complete"])
        try:
            result["armed_sides"] = json.loads(result["armed_sides"])
        except (TypeError, json.JSONDecodeError):
            result["armed_sides"] = []
        return result

    def mark_reconciling(self, month: str, symbol: str, now_ms: int):
        with self.db() as db:
            db.execute("UPDATE anchors SET state='reconciling',coverage_complete=0,updated_ms=? "
                       "WHERE month=? AND symbol=? AND state IN ('ready','coverage_incomplete','reconciling')",
                       (now_ms, month, symbol))

    def touch(self, *, month: str, symbol: str, side: str, level: float, price: float,
              candle_extreme: float | None, occurred_ms: int, candle_ms: int, created_ms: int,
              expires_ms: int, source: str, candle_confirmed: bool) -> dict | None:
        if not SYMBOL_RE.fullmatch(symbol) or side not in {"upper", "lower"}:
            raise ValueError("invalid touch identity")
        if source not in {"live", "history"}:
            raise ValueError("invalid touch source")
        numbers = (level, price, occurred_ms, candle_ms, created_ms, expires_ms)
        if any(type(value) not in (int, float) or not math.isfinite(float(value)) for value in numbers):
            raise ValueError("invalid touch values")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._insert_touch_db(db, {"month": month, "symbol": symbol, "side": side,
                "level": level, "price": price, "candle_extreme": candle_extreme,
                "occurred_ms": occurred_ms, "candle_ms": candle_ms, "created_ms": created_ms,
                "expires_ms": expires_ms, "source": source,
                "candle_confirmed": candle_confirmed})

    @classmethod
    def _insert_touch_db(cls, db, event):
        event_id = event.get("event_id") or f"{event['month']}|{event['symbol']}|{event['side']}"
        source = event["source"]
        if (not SYMBOL_RE.fullmatch(event["symbol"]) or event["side"] not in {"upper", "lower"}
                or source not in {"live", "history"}):
            raise ValueError("invalid touch identity")
        values = (event["level"], event["price"], event["occurred_ms"], event["candle_ms"],
                  event["created_ms"], event["expires_ms"])
        if any(type(value) not in (int, float) or not math.isfinite(float(value)) for value in values):
            raise ValueError("invalid touch values")
        fresh_until_ms = int(event["occurred_ms"] + FRESH_MS)
        inserted = db.execute("""INSERT OR IGNORE INTO touches
            (event_id,event_type,month,symbol,side,level,price,candle_extreme,occurred_ms,candle_ms,
             created_ms,expires_ms,fresh_until_ms,source,touch_confirmed,candle_confirmed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, event.get("event_type", EVENT_TYPE), event["month"], event["symbol"], event["side"],
             float(event["level"]), float(event["price"]),
             None if event.get("candle_extreme") is None else float(event["candle_extreme"]),
             int(event["occurred_ms"]), int(event["candle_ms"]), int(event["created_ms"]),
             int(event["expires_ms"]), fresh_until_ms, source, int(bool(event.get("touch_confirmed", True))),
             int(bool(event.get("candle_confirmed", False))))).rowcount == 1
        if not inserted:
            return None
        if source == "live":
            for channel in ("telegram", "bark"):
                subscription = db.execute("SELECT * FROM subscriptions WHERE channel=?", (channel,)).fetchone()
                if (subscription and subscription["enabled"] and event["occurred_ms"] > subscription["activated_ms"]
                        and event["created_ms"] <= fresh_until_ms):
                    db.execute("INSERT OR IGNORE INTO notices(event_id,channel,generation,status,due_ms,created_ms) "
                               "VALUES (?,?,?,'pending',?,?)",
                               (event_id, channel, subscription["generation"], event["created_ms"], event["created_ms"]))
        row = db.execute("SELECT * FROM touches WHERE event_id=?", (event_id,)).fetchone()
        return cls._touch_dict(row)

    def commit_cycle(self, *, anchors=(), touches=(), quote_cursors=(), now_ms: int):
        """Persist one listener/worker batch with a single SQLite transaction."""
        inserted = []
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for anchor in anchors:
                self._save_anchor_db(db, anchor, anchor["symbol"], now_ms)
            for cursor in quote_cursors:
                db.execute("INSERT INTO quote_cursors(symbol,candle_ms,received_ms) VALUES (?,?,?) "
                           "ON CONFLICT(symbol) DO UPDATE SET candle_ms=max(candle_ms,excluded.candle_ms),"
                           "received_ms=CASE WHEN excluded.candle_ms>=candle_ms THEN excluded.received_ms ELSE received_ms END",
                           (cursor["symbol"], cursor["candle_ms"], cursor["received_ms"]))
            for event in touches:
                row = self._insert_touch_db(db, event)
                if row:
                    inserted.append(row)
            db.execute("UPDATE worker_state SET last_cycle_ms=?,updated_ms=?,error=NULL WHERE id=1",
                       (now_ms, now_ms))
        return inserted

    def load_state(self, month: str):
        """Load month anchors, consumed first touches and quote watermarks in one read."""
        with self.db() as db:
            anchors = [self._anchor_dict(row) for row in db.execute(
                "SELECT * FROM anchors WHERE month=? ORDER BY symbol", (month,))]
            touches = [self._touch_dict(row) for row in db.execute(
                "SELECT * FROM touches WHERE month=? ORDER BY id", (month,))]
            cursors = [dict(row) for row in db.execute("SELECT * FROM quote_cursors")]
            return {"anchors": anchors, "touches": touches, "quote_cursors": cursors}

    def notice_statuses(self, month: str):
        with self.db() as db:
            result = {}
            for row in db.execute("""SELECT n.event_id,n.channel,n.status,n.error,n.attempts
                FROM notices n JOIN touches t USING(event_id) WHERE t.month=?""", (month,)):
                result.setdefault(row["event_id"], {})[row["channel"]] = {
                    "status": row["status"], "error": row["error"], "attempts": row["attempts"]}
            return result

    @staticmethod
    def _touch_dict(row):
        result = dict(row)
        # ``event_type`` is the persisted SQLite column; the public monitor
        # contract and APNs consumer call this discriminator ``type``.
        result["type"] = result.get("event_type") or EVENT_TYPE
        result["touch_confirmed"] = bool(result["touch_confirmed"])
        result["candle_confirmed"] = bool(result["candle_confirmed"])
        return result

    def events_after(self, after_id: int = 0, limit: int = 500) -> list[dict]:
        """Return a stable ascending cursor page; ``after_id`` is exclusive."""
        if type(after_id) is not int or after_id < 0 or type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("invalid event cursor")
        with self.db() as db:
            rows = db.execute("SELECT * FROM touches WHERE id>? ORDER BY id ASC LIMIT ?", (after_id, limit))
            return [self._touch_dict(row) for row in rows]

    def chart_record(self, event_id: str):
        """Read the saved touch and its own month's anchor, including after rollover."""
        with self.db() as db:
            row = db.execute("""SELECT t.*, a.start_ms, a.ready_ms, a.high, a.low, a.upper, a.lower
                FROM touches t LEFT JOIN anchors a ON a.month=t.month AND a.symbol=t.symbol
                WHERE t.event_id=?""", (event_id,)).fetchone()
            return dict(row) if row else None

    def events(self, month: str | None = None, limit: int | None = None):
        with self.db() as db:
            if month is None:
                sql, params = "SELECT * FROM touches ORDER BY id DESC", ()
            else:
                sql, params = "SELECT * FROM touches WHERE month=? ORDER BY id DESC", (month,)
            if limit is not None:
                sql += " LIMIT ?"
                params += (int(limit),)
            return [self._touch_dict(row) for row in db.execute(sql, params)]

    def latest_event_id(self) -> int:
        with self.db() as db:
            return int(db.execute("SELECT COALESCE(MAX(id),0) FROM touches").fetchone()[0])

    def record_quote(self, symbol: str, candle_ms: int, received_ms: int) -> tuple[bool, dict | None]:
        """Persist one cursor per new minute and detect process/socket gaps."""
        if not SYMBOL_RE.fullmatch(symbol):
            return False, None
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM quote_cursors WHERE symbol=?", (symbol,)).fetchone()
            if prior and candle_ms < prior["candle_ms"]:
                return False, dict(prior)
            previous = dict(prior) if prior else None
            gap = bool(prior and (candle_ms > prior["candle_ms"] + MINUTE_MS
                                  or received_ms - prior["received_ms"] > FRESH_MS))
            if not prior or candle_ms > prior["candle_ms"]:
                db.execute("INSERT INTO quote_cursors(symbol,candle_ms,received_ms) VALUES (?,?,?) "
                           "ON CONFLICT(symbol) DO UPDATE SET candle_ms=excluded.candle_ms,received_ms=excluded.received_ms",
                           (symbol, candle_ms, received_ms))
            elif received_ms - prior["received_ms"] > FRESH_MS:
                # A restarted stream can resume inside the same one-minute bar.
                db.execute("UPDATE quote_cursors SET received_ms=? WHERE symbol=?", (received_ms, symbol))
            return gap, previous

    def set_enabled(self, channel: str, enabled: bool, now_ms: int):
        if channel not in {"telegram", "bark"} or type(enabled) is not bool:
            raise ValueError("invalid channel subscription")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM subscriptions WHERE channel=?", (channel,)).fetchone()
            if bool(row["enabled"]) != enabled:
                generation = row["generation"] + 1
                db.execute("UPDATE subscriptions SET enabled=?,activated_ms=?,generation=? WHERE channel=?",
                           (int(enabled), now_ms, generation, channel))
                db.execute("UPDATE notices SET status='skipped',error='subscription_changed' "
                           "WHERE channel=? AND status='pending'", (channel,))
        return self.channel_status(channel)

    def channel_status(self, channel: str, *, configured: bool | None = None):
        with self.db() as db:
            row = dict(db.execute("SELECT * FROM subscriptions WHERE channel=?", (channel,)).fetchone())
            counts = {r["status"]: r["n"] for r in db.execute(
                "SELECT status,COUNT(*) n FROM notices WHERE channel=? GROUP BY status", (channel,))}
        result = {"enabled": bool(row["enabled"]), "activated_ms": row["activated_ms"],
                  "generation": row["generation"], "counts": counts}
        if configured is not None:
            result["configured"] = bool(configured)
        return result

    def pending_notice(self, channel: str, now_ms: int):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            subscription = db.execute("SELECT * FROM subscriptions WHERE channel=?", (channel,)).fetchone()
            db.execute("""UPDATE notices SET status='skipped',error='expired_or_disabled'
                WHERE channel=? AND status='pending' AND (generation<>? OR ?=0 OR EXISTS
                (SELECT 1 FROM touches t WHERE t.event_id=notices.event_id AND
                 (t.source<>'live' OR t.touch_confirmed<>1 OR t.fresh_until_ms<? OR t.occurred_ms>? OR t.expires_ms<=?)))""",
                       (channel, subscription["generation"], subscription["enabled"], now_ms, now_ms, now_ms))
            row = db.execute("""SELECT n.id AS notice_id,n.event_id,n.channel,n.generation,n.status,
                n.due_ms,n.created_ms,n.attempts,n.error AS notice_error,t.event_type,t.month,t.symbol,t.side,
                t.level,t.price,t.candle_extreme,t.occurred_ms,t.candle_ms,t.expires_ms,t.fresh_until_ms,
                t.source,t.touch_confirmed,t.candle_confirmed
                FROM notices n JOIN touches t USING(event_id)
                WHERE n.channel=? AND n.status='pending' AND n.due_ms<=?
                ORDER BY n.id LIMIT 1""", (channel, now_ms)).fetchone()
            return dict(row) if row else None

    def claim_notice(self, notice_id: int, channel: str, now_ms: int):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            subscription = db.execute("SELECT * FROM subscriptions WHERE channel=?", (channel,)).fetchone()
            row = db.execute("""SELECT n.id AS notice_id,n.event_id,n.channel,n.generation,n.status,
                n.due_ms,n.created_ms,n.attempts,n.error AS notice_error,t.event_type,t.month,t.symbol,t.side,
                t.level,t.price,t.candle_extreme,t.occurred_ms,t.candle_ms,t.expires_ms,t.fresh_until_ms,
                t.source,t.touch_confirmed,t.candle_confirmed
                FROM notices n JOIN touches t USING(event_id) WHERE n.id=? AND n.channel=?""",
                             (notice_id, channel)).fetchone()
            if not row or row["status"] != "pending" or row["due_ms"] > now_ms:
                return None
            if (not subscription or not subscription["enabled"] or row["generation"] != subscription["generation"]
                    or row["source"] != "live" or not row["touch_confirmed"]
                    or row["fresh_until_ms"] < now_ms or row["occurred_ms"] > now_ms
                    or row["expires_ms"] <= now_ms):
                db.execute("UPDATE notices SET status='skipped',error='expired_or_disabled' WHERE id=? AND status='pending'",
                           (notice_id,))
                return None
            cursor = db.execute("UPDATE notices SET status='sending',attempts=attempts+1 WHERE id=? "
                                "AND channel=? AND status='pending'", (notice_id, channel))
            if cursor.rowcount != 1:
                return None
            return {**dict(row), "attempts": row["attempts"] + 1}

    def finish_notice(self, notice_id: int, status: str, *, error=None, message_id=None, server_timestamp=None,
                      due_ms=0, expected="sending"):
        if status not in {"pending", "sent", "failed", "unknown", "skipped"}:
            raise ValueError("invalid notice status")
        with self.db() as db:
            db.execute("UPDATE notices SET status=?,error=?,message_id=?,server_timestamp=?,due_ms=? "
                       "WHERE id=? AND status=?", (status, error, message_id, server_timestamp, due_ms, notice_id, expected))

    def mark_worker(self, now_ms: int, error=None):
        with self.db() as db:
            db.execute("UPDATE worker_state SET last_cycle_ms=?,updated_ms=?,error=? WHERE id=1",
                       (now_ms, now_ms, error))

    def worker_state(self):
        with self.db() as db:
            return dict(db.execute("SELECT * FROM worker_state WHERE id=1").fetchone())


__all__ = ["EVENT_TYPE", "FRESH_MS", "HOUR_MS", "MINUTE_MS", "MonthFirstWeekBook", "VERSION",
           "closed_hour_touches", "expected_hour_opens", "make_anchor", "month_window"]

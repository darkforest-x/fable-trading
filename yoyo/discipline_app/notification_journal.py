"""Durable per-device APNs outbox for month-first-week 1.618 touches.

The monitor owns the immutable market facts and its Telegram/Bark outboxes. This
journal is a read-only consumer of those facts and owns only the iPhone APNs
receipt state. A sender that was interrupted after claiming is marked unknown;
it is never blindly retried.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import hashlib
import math
from pathlib import Path
import re
import sqlite3
import time

MONTH_TOUCH_TYPE = "month_first_week_touch"
MONTH_TOUCH_TTL_MS = 90_000
SYMBOL = re.compile(r"[A-Z0-9]{1,40}-USDT-SWAP")
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def valid_month_touch(event: dict) -> bool:
    """Validate the immutable monthly touch contract before persisting/sending."""
    if not isinstance(event, dict) or event.get("type") != MONTH_TOUCH_TYPE:
        return False
    if type(event.get("id")) is not int or event["id"] <= 0:
        return False
    month, symbol, side = event.get("month"), event.get("symbol"), event.get("side")
    if not isinstance(month, str) or not MONTH.fullmatch(month):
        return False
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or side not in ("upper", "lower"):
        return False
    if event.get("event_id") != f"{month}|{symbol}|{side}":
        return False
    if event.get("source") not in ("live", "history") or event.get("touch_confirmed") is not True:
        return False
    if type(event.get("candle_confirmed")) is not bool:
        return False
    timestamps = ("occurred_ms", "candle_ms", "created_ms", "expires_ms", "fresh_until_ms")
    if any(type(event.get(key)) is not int or event[key] <= 0 for key in timestamps):
        return False
    if event["fresh_until_ms"] < event["occurred_ms"] or event["fresh_until_ms"] - event["occurred_ms"] > MONTH_TOUCH_TTL_MS:
        return False
    for key in ("level", "price", "candle_extreme"):
        value = event.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            return False
    return True


class NotificationJournal:
    """SQLite cursor, immutable event copy, and independent APNs delivery rows."""

    def __init__(self, path, *, started_ms=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.started_ms = int(started_ms if started_ms is not None else time.time() * 1000)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS month_touch_events(
                    source_id INTEGER PRIMARY KEY,event_id TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,
                    source TEXT NOT NULL,occurred_ms INTEGER NOT NULL,created_ms INTEGER NOT NULL,
                    expires_ms INTEGER NOT NULL,fresh_until_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS month_touch_apns(
                    source_id INTEGER NOT NULL,device_hash TEXT NOT NULL,status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,updated_ms INTEGER NOT NULL,
                    due_ms INTEGER NOT NULL,error TEXT,
                    PRIMARY KEY(source_id,device_hash),
                    FOREIGN KEY(source_id) REFERENCES month_touch_events(source_id));
                CREATE INDEX IF NOT EXISTS month_touch_apns_due ON month_touch_apns(status,due_ms);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _json(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    def cursor(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='month_touch_cursor'").fetchone()
        return int(row[0]) if row else None

    def seed_cursor(self, events):
        """On a new install, skip facts that existed when the app service started."""
        existing = self.cursor()
        if existing is not None:
            return existing
        before_start = [e["id"] for e in events if valid_month_touch(e) and e["created_ms"] <= self.started_ms]
        value = max(before_start, default=0)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('month_touch_cursor',?)", (str(value),))
            row = db.execute("SELECT value FROM meta WHERE key='month_touch_cursor'").fetchone()
        return int(row[0])

    def ingest(self, events, devices, now_ms):
        """Atomically persist new facts, enqueue opted-in phones, and advance source cursor."""
        rows = [e for e in events if valid_month_touch(e)]
        rows.sort(key=lambda e: e["id"])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("SELECT value FROM meta WHERE key='month_touch_cursor'").fetchone()
            if cursor is None:
                # Caller must first seed against the snapshot at service startup.
                return 0
            current = int(cursor[0])
            latest = current
            inserted = 0
            for event in rows:
                source_id = event["id"]
                if source_id <= current:
                    continue
                latest = max(latest, source_id)
                db.execute("INSERT OR IGNORE INTO month_touch_events(source_id,event_id,payload,source,occurred_ms,created_ms,expires_ms,fresh_until_ms) "
                           "VALUES (?,?,?,?,?,?,?,?)", (source_id, event["event_id"], self._json(event), event["source"],
                                                        event["occurred_ms"], event["created_ms"], event["expires_ms"], event["fresh_until_ms"]))
                if (event["source"] != "live" or now_ms > event["fresh_until_ms"] or now_ms < event["occurred_ms"]):
                    continue
                inserted += 1
                for device in devices:
                    if not device.get("month_first_week_touch", True):
                        continue
                    token = device.get("token")
                    if not isinstance(token, str):
                        continue
                    device_hash = hashlib.sha256(token.encode()).hexdigest()
                    db.execute("INSERT OR IGNORE INTO month_touch_apns(source_id,device_hash,status,due_ms,updated_ms) "
                               "VALUES (?,?,'pending',?,?)", (source_id, device_hash, now_ms, now_ms))
            if rows:
                latest = max(latest, rows[-1]["id"])
            db.execute("UPDATE meta SET value=? WHERE key='month_touch_cursor'", (str(latest),))
            return inserted

    def recover(self):
        """Ambiguous sends are terminal; no automatic duplicate notification after restart."""
        with self.connect() as db:
            db.execute("UPDATE month_touch_apns SET status='unknown',error='process_interrupted_during_delivery',updated_ms=? WHERE status='sending'",
                       (int(time.time() * 1000),))

    def claim(self, now_ms):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE month_touch_apns SET status='skipped',error='signal_expired',updated_ms=? "
                       "WHERE status='pending' AND source_id IN (SELECT source_id FROM month_touch_events WHERE fresh_until_ms<?)", (now_ms, now_ms))
            row = db.execute("""SELECT d.*,e.payload,e.event_id,e.fresh_until_ms
                FROM month_touch_apns d JOIN month_touch_events e USING(source_id)
                WHERE d.status='pending' AND d.due_ms<=? AND e.fresh_until_ms>=?
                ORDER BY d.due_ms,e.source_id LIMIT 1""", (now_ms, now_ms)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE month_touch_apns SET status='sending',attempts=attempts+1,updated_ms=? WHERE source_id=? AND device_hash=?",
                       (now_ms, row["source_id"], row["device_hash"]))
            result = dict(row)
            result["event"] = json.loads(result.pop("payload"))
            result["attempts"] += 1
            return result

    def finish(self, source_id, device_hash, status, *, error=None, due_ms=0, now_ms=None):
        if status not in {"pending", "sent", "failed", "unknown", "skipped"}:
            raise ValueError("invalid notification status")
        with self.connect() as db:
            db.execute("UPDATE month_touch_apns SET status=?,error=?,due_ms=?,updated_ms=? WHERE source_id=? AND device_hash=?",
                       (status, error, due_ms, int(now_ms if now_ms is not None else time.time() * 1000), source_id, device_hash))

    def skip_pending_for_device(self, token, *, now_ms=None):
        device_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            db.execute("UPDATE month_touch_apns SET status='skipped',error='device_preference_disabled',updated_ms=? WHERE device_hash=? AND status='pending'",
                       (int(now_ms if now_ms is not None else time.time() * 1000), device_hash))

    def delivery_status(self, event_id):
        return self.delivery_statuses([event_id]).get(event_id, {"apns": "not_subscribed", "apns_counts": {}})

    def delivery_statuses(self, event_ids):
        """Aggregate every requested event in bounded queries on one SQLite connection."""
        ids = list(dict.fromkeys(value for value in event_ids if isinstance(value, str) and value))
        result = {event_id: {"apns": "not_subscribed", "apns_counts": {}} for event_id in ids}
        if not ids:
            return result
        counts_by_event = {event_id: {} for event_id in ids}
        with self.connect() as db:
            for offset in range(0, len(ids), 900):
                batch = ids[offset:offset + 900]
                placeholders = ",".join("?" for _ in batch)
                rows = db.execute(
                    "SELECT e.event_id,d.status,COUNT(*) AS n FROM month_touch_apns d "
                    "JOIN month_touch_events e USING(source_id) "
                    f"WHERE e.event_id IN ({placeholders}) GROUP BY e.event_id,d.status", batch).fetchall()
                for row in rows:
                    counts_by_event[row["event_id"]][row["status"]] = row["n"]
        for event_id, counts in counts_by_event.items():
            if counts.get("sent"):
                status = "sent"
            elif counts.get("pending") or counts.get("sending"):
                status = "pending"
            elif counts.get("unknown"):
                status = "unknown"
            elif counts.get("failed"):
                status = "failed"
            elif counts.get("skipped"):
                status = "skipped"
            else:
                status = "not_subscribed"
            result[event_id] = {"apns": status, "apns_counts": counts}
        return result

    def status(self):
        with self.connect() as db:
            counts = {row["status"]: row["count"] for row in db.execute("SELECT status,COUNT(*) AS count FROM month_touch_apns GROUP BY status")}
            cursor = db.execute("SELECT value FROM meta WHERE key='month_touch_cursor'").fetchone()
        return {"counts": counts, "cursor": int(cursor[0]) if cursor else None}

"""SQLite event journal and transactional Telegram outbox for the local monitor.

Only derived observations are persisted; public OHLCV stays in memory. Signal
identity is the immutable protocol/instrument/timeframe/close/kind/direction.
An uncertain Telegram delivery is never silently retried as a new message.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import sqlite3
import time
from pathlib import Path


def now_ms():
    return int(time.time() * 1000)


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                kind TEXT NOT NULL, side TEXT NOT NULL, close_ms INTEGER NOT NULL,
                detected_ms INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS event_time ON events(close_ms DESC);
            CREATE TABLE IF NOT EXISTS markets (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(symbol,timeframe));
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (
                event_id TEXT PRIMARY KEY REFERENCES events(id), status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, due_ms INTEGER NOT NULL,
                updated_ms INTEGER NOT NULL, message_id INTEGER, error TEXT);
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

    def upsert_event(self, event, notify=False):
        e = dict(event)
        key = "|".join(str(e[k]) for k in ("protocol", "symbol", "timeframe", "bar_close_ms", "kind", "side"))
        e["id"] = hashlib.sha256(key.encode()).hexdigest()[:24]
        e.setdefault("detected_at_ms", now_ms())
        with self.connect() as db:
            cur = db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?)", (
                e["id"], e["symbol"], e["timeframe"], e["kind"], e["side"],
                e["bar_close_ms"], e["detected_at_ms"], encode(e)))
            inserted = cur.rowcount == 1
            if inserted and notify:
                db.execute("INSERT INTO outbox(event_id,status,due_ms,updated_ms) VALUES (?,?,?,?)",
                           (e["id"], "pending", e["detected_at_ms"], e["detected_at_ms"]))
        return inserted

    def upsert_market(self, row):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO markets VALUES (?,?,?)",
                       (row["symbol"], row["timeframe"], encode(row)))

    def list_markets(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM markets ORDER BY symbol,timeframe")]

    def list_events(self, limit=200, symbol=None, timeframe=None, kind=None, side=None):
        filters, values = [], []
        for field, value in (("symbol", symbol), ("timeframe", timeframe), ("kind", kind), ("side", side)):
            if value:
                filters.append("e." + field + "=?")
                values.append(value)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        sql = "SELECT e.payload,o.status FROM events e LEFT JOIN outbox o ON e.id=o.event_id" + where
        sql += " ORDER BY e.close_ms DESC,e.symbol,e.kind LIMIT ?"
        values.append(min(2000, max(1, int(limit))))
        with self.connect() as db:
            return [dict(json.loads(r[0]), notification_status=r[1] or "history") for r in db.execute(sql, values)]

    def event_count(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def count_since(self, since):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM events WHERE close_ms>=? AND kind IN ('entry','release')", (since,)).fetchone()[0]

    def set_meta(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, encode(value)))

    def get_meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def recover_outbox(self):
        with self.connect() as db:
            db.execute("UPDATE outbox SET status='unknown',error='process_interrupted_during_delivery' WHERE status='sending'")

    def claim(self, now):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT o.*,e.payload FROM outbox o JOIN events e ON e.id=o.event_id WHERE o.status='pending' AND o.due_ms<=? ORDER BY o.due_ms LIMIT 1", (now,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE outbox SET status='sending',attempts=attempts+1,updated_ms=? WHERE event_id=?", (now, row["event_id"]))
            return dict(row, event=json.loads(row["payload"]), attempts=row["attempts"] + 1)

    def finish(self, event_id, status, *, error=None, message_id=None, due_ms=0):
        with self.connect() as db:
            db.execute("UPDATE outbox SET status=?,error=?,message_id=?,due_ms=?,updated_ms=? WHERE event_id=?",
                       (status, error, message_id, due_ms, now_ms(), event_id))

    def telegram_status(self):
        with self.connect() as db:
            counts = {r[0]: r[1] for r in db.execute("SELECT status,COUNT(*) FROM outbox GROUP BY status")}
            sent = db.execute("SELECT MAX(updated_ms) FROM outbox WHERE status='sent'").fetchone()[0]
        return dict(counts, pending=counts.get("pending", 0), failed=counts.get("failed", 0),
                    unknown=counts.get("unknown", 0), last_success_ms=sent)

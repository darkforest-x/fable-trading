"""SQLite event journal and independent notification outboxes for the monitor.

Only derived observations are persisted; public OHLCV stays in memory. Signal
identity is the immutable protocol/instrument/timeframe/close/kind/direction.
Telegram and Bark receipts, retries and policy cutovers never consume each
other. Outboxes are seeded only in the transaction inserting a new event;
enabling a channel cannot silently replay an existing observation. An uncertain
delivery is never silently retried as a new message.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import sqlite3
import time
from pathlib import Path

from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL, MONITORED_TIMEFRAMES


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
            CREATE TABLE IF NOT EXISTS bark_outbox (
                event_id TEXT PRIMARY KEY REFERENCES events(id), status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, due_ms INTEGER NOT NULL,
                updated_ms INTEGER NOT NULL, error TEXT, server_timestamp INTEGER);
            CREATE TABLE IF NOT EXISTS telegram_media (
                event_id TEXT PRIMARY KEY REFERENCES events(id), png BLOB,
                sha256 TEXT, error TEXT);
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
    def event_id(event):
        key = "|".join(str(event[k]) for k in ("protocol", "symbol", "timeframe", "bar_close_ms", "kind", "side"))
        return hashlib.sha256(key.encode()).hexdigest()[:24]

    def has_event(self, event):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM events WHERE id=?", (self.event_id(event),)).fetchone() is not None

    def upsert_event(self, event, notify=False, bark_notify=False, telegram_photo=None, photo_error=None):
        e = dict(event)
        e["id"] = self.event_id(e)
        if telegram_photo is not None and (not isinstance(telegram_photo, bytes)
                or not telegram_photo.startswith(b'\x89PNG\r\n\x1a\n') or len(telegram_photo) > 9_000_000):
            raise ValueError("invalid_telegram_photo")
        e.setdefault("detected_at_ms", now_ms())
        with self.connect() as db:
            cur = db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?)", (
                e["id"], e["symbol"], e["timeframe"], e["kind"], e["side"],
                e["bar_close_ms"], e["detected_at_ms"], encode(e)))
            inserted = cur.rowcount == 1
            if inserted and notify:
                db.execute("INSERT INTO outbox(event_id,status,due_ms,updated_ms) VALUES (?,?,?,?)",
                           (e["id"], "pending", e["detected_at_ms"], e["detected_at_ms"]))
                if telegram_photo is not None or photo_error:
                    db.execute("INSERT INTO telegram_media VALUES (?,?,?,?)",
                               (e["id"], telegram_photo,
                                hashlib.sha256(telegram_photo).hexdigest() if telegram_photo else None,
                                "snapshot_unavailable" if photo_error else None))
            if inserted and bark_notify:
                db.execute("INSERT INTO bark_outbox(event_id,status,due_ms,updated_ms) VALUES (?,?,?,?)",
                           (e["id"], "pending", e["detected_at_ms"], e["detected_at_ms"]))
        return inserted

    def upsert_market(self, row):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO markets VALUES (?,?,?)",
                       (row["symbol"], row["timeframe"], encode(row)))

    def list_markets(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM markets ORDER BY symbol,timeframe")]

    def list_events(self, limit=200, symbol=None, timeframe=None, kind=None, side=None, protocol=None):
        filters, values = [], []
        for field, value in (("symbol", symbol), ("timeframe", timeframe), ("kind", kind), ("side", side)):
            if value:
                filters.append("e." + field + "=?")
                values.append(value)
        if protocol:
            filters.append("json_extract(e.payload,'$.protocol')=?")
            values.append(protocol)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        sql = ("SELECT e.payload,o.status,b.status FROM events e "
               "LEFT JOIN outbox o ON e.id=o.event_id "
               "LEFT JOIN bark_outbox b ON e.id=b.event_id") + where
        sql += " ORDER BY e.close_ms DESC,e.symbol,e.kind LIMIT ?"
        values.append(min(2000, max(1, int(limit))))
        with self.connect() as db:
            return [dict(json.loads(r[0]), notification_status=r[1] or "history",
                         bark_notification_status=r[2] or "history") for r in db.execute(sql, values)]

    def event_count(self, kind=None, protocol=None):
        filters, values = [], []
        if kind:
            filters.append("kind=?")
            values.append(kind)
        if protocol:
            filters.append("json_extract(payload,'$.protocol')=?")
            values.append(protocol)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM events" + where, values).fetchone()[0]

    def count_since(self, since):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM events WHERE close_ms>=? AND kind=? AND json_extract(payload,'$.protocol')=?",
                              (since, SIGNAL_KIND, SIGNAL_PROTOCOL)).fetchone()[0]

    def activate_notification_policy(self, activated_ms):
        """Once per protocol, set a forward-only cutover; preserve old receipts.

        Called only while the service owns its process lock. Historical
        reconstruction under a new identity must not resend pre-cutover bars.
        Old pending messages are retired, never deleted or reclassified sent.
        """
        key = "notification_policy:" + SIGNAL_PROTOCOL
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)",
                       (key, encode({"activated_ms": activated_ms, "kind": SIGNAL_KIND})))
            db.execute("""UPDATE outbox SET status='skipped',error='notification_policy_replaced',updated_ms=?
                WHERE status='pending' AND event_id IN (
                    SELECT id FROM events WHERE kind!=? OR COALESCE(json_extract(payload,'$.protocol'),'')!=?)""",
                       (activated_ms, SIGNAL_KIND, SIGNAL_PROTOCOL))
            return json.loads(db.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()[0])["activated_ms"]

    def activate_bark_policy(self, activated_ms):
        """Persist Bark's first cutover independently of Telegram's activation.

        As with the Telegram policy, callers enforce this cutover before
        delivery. Retire only obsolete pending Bark items, preserving all
        receipts and every Telegram queue state.
        """
        key = "notification_policy:bark:" + SIGNAL_PROTOCOL
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)",
                       (key, encode({"activated_ms": activated_ms, "kind": SIGNAL_KIND})))
            db.execute("""UPDATE bark_outbox SET status='skipped',error='notification_policy_replaced',updated_ms=?
                WHERE status='pending' AND event_id IN (
                    SELECT id FROM events WHERE kind!=? OR COALESCE(json_extract(payload,'$.protocol'),'')!=?)""",
                       (activated_ms, SIGNAL_KIND, SIGNAL_PROTOCOL))
            return json.loads(db.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()[0])["activated_ms"]

    def timeframe_activation(self, timeframe):
        """Legacy streams retain channel cutovers; new streams fail closed."""
        if timeframe not in MONITORED_TIMEFRAMES:
            return None
        policy = self.get_meta("notification_timeframe:" + SIGNAL_PROTOCOL + ":" + timeframe)
        if policy is None:
            return 0 if timeframe in ("1H", "4H") else None
        value = policy.get("activated_ms")
        return value if type(value) is int and value >= 0 else None

    def activate_timeframe_policy(self, timeframe, activated_ms):
        """Persist a new stream's cutover once, independent of each channel.

        Old 1H/4H queues keep their existing per-channel policy. A newly
        enabled timeframe must not replay recent pre-enablement candles.
        """
        if timeframe not in MONITORED_TIMEFRAMES:
            raise ValueError("unsupported timeframe")
        key = "notification_timeframe:" + SIGNAL_PROTOCOL + ":" + timeframe
        baseline = 0 if timeframe in ("1H", "4H") else activated_ms
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)",
                       (key, encode({"activated_ms": baseline})))
        return self.timeframe_activation(timeframe)

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
            db.execute("UPDATE bark_outbox SET status='unknown',error='process_interrupted_during_delivery' WHERE status='sending'")

    def claim(self, now):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT o.*,e.payload,m.png,m.sha256 AS photo_sha256 FROM outbox o JOIN events e ON e.id=o.event_id LEFT JOIN telegram_media m ON m.event_id=e.id WHERE o.status='pending' AND o.due_ms<=? ORDER BY o.due_ms LIMIT 1", (now,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE outbox SET status='sending',attempts=attempts+1,updated_ms=? WHERE event_id=?", (now, row["event_id"]))
            return dict(row, event=json.loads(row["payload"]), attempts=row["attempts"] + 1)

    def finish(self, event_id, status, *, error=None, message_id=None, due_ms=0):
        with self.connect() as db:
            db.execute("UPDATE outbox SET status=?,error=?,message_id=?,due_ms=?,updated_ms=? WHERE event_id=?",
                       (status, error, message_id, due_ms, now_ms(), event_id))

    def telegram_status(self, protocol=None):
        where = " WHERE json_extract(e.payload,'$.protocol')=?" if protocol else ""
        values = (protocol,) if protocol else ()
        with self.connect() as db:
            counts = {r[0]: r[1] for r in db.execute("SELECT o.status,COUNT(*) FROM outbox o JOIN events e ON e.id=o.event_id" + where + " GROUP BY o.status", values)}
            sent = db.execute("SELECT MAX(o.updated_ms) FROM outbox o JOIN events e ON e.id=o.event_id" + where + (" AND" if where else " WHERE") + " o.status='sent'", values).fetchone()[0]
        return dict(counts, pending=counts.get("pending", 0), failed=counts.get("failed", 0),
                    unknown=counts.get("unknown", 0), last_success_ms=sent)

    def telegram_media_status(self, protocol=None):
        where = " WHERE json_extract(e.payload,'$.protocol')=?" if protocol else ""
        with self.connect() as db:
            row = db.execute("SELECT COUNT(m.png),SUM(CASE WHEN m.error IS NOT NULL THEN 1 ELSE 0 END) FROM telegram_media m JOIN events e ON e.id=m.event_id" + where,
                             (protocol,) if protocol else ()).fetchone()
        return {"snapshots": row[0], "render_fallbacks": row[1] or 0}

    def claim_bark(self, now):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT b.*,e.payload FROM bark_outbox b JOIN events e ON e.id=b.event_id WHERE b.status='pending' AND b.due_ms<=? ORDER BY b.due_ms LIMIT 1", (now,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE bark_outbox SET status='sending',attempts=attempts+1,updated_ms=? WHERE event_id=?", (now, row["event_id"]))
            return dict(row, event=json.loads(row["payload"]), attempts=row["attempts"] + 1)

    def finish_bark(self, event_id, status, *, error=None, server_timestamp=None, due_ms=0):
        with self.connect() as db:
            db.execute("UPDATE bark_outbox SET status=?,error=?,server_timestamp=?,due_ms=?,updated_ms=? WHERE event_id=?",
                       (status, error, server_timestamp, due_ms, now_ms(), event_id))

    def bark_status(self, protocol=None):
        where = " WHERE json_extract(e.payload,'$.protocol')=?" if protocol else ""
        values = (protocol,) if protocol else ()
        with self.connect() as db:
            counts = {r[0]: r[1] for r in db.execute("SELECT b.status,COUNT(*) FROM bark_outbox b JOIN events e ON e.id=b.event_id" + where + " GROUP BY b.status", values)}
            sent = db.execute("SELECT MAX(b.updated_ms) FROM bark_outbox b JOIN events e ON e.id=b.event_id" + where + (" AND" if where else " WHERE") + " b.status='sent'", values).fetchone()[0]
        return dict(counts, pending=counts.get("pending", 0), failed=counts.get("failed", 0),
                    unknown=counts.get("unknown", 0), last_success_ms=sent)

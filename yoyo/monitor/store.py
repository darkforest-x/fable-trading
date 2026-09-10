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
import gzip
import json
import sqlite3
import time
from pathlib import Path

from yoyo.monitor import (SIGNAL_KIND, SIGNAL_PROTOCOL, MONITORED_TIMEFRAMES, MODEL_PROTOCOL, MODEL_KIND, FRESH_MS,
                          DIRECT_POLICY, DIRECT_TIMEFRAMES, BARK_TIMEFRAMES)


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
            -- Health and status may only need counts.  Keep those requests on
            -- indexed columns rather than decoding every historic payload.
            CREATE INDEX IF NOT EXISTS event_protocol_kind_close ON events(
                kind, json_extract(payload, '$.protocol'), close_ms DESC);
            CREATE TABLE IF NOT EXISTS model_candidates (
                id TEXT PRIMARY KEY REFERENCES events(id), symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL, close_ms INTEGER NOT NULL,
                status TEXT NOT NULL, model TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS candidate_state ON model_candidates(status,symbol,timeframe);
            CREATE TABLE IF NOT EXISTS markets (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(symbol,timeframe));
            -- The isolated scanner owns this private, complete recurrence
            -- seed.  UI chart rows are intentionally shorter and cannot be
            -- used to restore frozen V1 feature state after a process restart.
            CREATE TABLE IF NOT EXISTS candle_checkpoints (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, payload BLOB NOT NULL,
                updated_ms INTEGER NOT NULL, PRIMARY KEY(symbol,timeframe));
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
        # A historical replay can describe the same source bar as a live
        # observation.  Its identity must remain separate so importing it
        # never consumes, overwrites, or creates a receipt for the live leg.
        key = "|".join(str(event.get(k, "")) for k in
                       ("protocol", "source", "confirmation", "symbol", "timeframe", "bar_close_ms", "kind", "side"))
        if event.get("kind") == MODEL_KIND:
            key += "|" + str(event.get("source_event_id", Store.event_id(event["indicator"])))
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
            return self._insert_event(db, e, notify, bark_notify, telegram_photo, photo_error)

    def update_event_payload(self, event_id, updates):
        """Merge audited display metadata into one existing journal event.

        Historical reconciliation must never use ``upsert_event``: event
        insertion is intentionally immutable and the only path that can seed
        an outbox.  This narrow update preserves the event identity, receipt
        tables and candidate tables while allowing a separately audited,
        read-only provenance attachment.
        """
        if not isinstance(event_id, str) or not event_id or not isinstance(updates, dict):
            raise ValueError("invalid event payload update")
        with self.connect() as db:
            row = db.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()
            if row is None:
                return False
            event = json.loads(row[0])
            if event.get("id") != event_id:
                raise ValueError("event payload identity mismatch")
            event.update(updates)
            return db.execute("UPDATE events SET payload=? WHERE id=?", (encode(event), event_id)).rowcount == 1

    def save_candle_checkpoint(self, symbol, timeframe, candles):
        """Persist the scanner's full raw recurrence seed, compressed locally."""
        if not isinstance(symbol, str) or not isinstance(timeframe, str) or not isinstance(candles, list):
            raise ValueError("invalid candle checkpoint")
        payload = gzip.compress(encode(candles).encode("utf-8"), mtime=0)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO candle_checkpoints VALUES (?,?,?,?)",
                       (symbol, timeframe, payload, now_ms()))

    def load_candle_checkpoints(self):
        """Return raw checkpoint payloads; malformed entries are explicit ``None``."""
        with self.connect() as db:
            rows = db.execute("SELECT symbol,timeframe,payload FROM candle_checkpoints").fetchall()
        result = {}
        for row in rows:
            try:
                value = json.loads(gzip.decompress(row[2]).decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
                value = None
            result[(row[0], row[1])] = value
        return result

    @staticmethod
    def _insert_event(db, e, notify, bark_notify, telegram_photo=None, photo_error=None):
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

    def register_candidate(self, event, model):
        """Journal the immutable original arrow; registration never sends it."""
        self.upsert_event(event)
        with self.connect() as db:
            return db.execute("INSERT OR IGNORE INTO model_candidates VALUES (?,?,?,?,?,?)",
                              (self.event_id(event), event["symbol"], event["timeframe"],
                               event["bar_close_ms"], "pending", encode(model))).rowcount == 1

    def list_candidates(self, limit=2000, symbol=None, timeframe=None, pending_only=False):
        filters, values = [], []
        for field, value in (("symbol", symbol), ("timeframe", timeframe)):
            if value:
                filters.append("c." + field + "=?")
                values.append(value)
        if pending_only:
            filters.append("c.status IN ('pending','error')")
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with self.connect() as db:
            rows = db.execute("SELECT e.payload,c.model FROM model_candidates c JOIN events e ON e.id=c.id" +
                              where + " ORDER BY c.close_ms DESC,c.id LIMIT ?",
                              values + [min(2000, max(1, int(limit)))]).fetchall()
        return [dict(json.loads(r[0]), model=json.loads(r[1])) for r in rows]

    def candidate_counts(self):
        with self.connect() as db:
            return {r[0]: r[1] for r in db.execute("SELECT status,COUNT(*) FROM model_candidates GROUP BY status")}

    def update_candidate(self, candidate_id, model):
        with self.connect() as db:
            return db.execute("UPDATE model_candidates SET status=?,model=? WHERE id=? AND status IN ('pending','error')",
                              (model["status"], encode(model), candidate_id)).rowcount == 1

    def confirm_candidate(self, candidate_id, event, notify=False, bark_notify=False,
                          telegram_photo=None, photo_error=None):
        """Commit the terminal candidate, derived event and both outboxes atomically."""
        from yoyo.monitor.policy import is_model_signal
        if (not is_model_signal(event) or self.event_id(event["indicator"]) != candidate_id
                or event.get("source_event_id", candidate_id) != candidate_id):
            raise ValueError("invalid_model_confirmation")
        if telegram_photo is not None and (not isinstance(telegram_photo, bytes)
                or not telegram_photo.startswith(b'\x89PNG\r\n\x1a\n') or len(telegram_photo) > 9_000_000):
            raise ValueError("invalid_telegram_photo")
        e = dict(event, id=self.event_id(event))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE model_candidates SET status='confirmed',model=? WHERE id=? AND status IN ('pending','error')",
                                 (encode(e["model"]), candidate_id)).rowcount
            if not changed:
                return False
            return self._insert_event(db, e, notify, bark_notify, telegram_photo, photo_error)

    def upsert_market(self, row):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO markets VALUES (?,?,?)",
                       (row["symbol"], row["timeframe"], encode(row)))

    def list_markets(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM markets ORDER BY symbol,timeframe")]

    def list_market_summaries(self):
        """Read card state without decoding each persisted chart or event list.

        The market overview needs only its compact state.  Charts remain in the
        individual row for ``get_market`` and the selected-chart endpoint.
        """
        sql = "SELECT json_remove(payload, '$.chart', '$.events') FROM markets ORDER BY symbol,timeframe"
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(sql)]

    def get_market(self, symbol, timeframe):
        """Read one persisted chart for the separate causal YOLO worker."""
        with self.connect() as db:
            row = db.execute("SELECT payload FROM markets WHERE symbol=? AND timeframe=?", (symbol, timeframe)).fetchone()
        return json.loads(row[0]) if row else None

    def list_events(self, limit=200, symbol=None, timeframe=None, kind=None, side=None, protocol=None,
                    source=None, confirmation=None, *, direct_only=False):
        filters, values = [], []
        for field, value in (("symbol", symbol), ("timeframe", timeframe), ("kind", kind), ("side", side)):
            if value:
                filters.append("e." + field + "=?")
                values.append(value)
        if protocol:
            filters.append("json_extract(e.payload,'$.protocol')=?")
            values.append(protocol)
        if source:
            filters.append("json_extract(e.payload,'$.source')=?")
            values.append(source)
        if confirmation:
            filters.append("json_extract(e.payload,'$.confirmation')=?")
            values.append(confirmation)
        if direct_only:
            clause, args = self._direct_filter()
            filters.append(clause)
            values.extend(args)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        sql = ("SELECT e.payload,o.status,b.status FROM events e "
               "LEFT JOIN outbox o ON e.id=o.event_id "
               "LEFT JOIN bark_outbox b ON e.id=b.event_id") + where
        sql += " ORDER BY e.close_ms DESC,e.symbol,e.kind LIMIT ?"
        values.append(min(2000, max(1, int(limit))))
        with self.connect() as db:
            return [dict(json.loads(r[0]), notification_status=r[1] or "history",
                         bark_notification_status=r[2] or "history") for r in db.execute(sql, values)]

    def get_event(self, event_id):
        """Read one journaled event for an API that derives no client-supplied identity."""
        with self.connect() as db:
            row = db.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(json.loads(row[0]), id=event_id) if row else None

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

    def count_since(self, since, kind=SIGNAL_KIND, protocol=SIGNAL_PROTOCOL):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM events WHERE close_ms>=? AND kind=? AND json_extract(payload,'$.protocol')=?",
                              (since, kind, protocol)).fetchone()[0]

    def activate_notification_policy(self, activated_ms, protocol=SIGNAL_PROTOCOL, kind=None, *, retire_obsolete=True):
        """Once per protocol, set a forward-only cutover; preserve old receipts.

        Called only while the service owns its process lock. Historical
        reconstruction under a new identity must not resend pre-cutover bars.
        Old pending messages are retired, never deleted or reclassified sent.
        """
        kind = kind or (MODEL_KIND if protocol == MODEL_PROTOCOL else SIGNAL_KIND)
        key = "notification_policy:" + protocol
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)",
                       (key, encode({"activated_ms": activated_ms, "kind": kind})))
            if retire_obsolete:
                db.execute("""UPDATE outbox SET status='skipped',error='notification_policy_replaced',updated_ms=?
                    WHERE status='pending' AND event_id IN (
                        SELECT id FROM events WHERE kind!=? OR COALESCE(json_extract(payload,'$.protocol'),'')!=?)""",
                           (activated_ms, kind, protocol))
            return json.loads(db.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()[0])["activated_ms"]

    def activate_bark_policy(self, activated_ms, protocol=SIGNAL_PROTOCOL, kind=None, *, retire_obsolete=True):
        """Persist Bark's first cutover independently of Telegram's activation.

        As with the Telegram policy, callers enforce this cutover before
        delivery. Retire only obsolete pending Bark items, preserving all
        receipts and every Telegram queue state.
        """
        kind = kind or (MODEL_KIND if protocol == MODEL_PROTOCOL else SIGNAL_KIND)
        key = "notification_policy:bark:" + protocol
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)",
                       (key, encode({"activated_ms": activated_ms, "kind": kind})))
            if retire_obsolete:
                db.execute("""UPDATE bark_outbox SET status='skipped',error='notification_policy_replaced',updated_ms=?
                    WHERE status='pending' AND event_id IN (
                        SELECT id FROM events WHERE kind!=? OR COALESCE(json_extract(payload,'$.protocol'),'')!=?)""",
                           (activated_ms, kind, protocol))
            return json.loads(db.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()[0])["activated_ms"]

    def timeframe_activation(self, timeframe, protocol=SIGNAL_PROTOCOL):
        """Legacy streams retain channel cutovers; new streams fail closed."""
        if timeframe not in MONITORED_TIMEFRAMES:
            return None
        policy = self.get_meta("notification_timeframe:" + protocol + ":" + timeframe)
        if policy is None:
            return 0 if protocol == SIGNAL_PROTOCOL and timeframe in ("1H", "4H") else None
        value = policy.get("activated_ms")
        return value if type(value) is int and value >= 0 else None

    def activate_timeframe_policy(self, timeframe, activated_ms, protocol=SIGNAL_PROTOCOL):
        """Persist a new stream's cutover once, independent of each channel.

        Old 1H/4H queues keep their existing per-channel policy. A newly
        enabled timeframe must not replay recent pre-enablement candles.
        """
        if timeframe not in MONITORED_TIMEFRAMES:
            raise ValueError("unsupported timeframe")
        key = "notification_timeframe:" + protocol + ":" + timeframe
        baseline = 0 if protocol == SIGNAL_PROTOCOL and timeframe in ("1H", "4H") else activated_ms
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)",
                       (key, encode({"activated_ms": baseline})))
        return self.timeframe_activation(timeframe, protocol=protocol)

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

    def retire_telegram_pending(self):
        """Stop unattempted/retry TG work; retain sent and uncertain receipts.

        Called under the monitor process lock before starting any workers.
        Bark queues, event identities and both channels' cutovers are unchanged.
        """
        with self.connect() as db:
            return db.execute("UPDATE outbox SET status='skipped',error='telegram_disabled_by_owner',updated_ms=? "
                              "WHERE status='pending'", (now_ms(),)).rowcount

    def retire_disabled_timeframes(self):
        """Withdraw unsent Bark legs and pending inference under the process lock.

        Preserve events, cutovers, sent/unknown receipts and terminal model
        proofs. Only pending delivery and pending/error candidate work on
        withdrawn periods is retired; sender guards also reject stale claims.
        """
        placeholders = ",".join("?" for _ in MONITORED_TIMEFRAMES)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            retired = db.execute(
                "UPDATE bark_outbox SET status='skipped',error='timeframe_disabled_by_owner',updated_ms=? "
                "WHERE status='pending' AND event_id IN (SELECT id FROM events WHERE timeframe NOT IN ("
                + placeholders + "))", (now_ms(), *MONITORED_TIMEFRAMES)).rowcount
            candidates = db.execute(
                "SELECT id,model FROM model_candidates WHERE status IN ('pending','error') "
                "AND timeframe NOT IN (" + placeholders + ")", MONITORED_TIMEFRAMES).fetchall()
            for row in candidates:
                proof = dict(json.loads(row["model"]), status="disabled", reason="timeframe_disabled_by_owner")
                db.execute("UPDATE model_candidates SET status='disabled',model=? WHERE id=?",
                           (encode(proof), row["id"]))
        return {"bark_pending": retired, "model_candidates": len(candidates)}

    def retire_muted_bark_timeframes(self):
        """Retire pending Bark only; display events, model work and receipts stay.

        Called under the monitor process lock before delivery workers start.
        Recovery changes interrupted sending to unknown first; unknown and all
        terminal receipts are never rewritten as skipped or automatically retried.
        """
        placeholders = ",".join("?" for _ in BARK_TIMEFRAMES)
        with self.connect() as db:
            return db.execute(
                "UPDATE bark_outbox SET status='skipped',error='bark_timeframe_muted_by_owner',updated_ms=? "
                "WHERE status='pending' AND event_id IN (SELECT id FROM events WHERE timeframe NOT IN ("
                + placeholders + "))", (now_ms(), *BARK_TIMEFRAMES)).rowcount

    def claim(self, now):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT o.*,e.payload,m.png,m.sha256 AS photo_sha256
                FROM outbox o JOIN events e ON e.id=o.event_id LEFT JOIN telegram_media m ON m.event_id=e.id
                WHERE o.status='pending' AND o.due_ms<=? AND NOT EXISTS (
                    SELECT 1 FROM outbox first JOIN events original ON original.id=first.event_id
                    WHERE first.event_id=json_extract(e.payload,'$.source_event_id')
                    AND (first.status='sending' OR (first.status='pending' AND original.close_ms>=?)))
                ORDER BY o.due_ms,e.close_ms LIMIT 1""", (now, now - FRESH_MS)).fetchone()
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
            row = db.execute("""SELECT b.*,e.payload FROM bark_outbox b JOIN events e ON e.id=b.event_id
                WHERE b.status='pending' AND b.due_ms<=? AND NOT EXISTS (
                    SELECT 1 FROM bark_outbox first JOIN events original ON original.id=first.event_id
                    WHERE first.event_id=json_extract(e.payload,'$.source_event_id')
                    AND (first.status='sending' OR (first.status='pending' AND original.close_ms>=?)))
                ORDER BY b.due_ms,e.close_ms LIMIT 1""", (now, now - FRESH_MS)).fetchone()
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

    def _direct_filter(self, channel=None):
        """SQL predicate for the new direct stage, never the legacy raw receipts."""
        from yoyo.monitor.notification_policy import activation
        channels = (channel,) if channel else ("telegram", "bark")
        cutovers = [activation(self, c, DIRECT_POLICY) for c in channels]
        cutovers = [t for t in cutovers if t is not None]
        if not cutovers:
            return "0", []
        streams, values = [], [SIGNAL_KIND, SIGNAL_PROTOCOL]
        for tf in DIRECT_TIMEFRAMES:
            since = self.timeframe_activation(tf, protocol=DIRECT_POLICY)
            if since is not None:
                streams.append("(e.timeframe=? AND e.close_ms>?)")
                values.extend((tf, max(since, min(cutovers))))
        if not streams:
            return "0", []
        return ("(e.kind=? AND json_extract(e.payload,'$.protocol')=? AND ("
                + " OR ".join(streams) + "))"), values

    def direct_event_count(self, since=0):
        clause, args = self._direct_filter()
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM events e WHERE " + clause + " AND e.close_ms>=?",
                              args + [since]).fetchone()[0]

    def market_phase_counts(self, timeframes):
        """Return the small status summary without deserializing saved charts."""
        marks = ",".join("?" for _ in timeframes)
        sql = ("SELECT COALESCE(json_extract(payload, '$.phase'), 'loading'), COUNT(*) FROM markets "
               "WHERE COALESCE(json_extract(payload, '$.active'), 1)=1 "
               "AND timeframe IN (" + marks + ") GROUP BY 1")
        with self.connect() as db:
            return {row[0]: row[1] for row in db.execute(sql, list(timeframes))}

    def _notification_filter(self, channel):
        clause, args = self._direct_filter(channel)
        return ("((e.kind=? AND json_extract(e.payload,'$.protocol')=?) OR " + clause + ")",
                [MODEL_KIND, MODEL_PROTOCOL] + args)

    def notification_status(self, channel):
        """Current two-stage receipts; retain old raw receipts as historical only."""
        if channel not in ("telegram", "bark"):
            raise ValueError("unsupported notification channel")
        table = "outbox" if channel == "telegram" else "bark_outbox"
        clause, args = self._notification_filter(channel)
        # Keep the outbox as the outer relation.  A health/status read when
        # there is no pending history must not scan every event payload merely
        # to discover that no receipt exists.
        base = " FROM " + table + " o CROSS JOIN events e ON e.id=o.event_id WHERE " + clause
        with self.connect() as db:
            counts = {r[0]: r[1] for r in db.execute("SELECT o.status,COUNT(*)" + base + " GROUP BY o.status", args)}
            sent = db.execute("SELECT MAX(o.updated_ms)" + base + " AND o.status='sent'", args).fetchone()[0]
        return dict(counts, pending=counts.get("pending", 0), failed=counts.get("failed", 0),
                    unknown=counts.get("unknown", 0), last_success_ms=sent)

    def notification_media_status(self):
        clause, args = self._notification_filter("telegram")
        with self.connect() as db:
            row = db.execute("SELECT COUNT(m.png),SUM(CASE WHEN m.error IS NOT NULL THEN 1 ELSE 0 END) "
                             "FROM telegram_media m JOIN events e ON e.id=m.event_id WHERE " + clause, args).fetchone()
        return {"snapshots": row[0], "render_fallbacks": row[1] or 0}

    def migrate_v1_protocol(self, cutoff_ms):
        """Remove only obsolete monitor observations after a pre-migration backup.

        Historical research and non-monitor files are outside this SQLite DB.
        Terminal receipts are not reclassified; the old protocol rows are
        deleted as explicitly authorized monitor-signal history, while the
        caller records this transaction in its migration receipt.
        """
        obsolete = ("imacd-tv-visible-start-monitor-v3", "imacd-yolo-confirmation-monitor-v1",
                    "imacd-zero-axis-monitor-v2", "imacd-pine-v2.2-default-monitor-v1")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            marks = ",".join("?" for _ in obsolete)
            ids = [r[0] for r in db.execute("SELECT id FROM events WHERE json_extract(payload,'$.protocol') IN (" + marks + ")", obsolete)]
            if ids:
                marks = ",".join("?" for _ in ids)
                db.execute("DELETE FROM telegram_media WHERE event_id IN (" + marks + ")", ids)
                db.execute("DELETE FROM outbox WHERE event_id IN (" + marks + ")", ids)
                db.execute("DELETE FROM bark_outbox WHERE event_id IN (" + marks + ")", ids)
                db.execute("DELETE FROM model_candidates WHERE id IN (" + marks + ")", ids)
                db.execute("DELETE FROM events WHERE id IN (" + marks + ")", ids)
            # This method may run after a V1 cutover.  Deleting its market
            # cache or notification metadata here would erase the forward-only
            # boundary and invite historical replay, so cleanup is event-only.
            prior = json.loads(db.execute("SELECT payload FROM meta WHERE key='migration:spike-burst-v1'").fetchone()[0]) if db.execute("SELECT 1 FROM meta WHERE key='migration:spike-burst-v1'").fetchone() else {}
            receipt = {"migrated_at_ms": int(cutoff_ms), "obsolete_event_rows": len(ids),
                       "obsolete_event_rows_cumulative": int(prior.get("obsolete_event_rows_cumulative", prior.get("obsolete_event_rows", 0))) + len(ids),
                       "protocol": "spike-burst-v1-monitor-v1", "cutoff_ms": int(cutoff_ms)}
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("migration:spike-burst-v1", encode(receipt)))
        return receipt

    def dedupe_v1_legacy_identity(self):
        """Remove only an old V1 identity when its canonical replacement exists."""
        with self.connect() as db:
            rows = db.execute("SELECT id,payload FROM events WHERE kind=? AND json_extract(payload,'$.protocol')=? "
                              "AND json_extract(payload,'$.source')='live' AND json_extract(payload,'$.confirmation')='raw'",
                              (SIGNAL_KIND, SIGNAL_PROTOCOL)).fetchall()
            removable = []
            deferred = 0
            for row in rows:
                canonical = self.event_id(json.loads(row[1]))
                if canonical != row[0]:
                    if db.execute("SELECT 1 FROM events WHERE id=?", (canonical,)).fetchone():
                        removable.append(row[0])
                    else:
                        deferred += 1
            if removable:
                marks = ",".join("?" for _ in removable)
                for table, column in (("telegram_media", "event_id"), ("outbox", "event_id"),
                                      ("bark_outbox", "event_id"), ("model_candidates", "id")):
                    db.execute("DELETE FROM " + table + " WHERE " + column + " IN (" + marks + ")", removable)
                db.execute("DELETE FROM events WHERE id IN (" + marks + ")", removable)
        return {"removed": len(removable), "deferred_without_canonical": deferred}

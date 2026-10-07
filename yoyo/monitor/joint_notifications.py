"""Durable, forward-only delivery receipts for live SPIKE line joint events.

This small SQLite adapter deliberately knows only the published joint event
contract.  It does not import the pandas-backed line scanner, so service/API
startup can inspect delivery state without loading the calculation stack.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import sqlite3

from yoyo.monitor import FRESH_MS, TIMEFRAMES
from yoyo.monitor.store import now_ms

JOINT_PROTOCOL = "spike-v128-lines-monitor-v1"
# Notification policy identity intentionally equals the immutable line-event
# protocol: a delivery receipt is meaningful only for this exact calculation.
JOINT_POLICY = JOINT_PROTOCOL
JOINT_TIMEFRAMES = ("15m", "30m", "1H", "4H")
_SYMBOL = re.compile(r"[A-Z0-9]+-[A-Z0-9]+-SWAP")
_TABLES = {"telegram": "joint_telegram_outbox", "bark": "joint_bark_outbox"}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def is_joint_event(event: object) -> bool:
    """Return whether an event is a closed, causal long joint observation."""
    if not isinstance(event, dict):
        return False
    if (not isinstance(event.get("id"), str) or not event["id"]
            or event.get("kind") != "joint" or event.get("protocol") != JOINT_PROTOCOL
            or event.get("side") != "long" or event.get("timeframe") not in JOINT_TIMEFRAMES
            or not isinstance(event.get("symbol"), str) or not _SYMBOL.fullmatch(event["symbol"])):
        return False
    close = event.get("close")
    if isinstance(close, bool) or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
        return False
    opened, closed, detected = (event.get(k) for k in ("bar_open_ms", "bar_close_ms", "detected_at_ms"))
    if not all(type(value) is int and value >= 0 for value in (opened, closed, detected)):
        return False
    step = TIMEFRAMES[event["timeframe"]]
    v9_open, v9_close = event.get("v9_signal_open_ms"), event.get("v9_signal_close_ms")
    if (event.get("source") not in {"chart", "higher", "both"}
            or not all(type(value) is int and value >= 0 for value in (v9_open, v9_close))
            or v9_open % step != 0 or v9_close != v9_open + step):
        return False
    stop = event.get("reference_stop")
    return (opened % step == 0 and closed == opened + step and v9_close <= closed
            and (stop is None or (isinstance(stop, (int, float)) and not isinstance(stop, bool)
                                  and math.isfinite(stop) and stop > 0)))


def normalize_joint_event(event: dict) -> dict:
    """Freeze the notification contract and keep its price equal to line close."""
    if not is_joint_event(event):
        raise ValueError("invalid_joint_notification_event")
    result = deepcopy(event)
    result["price"] = float(result["close"])
    return result


class JointNotificationStore:
    """A narrow Store-compatible adapter sharing only ``spike-lines-v1.sqlite3``.

    Rows carry an immutable normalized payload rather than joining ``events`` at
    send time: later position projection changes cannot change what a queued
    notification says.  Each channel has a separate forward-only activation.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            self.initialize_schema(db)
        self.path.chmod(0o600)

    @staticmethod
    def initialize_schema(db) -> None:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS joint_telegram_outbox (
                event_id TEXT PRIMARY KEY REFERENCES events(id), payload TEXT NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                due_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
                message_id INTEGER, error TEXT);
            CREATE TABLE IF NOT EXISTS joint_bark_outbox (
                event_id TEXT PRIMARY KEY REFERENCES events(id), payload TEXT NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                due_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
                error TEXT, server_timestamp INTEGER);
            CREATE INDEX IF NOT EXISTS joint_telegram_outbox_due ON joint_telegram_outbox(status,due_ms);
            CREATE INDEX IF NOT EXISTS joint_bark_outbox_due ON joint_bark_outbox(status,due_ms);
        """)

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

    def get_meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, _json(value)))

    @staticmethod
    def _policy_key(channel: str) -> str:
        return "notification_policy:" + ("bark:" if channel == "bark" else "") + JOINT_POLICY

    @staticmethod
    def _timeframe_key(channel: str, timeframe: str) -> str:
        return "joint_notification_timeframe:" + JOINT_PROTOCOL + ":" + channel + ":" + timeframe

    def activation(self, channel: str):
        if channel not in _TABLES:
            raise ValueError("unsupported notification channel")
        value = self.get_meta(self._policy_key(channel), {})
        stamp = value.get("activated_ms") if isinstance(value, dict) else None
        return stamp if type(stamp) is int and stamp >= 0 else None

    def timeframe_activation(self, timeframe, protocol=JOINT_POLICY, *, channel=None):
        if protocol != JOINT_POLICY or timeframe not in JOINT_TIMEFRAMES:
            return None
        channels = (channel,) if channel else tuple(_TABLES)
        values = []
        for current in channels:
            if current not in _TABLES:
                return None
            value = self.get_meta(self._timeframe_key(current, timeframe), {})
            stamp = value.get("activated_ms") if isinstance(value, dict) else None
            if type(stamp) is not int or stamp < 0:
                return None
            values.append(stamp)
        return min(values) if values else None

    def activate(self, channels, now):
        """Arm named channels once, after exchange-clock synchronization."""
        if type(now) is not int or now < 0:
            raise ValueError("invalid activation clock")
        channels = tuple(channels)
        if not channels or any(channel not in _TABLES for channel in channels):
            raise ValueError("unsupported notification channel")
        receipt = {}
        with self.connect() as db:
            for channel in channels:
                db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", (
                    self._policy_key(channel), _json({"activated_ms": now, "protocol": JOINT_POLICY})))
                for timeframe in JOINT_TIMEFRAMES:
                    db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", (
                        self._timeframe_key(channel, timeframe), _json({"activated_ms": now})))
                row = db.execute("SELECT payload FROM meta WHERE key=?", (self._policy_key(channel),)).fetchone()
                receipt[channel] = json.loads(row[0])
        return receipt

    @classmethod
    def queue_new_event(cls, db, event: dict, now: int) -> None:
        """Seed outboxes within the event-insertion transaction, never later."""
        if type(now) is not int or now < 0 or not is_joint_event(event):
            return
        normalized = normalize_joint_event(event)
        close = normalized["bar_close_ms"]
        # A future exchange event and a late/replayed scan are never delivery work.
        detected = normalized["detected_at_ms"]
        if not (close <= detected <= now and now - close <= FRESH_MS and now - detected <= FRESH_MS):
            return
        from yoyo.monitor.notification_center import routing_error_for_runtime
        book_path = next((row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main"), "")
        for channel, table in _TABLES.items():
            if book_path and routing_error_for_runtime(Path(book_path).parent, normalized, now, channel):
                continue
            policy = db.execute("SELECT payload FROM meta WHERE key=?", (cls._policy_key(channel),)).fetchone()
            if policy is None:
                continue
            activation = json.loads(policy[0]).get("activated_ms")
            tf = db.execute("SELECT payload FROM meta WHERE key=?", (cls._timeframe_key(channel, normalized["timeframe"]),)).fetchone()
            timeframe_activation = json.loads(tf[0]).get("activated_ms") if tf else None
            if (type(activation) is not int or type(timeframe_activation) is not int
                    or close <= activation or close <= timeframe_activation):
                continue
            db.execute("INSERT OR IGNORE INTO " + table + "(event_id,payload,status,due_ms,updated_ms) VALUES (?,?,?,?,?)",
                       (normalized["id"], _json(normalized), "pending", now, now))

    def recover_outbox(self):
        with self.connect() as db:
            for table in _TABLES.values():
                db.execute("UPDATE " + table + " SET status='unknown',error='process_interrupted_during_delivery' WHERE status='sending'")

    def _claim(self, channel, now):
        table = _TABLES[channel]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM " + table + " WHERE status='pending' AND due_ms<=? "
                             "ORDER BY due_ms,json_extract(payload,'$.bar_close_ms') LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE " + table + " SET status='sending',attempts=attempts+1,updated_ms=? WHERE event_id=?",
                       (now, row["event_id"]))
            return dict(row, event=json.loads(row["payload"]), attempts=row["attempts"] + 1)

    def claim(self, now):
        return self._claim("telegram", now)

    def claim_bark(self, now):
        return self._claim("bark", now)

    def finish(self, event_id, status, *, error=None, message_id=None, due_ms=0):
        self._finish("telegram", event_id, status, error=error, message_id=message_id, due_ms=due_ms)

    def finish_bark(self, event_id, status, *, error=None, server_timestamp=None, due_ms=0):
        self._finish("bark", event_id, status, error=error, server_timestamp=server_timestamp, due_ms=due_ms)

    def _finish(self, channel, event_id, status, *, error=None, message_id=None, server_timestamp=None, due_ms=0):
        if status not in {"pending", "sent", "failed", "unknown", "skipped"}:
            raise ValueError("invalid notification status")
        table = _TABLES[channel]
        with self.connect() as db:
            if channel == "telegram":
                db.execute("UPDATE " + table + " SET status=?,error=?,message_id=?,due_ms=?,updated_ms=? WHERE event_id=?",
                           (status, error, message_id, due_ms, now_ms(), event_id))
            else:
                db.execute("UPDATE " + table + " SET status=?,error=?,server_timestamp=?,due_ms=?,updated_ms=? WHERE event_id=?",
                           (status, error, server_timestamp, due_ms, now_ms(), event_id))

    def _status(self, channel):
        table = _TABLES[channel]
        with self.connect() as db:
            counts = {row[0]: row[1] for row in db.execute("SELECT status,COUNT(*) FROM " + table + " GROUP BY status")}
            sent = db.execute("SELECT MAX(updated_ms) FROM " + table + " WHERE status='sent'").fetchone()[0]
        return dict(counts, pending=counts.get("pending", 0), failed=counts.get("failed", 0),
                    unknown=counts.get("unknown", 0), last_success_ms=sent,
                    activated_ms=self.activation(channel))

    def telegram_status(self, protocol=None):
        return self._status("telegram")

    def bark_status(self, protocol=None):
        return self._status("bark")

    def notification_status(self, channel):
        if channel not in _TABLES:
            raise ValueError("unsupported notification channel")
        return self._status(channel)

    def notification_media_status(self, protocol=None):
        return {"snapshots": 0, "render_fallbacks": 0}

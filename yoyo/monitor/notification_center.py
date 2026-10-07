"""Shared notification subscriptions and a read model over existing outboxes.

The center owns routing preferences, never credentials, signals or delivery
receipts. Producers and senders consult the same small SQLite subscription
book; existing atomic per-channel outboxes remain the delivery authority.
Re-enabling a route establishes a new closed-bar boundary, never a replay.
Telegram sendMessage receipts and Bark server acceptance are not read receipts:
https://core.telegram.org/bots/api#sendmessage
https://github.com/Finb/bark-server
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time

from yoyo.monitor import DIRECT_POLICY, MODEL_KIND, MODEL_PROTOCOL, SIGNAL_KIND, SIGNAL_PROTOCOL, MONITORED_TIMEFRAMES
from yoyo.monitor.joint_notifications import JOINT_POLICY, JOINT_PROTOCOL
from yoyo.monitor.v130_policy import (POLICY as V130_POLICY, SIGNAL_KIND as V130_KIND,
                                      SIGNAL_PROTOCOL as V130_PROTOCOL, TIMEFRAMES as V130_TIMEFRAMES,
                                      TOPIC as V130_TOPIC)

DATABASE = "notifications.sqlite3"
CHANNELS = {"telegram": "Telegram", "bark": "Bark"}
STATES = ("pending", "sending", "sent", "failed", "unknown", "skipped")
TOPICS = {
    "spike_v128": {"label": "SPIKE V12.8 启动", "kind": SIGNAL_KIND,
                   "protocol": SIGNAL_PROTOCOL, "policy": DIRECT_POLICY, "sides": ["long", "short"]},
    "yolo_confirmation": {"label": "YOLO 补充确认", "kind": MODEL_KIND,
                          "protocol": MODEL_PROTOCOL, "policy": MODEL_PROTOCOL, "sides": ["long", "short"]},
    "joint": {"label": "突破＋spike 联合信号", "kind": "joint",
              "protocol": JOINT_PROTOCOL, "policy": JOINT_POLICY, "sides": ["long"]},
    V130_TOPIC: {"label": "SPIKE V13.1 回踩确认", "kind": V130_KIND,
                "protocol": V130_PROTOCOL, "policy": V130_POLICY,
                "sides": ["long", "short"], "timeframes": list(V130_TIMEFRAMES)},
}
NOTICE = ("订阅只影响后续新信号，不补发历史。关闭后，已经开始发送的请求可能完成。"
          "结果未知不会自动重发；服务接受不代表设备收到或已读。统计包含旧版发送记录。")


def topic_for(event):
    for topic, spec in TOPICS.items():
        if event.get("kind") == spec["kind"] and event.get("protocol") == spec["protocol"]:
            return topic
    return "legacy"


def routing_error_for_runtime(runtime, event, now, channel):
    """A supplemental route gate; existing causality/freshness gates still apply."""
    if channel not in CHANNELS:
        raise ValueError("unsupported notification channel")
    topic = topic_for(event)
    path = Path(runtime) / DATABASE
    if topic == "legacy" or not path.exists():
        return None
    # Missing configuration retains existing authorized behavior. An unreadable
    # or corrupt existing configuration must never silently re-enable delivery.
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            row = db.execute("SELECT enabled,activated_ms FROM routes WHERE topic=? AND channel=?",
                             (topic, channel)).fetchone()
    except sqlite3.Error:
        return "subscription_unavailable"
    if row is None:
        return "subscription_unavailable"
    enabled, since = row
    if enabled not in (0, 1) or type(since) is not int or since < 0:
        return "subscription_unavailable"
    if not enabled:
        return "subscription_disabled"
    closes = [event.get("bar_close_ms")]
    if topic == "yolo_confirmation":
        closes.append(event.get("indicator", {}).get("bar_close_ms"))
    if any(type(value) is not int or value <= since for value in closes):
        return "before_subscription_activation"
    return None


def routing_error(store, event, now, channel):
    return routing_error_for_runtime(store.path.parent, event, now, channel)


class NotificationCenter:
    def __init__(self, runtime):
        self.runtime = Path(runtime)
        self.path = self.runtime / DATABASE
        self.runtime.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS routes (
                    topic TEXT NOT NULL, channel TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    activated_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
                    PRIMARY KEY(topic,channel));
                CREATE TABLE IF NOT EXISTS route_changes (
                    id INTEGER PRIMARY KEY, topic TEXT NOT NULL, channel TEXT NOT NULL,
                    enabled INTEGER NOT NULL, changed_ms INTEGER NOT NULL);
            """)
            # Preserve every established signal-policy cutover. Zero here adds
            # no authority: an event must still pass the original channel arm.
            db.executemany("INSERT OR IGNORE INTO routes VALUES (?,?,1,0,0)",
                           [(topic, channel) for topic in TOPICS for channel in CHANNELS])
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def update_route(self, topic, channel, enabled, now):
        if topic not in TOPICS or channel not in CHANNELS or type(enabled) is not bool:
            raise ValueError("请选择有效的通知来源和渠道")
        if type(now) is not int or now < 0:
            raise ValueError("通知时钟尚未就绪")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT enabled,activated_ms FROM routes WHERE topic=? AND channel=?",
                             (topic, channel)).fetchone()
            if bool(row["enabled"]) == enabled:
                return
            since = max(now, row["activated_ms"]) if enabled else row["activated_ms"]
            db.execute("UPDATE routes SET enabled=?,activated_ms=?,updated_ms=? WHERE topic=? AND channel=?",
                       (int(enabled), since, now, topic, channel))
            db.execute("INSERT INTO route_changes(topic,channel,enabled,changed_ms) VALUES (?,?,?,?)",
                       (topic, channel, int(enabled), now))

    @contextmanager
    def books(self):
        """Attach source books read-only; never create missing source databases."""
        db = sqlite3.connect(":memory:", uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            for name, filename in (("ordinary", "monitor.sqlite3"), ("joint", "spike-lines-v1.sqlite3")):
                path = self.runtime / filename
                if path.exists():
                    db.execute(f"ATTACH DATABASE ? AS {name}", (path.resolve().as_uri() + "?mode=ro",))
            db.execute("BEGIN")
            yield db
        finally:
            db.close()

    @staticmethod
    def tables(db):
        result = set()
        for _, name, _ in db.execute("PRAGMA database_list"):
            if name in ("ordinary", "joint"):
                result.update((name, row[0]) for row in db.execute(
                    f"SELECT name FROM {name}.sqlite_master WHERE type='table'"))
        return result

    @staticmethod
    def meta(db, tables, book, key):
        if (book, "meta") not in tables:
            return {}
        row = db.execute(f"SELECT payload FROM {book}.meta WHERE key=?", (key,)).fetchone()
        result = json.loads(row[0]) if row else {}
        return result if isinstance(result, dict) else {}

    def clock(self, fallback):
        with self.books() as db:
            offset = self.meta(db, self.tables(db), "joint", "joint_notification_clock").get("offset_ms")
        return int(time.time() * 1000) + offset if type(offset) is int else fallback()

    @staticmethod
    def union(db, tables):
        queries = []
        for book, channel, table in (("ordinary", "telegram", "outbox"), ("ordinary", "bark", "bark_outbox"),
                                     ("joint", "telegram", "joint_telegram_outbox"), ("joint", "bark", "joint_bark_outbox")):
            if (book, table) not in tables:
                continue
            payload = "o.payload" if book == "joint" else "e.payload"
            field = lambda key: f"json_extract({payload},'$.{key}')"
            topic = "CASE " + " ".join(
                f"WHEN {field('protocol')}='{s['protocol']}' AND {field('kind')}='{s['kind']}' THEN '{t}'"
                for t, s in TOPICS.items()) + " ELSE 'legacy' END"
            query = (f"SELECT '{book}:{channel}:' || o.event_id AS id, o.event_id, '{channel}' AS channel, "
                     f"{topic} AS topic, {field('symbol')} AS symbol, {field('timeframe')} AS timeframe, "
                     f"{field('side')} AS side, {field('protocol')} AS protocol, {field('kind')} AS kind, "
                     f"{field('bar_close_ms')} AS bar_close_ms, {field('price')} AS price, "
                     "o.updated_ms, o.status, o.attempts, o.error "
                     f"FROM {book}.{table} o")
            if book == "ordinary":
                query += " JOIN ordinary.events e ON e.id=o.event_id"
            queries.append(query)
        return " UNION ALL ".join(queries)

    def snapshot(self, status, now):
        with self.connect() as db:
            preferences = {(r["topic"], r["channel"]): dict(r) for r in db.execute("SELECT * FROM routes")}
        with self.books() as db:
            tables = self.tables(db)
            union = self.union(db, tables)
            totals = list(db.execute(f"SELECT channel,status,COUNT(*) AS n,MAX(updated_ms) AS latest "
                                     f"FROM ({union}) GROUP BY channel,status")) if union else []
            routes = []
            for topic, spec in TOPICS.items():
                for channel in CHANNELS:
                    row = preferences[(topic, channel)]
                    prefix = "notification_policy:" + ("bark:" if channel == "bark" else "")
                    arm = self.meta(db, tables, "joint" if topic == "joint" else "ordinary", prefix + spec["policy"])
                    since = arm.get("activated_ms")
                    armed = type(since) is int and since >= 0
                    routes.append({**row, "enabled": bool(row["enabled"]), "label": spec["label"],
                                   "sides": spec["sides"],
                                   "timeframes": list(spec.get("timeframes", MONITORED_TIMEFRAMES)),
                                   "activated_ms": max(row["activated_ms"], since) if armed else None, "armed": armed})
        channels = []
        for channel, name in CHANNELS.items():
            counts = dict.fromkeys(STATES, 0)
            latest = None
            for row in totals:
                if row["channel"] == channel and row["status"] in counts:
                    counts[row["status"]] = row["n"]
                    if row["status"] == "sent":
                        latest = row["latest"]
            ordinary, joint = status.get(channel, {}), status.get("joint_notifications", {}).get(channel, {})
            configured = bool(ordinary.get("configured") or joint.get("configured"))
            active = any(route["enabled"] and route["armed"] and
                         (joint if route["topic"] == "joint" else ordinary).get("enabled")
                         for route in routes if route["channel"] == channel)
            channels.append({"id": channel, "name": name, "configured": configured, "enabled": active,
                             "counts": counts, "last_success_ms": latest})
        return {"as_of_ms": now, "channels": channels, "routes": routes, "notice": NOTICE}

    def events(self, *, channel=None, topic=None, status=None, side=None, timeframe=None, search="", offset=0, limit=50, now=0):
        allowed = {"channel": CHANNELS, "topic": (*TOPICS, "legacy"), "status": STATES,
                   "side": ("long", "short"), "timeframe": MONITORED_TIMEFRAMES}
        filters = {"channel": channel, "topic": topic, "status": status, "side": side, "timeframe": timeframe}
        if any(value is not None and value not in allowed[key] for key, value in filters.items()):
            raise ValueError("不支持的通知筛选条件")
        if type(offset) is not int or not 0 <= offset <= 100_000 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("通知分页参数无效")
        if not isinstance(search, str) or len(search) > 48:
            raise ValueError("搜索合约不超过 48 个字符")
        clauses, args = [], []
        for key, value in filters.items():
            if value is not None:
                clauses.append(f"{key}=?")
                args.append(value)
        if search.strip():
            clauses.append("instr(upper(symbol),?) > 0")
            args.append(search.strip().upper())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.books() as db:
            union = self.union(db, self.tables(db))
            if not union:
                rows, total = [], 0
            else:
                total = db.execute(f"SELECT COUNT(*) FROM ({union}){where}", args).fetchone()[0]
                rows = [dict(row) for row in db.execute(
                    f"SELECT * FROM ({union}){where} ORDER BY updated_ms DESC,id DESC LIMIT ? OFFSET ?",
                    [*args, limit, offset])]
        for row in rows:
            row["label"] = TOPICS.get(row["topic"], {}).get("label", "旧版通知")
            if row["error"] and not re.fullmatch(r"[a-z0-9_:.-]{1,160}", str(row["error"])):
                row["error"] = "details_redacted"
        return {"items": rows, "total": total, "offset": offset, "limit": limit, "as_of_ms": now}

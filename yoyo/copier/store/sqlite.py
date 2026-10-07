from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from yoyo.copier.config import get_db_path
from yoyo.copier.store.models import now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_message_id TEXT UNIQUE NOT NULL,
    channel_id TEXT NOT NULL,
    author TEXT,
    content TEXT,
    attachments_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    status TEXT DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    intent TEXT NOT NULL,
    should_act INTEGER NOT NULL,
    confidence REAL,
    ai_json TEXT NOT NULL,
    reject_reason TEXT,
    status TEXT DEFAULT 'parsed',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    signal_id INTEGER,
    inst_id TEXT,
    side TEXT,
    ord_type TEXT,
    sz TEXT,
    px TEXT,
    okx_ord_id TEXT,
    sl_trigger TEXT,
    tp_trigger TEXT,
    status TEXT,
    response_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    event TEXT NOT NULL,
    detail TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inst_id TEXT,
    pos_side TEXT,
    pos TEXT,
    avg_px TEXT,
    upl TEXT,
    synced_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or get_db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    def message_exists(self, external_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM messages WHERE discord_message_id=? LIMIT 1", (external_id,)
            ).fetchone() is not None

    def insert_message(
        self,
        discord_message_id: str,
        channel_id: str,
        author: str,
        content: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO messages
                (discord_message_id, channel_id, author, content, attachments_json, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    discord_message_id,
                    str(channel_id),
                    author,
                    content,
                    json.dumps(attachments or [], ensure_ascii=False),
                    now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def get_message(self, message_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            return dict(row) if row else None

    def update_message_status(self, message_id: int, status: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE messages SET status=? WHERE id=?", (status, message_id))

    def list_messages(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        status: str | None = None,
        intent: str | None = None,
        channel_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("m.status=?")
            params.append(status)
        if intent:
            where.append("s.intent=?")
            params.append(intent)
        if channel_id:
            where.append("m.channel_id=?")
            params.append(channel_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        join = "LEFT JOIN signals s ON s.message_id=m.id"
        with self._connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(DISTINCT m.id) FROM messages m {join} {clause}", params
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""SELECT m.*, s.intent, s.confidence, s.should_act, s.ai_json, s.reject_reason
                FROM messages m {join} {clause}
                GROUP BY m.id ORDER BY m.id DESC LIMIT ? OFFSET ?""",
                [*params, per_page, max(0, page - 1) * per_page],
            ).fetchall()
        return self._rows(rows), total

    def insert_signal(
        self,
        *,
        message_id: int,
        intent: str,
        should_act: bool,
        confidence: float,
        ai_json: str,
        reject_reason: str | None = None,
        status: str = "parsed",
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO signals
                (message_id, intent, should_act, confidence, ai_json, reject_reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id,
                    intent,
                    int(should_act),
                    confidence,
                    ai_json,
                    reject_reason,
                    status,
                    now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def insert_order(self, **values: Any) -> int:
        cols = (
            "message_id",
            "signal_id",
            "inst_id",
            "side",
            "ord_type",
            "sz",
            "px",
            "okx_ord_id",
            "sl_trigger",
            "tp_trigger",
            "status",
            "response_json",
        )
        payload = [values.get(col) for col in cols]
        with self._connect() as conn:
            cur = conn.execute(
                f"INSERT INTO orders ({','.join(cols)},created_at) VALUES ({','.join('?' for _ in cols)},?)",
                [*payload, now_iso()],
            )
            return int(cur.lastrowid)

    def list_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT o.*, m.author, m.channel_id, m.content
                FROM orders o LEFT JOIN messages m ON m.id=o.message_id
                ORDER BY o.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return self._rows(rows)

    def count_open_orders_today(self, channel_id: str | None = None) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        channel_clause = " AND m.channel_id=?" if channel_id else ""
        params: list[Any] = [today]
        if channel_id:
            params.append(str(channel_id))
        with self._connect() as conn:
            row = conn.execute(
                f"""SELECT COUNT(*) FROM orders o
                LEFT JOIN messages m ON m.id=o.message_id
                WHERE substr(o.created_at,1,10)=?
                AND o.status IN ('live','open','filled','partially_filled')
                {channel_clause}""",
                params,
            ).fetchone()
            return int(row[0])

    def count_positions_by_symbol(self, symbol: str, channel_id: str | None = None) -> int:
        like = f"{symbol.upper()}-%"
        channel_clause = " AND m.channel_id=?" if channel_id else ""
        params: list[Any] = [like]
        if channel_id:
            params.append(str(channel_id))
        with self._connect() as conn:
            row = conn.execute(
                f"""SELECT COUNT(*) FROM orders o
                LEFT JOIN messages m ON m.id=o.message_id
                WHERE upper(o.inst_id) LIKE ?
                AND o.status IN ('live','open','filled','partially_filled')
                {channel_clause}""",
                params,
            ).fetchone()
            return int(row[0])

    def mark_stale_open_orders(
        self,
        *,
        channel_id: str,
        active_inst_ids: set[str],
    ) -> int:
        active = {str(inst_id).upper() for inst_id in active_inst_ids if str(inst_id).strip()}
        active_statuses = ("live", "open", "filled", "partially_filled")
        params: list[Any] = [str(channel_id), *active_statuses]
        keep_clause = ""
        if active:
            keep_clause = f"AND upper(o.inst_id) NOT IN ({','.join('?' for _ in active)})"
            params.extend(sorted(active))
        with self._connect() as conn:
            cur = conn.execute(
                f"""UPDATE orders
                SET status='stale'
                WHERE id IN (
                    SELECT o.id FROM orders o
                    LEFT JOIN messages m ON m.id=o.message_id
                    WHERE m.channel_id=?
                    AND o.status IN ({','.join('?' for _ in active_statuses)})
                    {keep_clause}
                )""",
                params,
            )
            return int(cur.rowcount or 0)

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, str(value), now_iso()),
            )

    def get_setting(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return str(row[0]) if row else None

    def get_all_settings(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key,value FROM settings").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def audit(
        self,
        event: str,
        detail: str = "",
        *,
        message_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO audit_logs(message_id,event,detail,payload_json,created_at)
                VALUES(?,?,?,?,?)""",
                (
                    message_id,
                    event,
                    detail,
                    json.dumps(payload or {}, ensure_ascii=False),
                    now_iso(),
                ),
            )

    def recent_open_signals(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT s.*, m.author, m.channel_id, m.content AS content_preview, m.created_at
                FROM signals s JOIN messages m ON m.id=s.message_id
                WHERE s.intent='open' ORDER BY s.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        result = self._rows(rows)
        for row in result:
            try:
                parsed = json.loads(row.get("ai_json") or "{}")
                row.update(
                    {
                        "symbol": parsed.get("symbol"),
                        "side": parsed.get("side"),
                        "summary": parsed.get("summary"),
                        "stop_loss": parsed.get("stop_loss"),
                        "take_profit": parsed.get("take_profit"),
                    }
                )
            except json.JSONDecodeError:
                pass
        return result

    def intent_breakdown(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT intent,COUNT(*) AS n FROM signals GROUP BY intent ORDER BY n DESC"
            ).fetchall()
        return {str(row["intent"]): int(row["n"]) for row in rows}

    def kol_leaderboard(self, days: int = 30, limit: int = 10) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT m.author, m.channel_id, COUNT(*) messages,
                SUM(CASE WHEN s.intent='open' THEN 1 ELSE 0 END) open_signals,
                SUM(CASE WHEN m.status='executed' THEN 1 ELSE 0 END) executed
                FROM messages m LEFT JOIN signals s ON s.message_id=m.id
                WHERE m.created_at>=?
                GROUP BY m.author,m.channel_id ORDER BY open_signals DESC,messages DESC LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
        return self._rows(rows)

    def dashboard_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            messages = int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
            orders = int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
            dry = int(conn.execute("SELECT COUNT(*) FROM orders WHERE status='dry_run'").fetchone()[0])
            executed = int(conn.execute("SELECT COUNT(*) FROM messages WHERE status='executed'").fetchone()[0])
            skipped = int(conn.execute("SELECT COUNT(*) FROM messages WHERE status='skipped'").fetchone()[0])
            opens = int(conn.execute("SELECT COUNT(*) FROM signals WHERE intent='open'").fetchone()[0])
        return {
            "total_messages": messages,
            "orders_total": orders,
            "orders_dry_run": dry,
            "executed": executed,
            "skipped": skipped,
            "open_signals": opens,
        }

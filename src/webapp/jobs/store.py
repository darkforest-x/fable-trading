"""SQLite job store under data/ops_jobs.sqlite (gitignored via data/)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = PROJECT_ROOT / "data" / "ops_jobs.sqlite"

STATUSES = frozenset(
    {"queued", "running", "succeeded", "failed", "cancelled", "timeout"}
)
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timeout"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def jobs_db_path() -> Path:
    raw = os.environ.get("OPS_JOBS_DB", "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_DB


class JobStore:
    """Thread-safe thin wrapper around jobs table."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else jobs_db_path()
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    exit_code INTEGER,
                    log_path TEXT,
                    error_summary TEXT,
                    argv_json TEXT,
                    summary TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
            )

    def create(
        self,
        *,
        job_type: str,
        params: dict[str, Any],
        argv: list[str],
        summary: str,
        log_path: str,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        now = _utcnow()
        row = {
            "id": job_id,
            "job_type": job_type,
            "params_json": json.dumps(params, ensure_ascii=False, sort_keys=True),
            "status": "queued",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "log_path": log_path,
            "error_summary": None,
            "argv_json": json.dumps(argv, ensure_ascii=False),
            "summary": summary,
        }
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, job_type, params_json, status, created_at, started_at,
                    finished_at, exit_code, log_path, error_summary, argv_json, summary
                ) VALUES (
                    :id, :job_type, :params_json, :status, :created_at, :started_at,
                    :finished_at, :exit_code, :log_path, :error_summary, :argv_json, :summary
                )
                """,
                row,
            )
        return self.get(job_id)  # type: ignore[return-value]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def list_jobs(
        self, *, limit: int = 50, offset: int = 0, status: str | None = None
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        where = ""
        args: list[Any] = []
        if status:
            if status not in STATUSES:
                raise ValueError(f"invalid status filter: {status}")
            where = "WHERE status = ?"
            args.append(status)
        with self._lock, self._conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS n FROM jobs {where}", args
            ).fetchone()["n"]
            rows = conn.execute(
                f"""
                SELECT * FROM jobs {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*args, limit, offset],
            ).fetchall()
        return {
            "items": [self._row_to_dict(r) for r in rows],
            "total": int(total),
            "limit": limit,
            "offset": offset,
        }

    def claim_next_queued(self) -> dict[str, Any] | None:
        """Atomically mark oldest queued job as running. Returns claimed row or None."""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            job_id = row["id"]
            now = _utcnow()
            conn.execute(
                """
                UPDATE jobs
                SET status = 'running', started_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, job_id),
            )
            claimed = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._row_to_dict(claimed) if claimed else None

    def finish(
        self,
        job_id: str,
        *,
        status: str,
        exit_code: int | None = None,
        error_summary: str | None = None,
    ) -> None:
        if status not in TERMINAL:
            raise ValueError(f"finish status must be terminal, got {status}")
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, finished_at = ?, exit_code = ?, error_summary = ?
                WHERE id = ? AND status IN ('queued', 'running')
                """,
                (status, _utcnow(), exit_code, error_summary, job_id),
            )

    def mark_orphaned_running(self, reason: str = "orphaned_after_restart") -> int:
        """On dashboard restart: running jobs become failed (no auto-rerun)."""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    finished_at = ?,
                    error_summary = ?
                WHERE status = 'running'
                """,
                (_utcnow(), reason),
            )
            return int(cur.rowcount or 0)

    def count_active(self) -> int:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM jobs
                WHERE status IN ('queued', 'running')
                """
            ).fetchone()
        return int(row["n"])

    def count_running(self) -> int:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status = 'running'"
            ).fetchone()
        return int(row["n"])

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["params"] = json.loads(d.pop("params_json") or "{}")
        except json.JSONDecodeError:
            d["params"] = {}
        try:
            d["argv"] = json.loads(d.pop("argv_json") or "[]")
        except json.JSONDecodeError:
            d["argv"] = []
        return d


_store: JobStore | None = None
_store_lock = threading.Lock()


def get_store() -> JobStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = JobStore()
        return _store


def reset_store_for_tests(db_path: Path | None = None) -> JobStore:
    """Test helper: replace process-global store."""
    global _store
    with _store_lock:
        _store = JobStore(db_path=db_path)
        return _store

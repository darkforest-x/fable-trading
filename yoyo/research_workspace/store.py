"""Durable factor notes and immutable run specifications.

Experiment/run separation follows Qlib Recorder and MLflow Tracking. Research
judgments never alter production eligibility or erase previous conclusions.
https://qlib.readthedocs.io/en/stable/component/recorder.html
https://mlflow.org/docs/latest/ml/tracking/
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkspaceStore:
    def __init__(self, runtime):
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.path = self.runtime / "workspace.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS notes (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
              entity_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
              created_at TEXT NOT NULL, UNIQUE(kind,entity_id,revision));
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, recipe TEXT NOT NULL,
              spec TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
              started_at TEXT, finished_at TEXT, error TEXT, cancel_requested INTEGER DEFAULT 0,
              output TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def notes(self, kind):
        with self.connect() as db:
            rows = db.execute("SELECT n.* FROM notes n JOIN (SELECT entity_id,MAX(revision) rev "
                              "FROM notes WHERE kind=? GROUP BY entity_id) x "
                              "ON n.entity_id=x.entity_id AND n.revision=x.rev WHERE n.kind=?",
                              (kind, kind)).fetchall()
        return {r["entity_id"]: dict(json.loads(r["payload"]), revision=r["revision"],
                                   updated_at=r["created_at"]) for r in rows}

    def save_note(self, kind, entity_id, payload, expected_revision=0):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT COALESCE(MAX(revision),0) FROM notes WHERE kind=? AND entity_id=?",
                                  (kind, entity_id)).fetchone()[0]
            if revision != expected_revision:
                raise ValueError("记录已被另一页面更新，请刷新后再保存。")
            db.execute("INSERT INTO notes(kind,entity_id,revision,payload,created_at) VALUES(?,?,?,?,?)",
                       (kind, entity_id, revision + 1, json.dumps(payload, ensure_ascii=False), now()))
        return dict(payload, revision=revision + 1)

    def history(self, kind, entity_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM notes WHERE kind=? AND entity_id=? ORDER BY revision DESC",
                              (kind, entity_id)).fetchall()
        return [dict(json.loads(r["payload"]), revision=r["revision"], created_at=r["created_at"]) for r in rows]

    def create_job(self, experiment_id, recipe, spec, output_root):
        job_id = uuid.uuid4().hex
        output = str(Path(output_root) / job_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 8:
                raise ValueError("已有 8 个待完成任务，请等待或取消后重试。")
            db.execute("INSERT INTO jobs(id,experiment_id,recipe,spec,status,created_at,output) VALUES(?,?,?,?,?,?,?)",
                       (job_id, experiment_id, recipe, json.dumps(spec, ensure_ascii=False), "queued", now(), output))
        return self.job(job_id)

    @staticmethod
    def _job(row):
        if row is None:
            raise KeyError("任务不存在")
        value = dict(row)
        value["spec"] = json.loads(value["spec"])
        value["cancel_requested"] = bool(value["cancel_requested"])
        return value

    def job(self, job_id):
        with self.connect() as db:
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def jobs(self):
        with self.connect() as db:
            return [self._job(r) for r in db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 200")]

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running',started_at=? WHERE id=?", (now(), row["id"]))
        return self.job(row["id"])

    def finish(self, job_id, status, error=None):
        if status not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("invalid job terminal state")
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?,finished_at=?,error=? WHERE id=? AND status='running'",
                       (status, now(), error, job_id))

    def recover(self):
        # Called only after obtaining the exclusive OS worker lock. A HTTP
        # service restart must never mark an independent worker interrupted.
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted',finished_at=?,error=? WHERE status='running'",
                       (now(), "工作进程意外退出；原始输出保留，可新建运行。"))

    def cancel(self, job_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            if row["status"] == "queued":
                db.execute("UPDATE jobs SET status='cancelled',finished_at=?,cancel_requested=1 WHERE id=?", (now(), job_id))
            elif row["status"] == "running":
                db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
        return self.job(job_id)

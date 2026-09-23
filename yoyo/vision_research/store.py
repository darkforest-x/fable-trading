"""Local-only inference ledger; no model registry or execution-system writes.

Each inference preserves its image, criteria and model identity. Human reviews
are a separate append-only history and never promote model output into gold.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .schemas import ImageInput


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchStore:
    def __init__(self, root: Path):
        self.root = root
        self.images = root / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self.database = root / "research.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS runs "
                       "(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, record TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS global_references "
                       "(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, items TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO global_references(id,revision,items) VALUES(1,0,'[]')")
            rows = db.execute("SELECT id,record FROM runs").fetchall()
            for run_id, raw in rows:
                record = json.loads(raw)
                if record.get("status") == "running":
                    record.update(status="interrupted", error="服务重启，上一请求结果未知；未自动重试")
                    db.execute("UPDATE runs SET record=? WHERE id=?",
                               (json.dumps(record, ensure_ascii=False), run_id))

    def connect(self):
        return sqlite3.connect(str(self.database), timeout=10)

    def put_image(self, image: ImageInput) -> str:
        path = self.images / (image.sha256 + ".png")
        if not path.exists():
            try:
                with path.open("xb") as handle:
                    handle.write(image.data)
            except FileExistsError:
                pass
        return "/api/images/" + path.name

    def get_references(self):
        with self.connect() as db:
            row = db.execute("SELECT revision,items FROM global_references WHERE id=1").fetchone()
        if row is None:
            return {"items": [], "revision": 0}
        return {"items": json.loads(row[1]), "revision": row[0]}

    def replace_references(self, items, expected_revision=None):
        """Atomically point the durable global set at already-stored image objects."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision,items FROM global_references WHERE id=1").fetchone()
            revision, raw = row if row is not None else (0, "[]")
            current = json.loads(raw)
            if expected_revision is not None and expected_revision != revision:
                raise ReferenceRevisionConflict("参考图已在其他页面更新，请重新载入后再保存")
            if current == items:
                return {"items": current, "revision": revision}
            revision += 1
            db.execute("INSERT INTO global_references(id,revision,items) VALUES(1,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,items=excluded.items",
                       (revision, json.dumps(items, ensure_ascii=False)))
        return {"items": items, "revision": revision}

    def image_path(self, name: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}\.png", name):
            raise FileNotFoundError(name)
        path = self.images / name
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(name)
        return path

    def save(self, record: Dict[str, Any]):
        with self.connect() as db:
            db.execute("INSERT INTO runs(id,created_at,record) VALUES(?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET record=excluded.record",
                       (record["id"], record["created_at"], json.dumps(record, ensure_ascii=False)))

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as db:
            row = db.execute("SELECT record FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, limit: int = 200):
        with self.connect() as db:
            rows = db.execute("SELECT record FROM runs ORDER BY created_at DESC LIMIT ?",
                              (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def review(self, run_id: str, verdict: str, note: str):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            record = json.loads(row[0])
            if record.get("status") != "completed":
                raise ValueError("只有已完成的识别可以人工复核")
            review = {"verdict": verdict, "note": note, "reviewed_at": utc_now()}
            record["review"] = review
            record.setdefault("review_history", []).append(review)
            db.execute("UPDATE runs SET record=? WHERE id=?",
                       (json.dumps(record, ensure_ascii=False), run_id))
        return record


class ReferenceRevisionConflict(Exception):
    """Raised when a client tries to replace a newer global reference set."""

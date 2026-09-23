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
            with path.open("xb") as handle:
                handle.write(image.data)
        return "/api/images/" + path.name

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

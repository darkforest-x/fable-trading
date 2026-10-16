"""Durable per-signal ownership and pre-send intent journal.

OKX clOrdId is unique only among pending orders. A timed-out POST is never
automatically retried, even with the same client ID. Restart reconciles GETs.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3


def identifier(event_id, purpose):
    return "fb" + hashlib.sha256((purpose + ":" + event_id).encode()).hexdigest()[:30]


class Store:
    def __init__(self, runtime: Path):
        self.runtime = runtime
        self.path = runtime / "execution.sqlite3"
        if self.path.is_symlink():
            raise ValueError("ledger must not be a symlink")
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trades(id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS journal(seq INTEGER PRIMARY KEY, id TEXT, value TEXT NOT NULL);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value, allow_nan=False)))

    def trades(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT value FROM trades ORDER BY rowid")]

    def save(self, trade):
        encoded = json.dumps(trade, allow_nan=False)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO trades VALUES (?,?)", (trade["id"], encoded))
            db.execute("INSERT INTO journal(id,value) VALUES (?,?)", (trade["id"], encoded))

    @contextmanager
    def lock(self):
        path = self.runtime / "worker.lock"
        if path.is_symlink():
            raise ValueError("unsafe lock path")
        with path.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

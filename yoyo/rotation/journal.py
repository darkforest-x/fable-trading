"""Transactional local derived-observation journal; never a trade ledger.

Python3.9 SQLite transaction documentation:
https://docs.python.org/3.9/library/sqlite3.html#controlling-transactions
One scan id is immutable. States are namespaced by source/config/catalog and
historical/live mode. Old snapshots cannot turn the current state backwards.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from yoyo.contracts.rotation import RotationError, digest, safe_output, utc


class Journal:
    def __init__(self, directory: Path, *, enforce_path: bool = True):
        self.directory = safe_output(directory) if enforce_path else Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "observations.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scans (
                    scan_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, as_of TEXT NOT NULL,
                    generated_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS scans_namespace_time ON scans(namespace, as_of);
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, scan_id TEXT NOT NULL,
                    as_of TEXT NOT NULL, symbol TEXT NOT NULL, previous_state TEXT,
                    state TEXT NOT NULL, kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS states (
                    namespace TEXT NOT NULL, symbol TEXT NOT NULL, state TEXT NOT NULL,
                    as_of TEXT NOT NULL, PRIMARY KEY(namespace,symbol));
            """)

    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def namespace(snapshot: dict) -> str:
        return digest({k: snapshot.get(k) for k in ("schema_version", "mode", "config_hash", "source_hash", "catalog_hash")})

    def save(self, snapshot: dict) -> dict:
        namespace = self.namespace(snapshot)
        payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False)
        created = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM scans WHERE scan_id=?", (snapshot["scan_id"],)).fetchone():
                return {"inserted": False, "events_added": 0}
            latest = db.execute("SELECT as_of FROM scans WHERE namespace=? ORDER BY as_of DESC LIMIT 1", (namespace,)).fetchone()
            if latest and utc(snapshot["as_of"]) <= utc(latest["as_of"]):
                raise RotationError("older or revised same-cutoff scan cannot rewrite an immutable observation namespace")
            db.execute("INSERT INTO scans VALUES (?,?,?,?,?)", (snapshot["scan_id"], namespace,
                       snapshot["as_of"], snapshot["generated_at"], payload))
            for row in snapshot["candidates"]:
                symbol, state = row["symbol"], row.get("status", "unknown")
                previous = db.execute("SELECT state FROM states WHERE namespace=? AND symbol=?", (namespace, symbol)).fetchone()
                old = previous["state"] if previous else None
                if old != state:
                    event_id = digest([namespace, snapshot["as_of"], symbol, old, state])
                    cursor = db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?)",
                        (event_id, namespace, snapshot["scan_id"], snapshot["as_of"], symbol, old, state,
                         "first_observed" if old is None else "state_changed"))
                    created += cursor.rowcount
                db.execute("INSERT OR REPLACE INTO states VALUES (?,?,?,?)", (namespace, symbol, state, snapshot["as_of"]))
        return {"inserted": True, "events_added": created}

    def latest(self, *, config_hash: str = None, namespace: str = None) -> dict:
        with self.connect() as db:
            rows = db.execute("SELECT namespace,payload FROM scans ORDER BY generated_at DESC, rowid DESC")
            for row in rows:
                value = json.loads(row["payload"])
                if (config_hash is None or value["config_hash"] == config_hash) and (namespace is None or row["namespace"] == namespace):
                    return value
        raise RotationError("no saved observation for this configuration")

    def events(self, *, namespace: str = None, limit: int = 200) -> dict:
        with self.connect() as db:
            where, args = (" WHERE namespace=?", (namespace,)) if namespace else ("", ())
            count = db.execute("SELECT COUNT(*) FROM events" + where, args).fetchone()[0]
            rows = db.execute("SELECT * FROM events" + where + " ORDER BY as_of DESC,rowid DESC LIMIT ?", args + (limit,))
            return {"events": [dict(r) for r in rows], "total": count}

"""Private preview/confirmation queue; the execution worker remains sole writer.

Confirmation is an atomic, one-time transition for a saved detection. A lost
HTTP response returns the same intent on retry. Neither GET nor preview can
authorize execution. Superseded previews and expired tokens fail closed.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import sqlite3


class ReadLedger:
    def __init__(self, runtime):
        self.path = runtime / "execution.sqlite3"

    @contextmanager
    def connect(self):
        if self.path.is_symlink():
            raise ValueError("unsafe execution ledger")
        with closing(sqlite3.connect(self.path.as_uri()+"?mode=ro", uri=True, timeout=2)) as db:
            yield db

    def meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def trades(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT value FROM trades ORDER BY rowid")]


class Queue:
    def __init__(self, runtime):
        self.path = runtime / "manual.sqlite3"
        if self.path.is_symlink():
            raise ValueError("unsafe manual queue")
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, reference TEXT NOT NULL, state TEXT NOT NULL, value TEXT NOT NULL)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS confirmed_reference ON requests(reference) WHERE state='confirmed'")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db

    def preview(self, draft, *, retryable_ids=()):
        context = draft["event"]["detection"]
        reference = ":".join(context[k] for k in ("job_id", "symbol", "detection_id"))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT id FROM requests WHERE reference=? AND state='confirmed'", (reference,)).fetchone()
            if old:
                if old[0] not in retryable_ids:
                    raise ValueError("this detection already has a confirmed order")
                # Only a terminal, zero-fill execution result permits a new
                # owner-reviewed attempt. Preserve the original confirmation.
                db.execute("UPDATE requests SET state='finished' WHERE id=?", (old[0],))
            db.execute("UPDATE requests SET state='superseded' WHERE reference=? AND state='preview'", (reference,))
            db.execute("INSERT INTO requests VALUES (?,?,?,?)", (draft["id"], reference, "preview", json.dumps(draft, allow_nan=False)))
        return draft

    def get(self, identity):
        with self.connect() as db:
            row = db.execute("SELECT state,value FROM requests WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("manual preview not found")
        return dict(json.loads(row[1]), queue_state=row[0])

    def confirm(self, identity, now):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,value FROM requests WHERE id=?", (identity,)).fetchone()
            if not row:
                raise ValueError("manual preview not found")
            draft = json.loads(row[1])
            if row[0] in ("confirmed", "finished"):
                return draft
            if row[0] != "preview" or not draft["created_ms"] <= now < draft["expires_ms"]:
                raise ValueError("manual preview expired or superseded")
            draft["event"]["confirmed_ms"] = now
            db.execute("UPDATE requests SET state='confirmed',value=? WHERE id=?", (json.dumps(draft, allow_nan=False), identity))
        return draft

    def events(self):
        with self.connect() as db:
            return [json.loads(r[0])["event"] for r in db.execute("SELECT value FROM requests WHERE state='confirmed' ORDER BY rowid")]

    def recent(self):
        with self.connect() as db:
            return [json.loads(r[0])["id"] for r in db.execute("SELECT value FROM requests WHERE state IN ('confirmed','finished') ORDER BY rowid DESC LIMIT 20")]

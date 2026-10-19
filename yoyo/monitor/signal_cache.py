"""Bounded HTTP-only cohort cache; SQLite commits invalidate compact snapshots.

Keep time, freshness, calendar filters and analytics live on every request. Only
the expensive immutable-per-revision compact event projection is reused. A persistent
read-only connection's data_version detects commits from scanner/outbox writers;
filesystem identity also detects database replacement. No scanner or order path
uses this cache. SQL failures propagate instead of serving an unlabelled stale hit.
"""
from collections import OrderedDict
import json
import sqlite3
from threading import RLock
import time


class SignalReadCache:
    def __init__(self, store, *, max_entries=8, max_bytes=16_000_000, ttl=30):
        self.store = store
        self.max_entries, self.max_bytes, self.ttl = max_entries, max_bytes, ttl
        self._lock = RLock()
        self._db = None
        self._identity = self._version = None
        self._rows = OrderedDict()
        self._bytes = 0

    def __getattr__(self, name):
        return getattr(self.store, name)

    def close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
            self._db = None
            self._rows.clear()
            self._bytes = 0

    def _revision(self):
        stat = self.store.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self._db is None or identity != self._identity:
            self.close()
            self._db = sqlite3.connect(self.store.path.resolve().as_uri() + '?mode=ro',
                                       uri=True, check_same_thread=False, timeout=2)
            self._identity = identity
            self._version = None
        version = self._db.execute('PRAGMA data_version').fetchone()[0]
        if version != self._version:
            self._rows.clear()
            self._bytes = 0
            self._version = version

    def list_events(self, *args, **kwargs):
        # Do not cache legacy cursors, full evidence, or instrumentation calls.
        if not kwargs.get('complete') or not kwargs.get('summary') or kwargs.get('timing') is not None:
            return self.store.list_events(*args, **kwargs)
        key = json.dumps([args, kwargs], sort_keys=True, separators=(',', ':'))
        with self._lock:
            self._revision()
            now = time.monotonic()
            cached = self._rows.pop(key, None)
            if cached and now - cached[0] < self.ttl:
                self._rows[key] = cached
                # C JSON decoding is cheaper than recursively copying Python
                # containers; each response still owns independent nested data.
                return json.loads(cached[1])
            if cached:
                self._bytes -= cached[2]
            rows = self.store.list_events(*args, **kwargs)
            encoded = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            size = len(encoded)
            if size <= self.max_bytes:
                while self._rows and (len(self._rows) >= self.max_entries or self._bytes + size > self.max_bytes):
                    self._bytes -= self._rows.popitem(last=False)[1][2]
                self._rows[key] = (now, encoded, size)
                self._bytes += size
            return rows

"""V1 status responses retain their last complete snapshot during a WAL stall."""
from __future__ import annotations

import threading
import time

from yoyo.monitor.service import Monitor
from yoyo.monitor.store import Store


def test_status_returns_last_snapshot_while_a_store_read_is_blocked(tmp_path):
    class BlockingStore(Store):
        def __init__(self, path):
            super().__init__(path)
            self.entered = threading.Event()
            self.release = threading.Event()

        def market_phase_counts(self, timeframes):
            self.entered.set()
            self.release.wait(2)
            return {}

    store = BlockingStore(tmp_path / "monitor.sqlite3")
    monitor = Monitor(store)
    worker = threading.Thread(target=monitor.refresh_status)
    worker.start()
    assert store.entered.wait(.5)
    started = time.monotonic()
    snapshot = monitor.status()
    assert time.monotonic() - started < .1
    assert snapshot["scan"]["status"] == "starting"
    assert snapshot["snapshot_at_ms"] is None and snapshot["stale"] is True
    store.release.set()
    worker.join(2)
    assert not worker.is_alive()
    assert monitor.status()["stale"] is False

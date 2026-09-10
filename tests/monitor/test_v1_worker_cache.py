"""Persistent V1 worker cache tests; all exchange data is synthetic."""
from __future__ import annotations
import threading

from yoyo.monitor import MONITORED_TIMEFRAMES, TIMEFRAMES
from yoyo.monitor import v1_worker


class Client:
    def __init__(self):
        self.calls = []

    def synchronize(self):
        return 0

    def clock(self):
        return 1_900_000_000_000

    def instruments(self):
        return [{"instId": "TEST-USDT-SWAP", "tickSz": "0.01"}]

    def candles(self, symbol, timeframe, previous=None, limit=720):
        self.calls.append((symbol, timeframe, previous, limit))
        if previous:
            return previous, 0
        step = TIMEFRAMES[timeframe]
        return [{"t": self.clock() // step * step - step, "o": 100., "h": 101., "l": 99., "c": 100., "v": 1.}], 0


def test_persistent_worker_reuses_client_candles_and_skips_unchanged_replay(tmp_path, monkeypatch):
    client, calls = Client(), []
    monkeypatch.setattr(v1_worker, "OKX", lambda: client)
    def analyze(candles, higher, timeframe, *, tick):
        calls.append((timeframe, tuple(row["t"] for row in candles)))
        return {"events": [], "chart": list(candles), "state": {"phase": "ready", "ready": True, "timeframe": timeframe}}
    monkeypatch.setattr(v1_worker, "analyze", analyze)
    scanner = v1_worker.V1Scanner(str(tmp_path / "monitor.sqlite3"))
    scanner.scan_once()
    scanner.scan_once()
    assert [timeframe for timeframe, _ in calls] == list(MONITORED_TIMEFRAMES)
    assert len(client.calls) == 2 * len(MONITORED_TIMEFRAMES)
    assert all(previous for _, _, previous, _ in client.calls[len(MONITORED_TIMEFRAMES):])
    assert all(limit == 720 for _, _, _, limit in client.calls)


def test_prefetches_up_to_eight_cells_but_replays_and_writes_in_cell_order(tmp_path, monkeypatch):
    class PrefetchClient(Client):
        def __init__(self):
            super().__init__()
            self.started = []
            self.release = threading.Event()

        def instruments(self):
            return [{"instId": f"TEST{i}-USDT-SWAP", "tickSz": "0.01"} for i in range(3)]

        def candles(self, symbol, timeframe, previous=None, limit=720):
            self.started.append((symbol, timeframe))
            if len(self.started) <= 8:
                self.release.wait(.5)
            return super().candles(symbol, timeframe, previous, limit)

    client, replays = PrefetchClient(), []
    monkeypatch.setattr(v1_worker, "OKX", lambda: client)
    monkeypatch.setattr(v1_worker, "analyze", lambda candles, higher, timeframe, *, tick:
                        (replays.append(timeframe) or {"events": [], "chart": list(candles),
                                                        "state": {"phase": "ready", "ready": True}}))
    scanner = v1_worker.V1Scanner(str(tmp_path / "monitor.sqlite3"))
    thread = threading.Thread(target=scanner.scan_once)
    thread.start()
    for _ in range(100):
        if len(client.started) == 8:
            break
        threading.Event().wait(.01)
    assert len(client.started) == 8
    client.release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert replays == list(MONITORED_TIMEFRAMES) * 3

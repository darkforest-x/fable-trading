"""Persistent V1 worker cache tests; all exchange data is synthetic."""
from __future__ import annotations
import threading

from yoyo.monitor import MONITORED_TIMEFRAMES, TIMEFRAMES
from yoyo.monitor import v1_worker
from yoyo.monitor.signals import analyze as real_analyze
from yoyo.monitor.store import Store


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
    def analyze(candles, higher, timeframe, *, tick, chart_limit=None):
        assert chart_limit == 240
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
    monkeypatch.setattr(v1_worker, "analyze", lambda candles, higher, timeframe, *, tick, chart_limit:
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


def _complete_bars(timeframe, count=341):
    step = TIMEFRAMES[timeframe]
    return [{"t": index * step, "o": 100., "h": 101., "l": 99., "c": 100., "v": 100.}
            for index in range(count)]


def test_restart_hydrates_full_checkpoint_then_replays_exact_increment(tmp_path, monkeypatch):
    class SeedClient(Client):
        def candles(self, symbol, timeframe, previous=None, limit=720):
            self.calls.append((symbol, timeframe, previous, limit))
            assert previous is None
            return _complete_bars(timeframe), 0

    database = tmp_path / "monitor.sqlite3"
    seed = SeedClient()
    monkeypatch.setattr(v1_worker, "OKX", lambda: seed)
    first = v1_worker.V1Scanner(str(database))
    first.scan_once()

    class IncrementClient(Client):
        def candles(self, symbol, timeframe, previous=None, limit=720):
            self.calls.append((symbol, timeframe, previous, limit))
            assert previous == _complete_bars(timeframe)
            step = TIMEFRAMES[timeframe]
            return previous + [{"t": previous[-1]["t"] + step, "o": 100., "h": 101., "l": 99., "c": 100., "v": 100.}], 0

    increment = IncrementClient()
    monkeypatch.setattr(v1_worker, "OKX", lambda: increment)
    second = v1_worker.V1Scanner(str(database))
    assert all(second.candles[("TEST-USDT-SWAP", timeframe)] == _complete_bars(timeframe)
               for timeframe in MONITORED_TIMEFRAMES)
    second.scan_once()
    store = Store(database)
    for timeframe in MONITORED_TIMEFRAMES:
        full = _complete_bars(timeframe)
        step = TIMEFRAMES[timeframe]
        full.append({"t": full[-1]["t"] + step, "o": 100., "h": 101., "l": 99., "c": 100., "v": 100.})
        expected = real_analyze(full, [], timeframe, tick=.01, chart_limit=240)
        actual = store.get_market("TEST-USDT-SWAP", timeframe)
        assert actual["chart"] == expected["chart"]
        assert {key: actual[key] for key in expected["state"]} == expected["state"]
    assert all(previous for _, _, previous, _ in increment.calls)


def test_gap_checkpoint_is_not_restored_as_a_recurrence_seed(tmp_path, monkeypatch):
    database = tmp_path / "monitor.sqlite3"
    store = Store(database)
    step = TIMEFRAMES["1H"]
    store.save_candle_checkpoint("TEST-USDT-SWAP", "1H", [
        {"t": 0, "o": 100., "h": 101., "l": 99., "c": 100., "v": 1.},
        {"t": 2 * step, "o": 100., "h": 101., "l": 99., "c": 100., "v": 1.},
    ])
    client = Client()
    monkeypatch.setattr(v1_worker, "OKX", lambda: client)
    scanner = v1_worker.V1Scanner(str(database))
    assert ("TEST-USDT-SWAP", "1H") not in scanner.candles


def test_fractional_checkpoint_timestamp_is_not_restored(tmp_path, monkeypatch):
    database = tmp_path / "monitor.sqlite3"
    store = Store(database)
    store.save_candle_checkpoint("TEST-USDT-SWAP", "1H", [
        {"t": 0.5, "o": 100., "h": 101., "l": 99., "c": 100., "v": 1.},
    ])
    client = Client()
    monkeypatch.setattr(v1_worker, "OKX", lambda: client)
    scanner = v1_worker.V1Scanner(str(database))
    assert ("TEST-USDT-SWAP", "1H") not in scanner.candles

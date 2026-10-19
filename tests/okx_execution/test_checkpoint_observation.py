"""Closed-seed age must not replace successful scan age; no exchange calls."""
import pytest

from yoyo.monitor import FRESH_MS, TIMEFRAMES, V9_RESET_KEY
from yoyo.monitor import store as monitor_store, v9_worker, v130_worker
from yoyo.monitor.store import Store
from yoyo.okx_execution.source import Source


SYMBOL = "ALT-USDT-SWAP"
STEP = TIMEFRAMES["4H"]
CLOSE = 10 * STEP


def seed(tmp_path, monkeypatch):
    clock = [CLOSE + 1_000]
    monkeypatch.setattr(monitor_store, "now_ms", lambda: clock[0])
    store = Store(tmp_path / "monitor.sqlite3")
    store.save_candle_checkpoint(SYMBOL, "4H", [
        dict(t=CLOSE - STEP, o=100., h=101., l=99., c=100., v=10.)])
    store.upsert_market(dict(symbol=SYMBOL, timeframe="4H", tick_size="0.01"))
    return store, Source(tmp_path), clock


def raw_checkpoint(store):
    with store.connect() as db:
        return db.execute("SELECT payload,updated_ms FROM candle_checkpoints").fetchone()


def test_unchanged_four_hour_seed_accepts_current_observation_without_rewrite(tmp_path, monkeypatch):
    store, source, clock = seed(tmp_path, monkeypatch)
    original = raw_checkpoint(store)
    clock[0] = CLOSE + 3 * TIMEFRAMES["1H"]
    with pytest.raises(ValueError, match="stale"):
        source.checkpoint(SYMBOL, "4H", clock[0])
    store.observe_candle_checkpoint(SYMBOL, "4H", CLOSE)
    cp = source.checkpoint(SYMBOL, "4H", clock[0])
    assert cp["updated_ms"] == original[1]
    assert source.bars(SYMBOL, "4H", CLOSE, clock[0]) == []
    assert raw_checkpoint(store) == original
    assert source.checkpoint(SYMBOL, "4H", clock[0] + FRESH_MS)
    with pytest.raises(ValueError, match="stale"):
        source.checkpoint(SYMBOL, "4H", clock[0] + FRESH_MS + 1)


@pytest.mark.parametrize("change", [
    {"checkpoint_updated_ms": 1}, {"close_ms": CLOSE - STEP},
    {"observed_ms": CLOSE + STEP}, {"observed_ms": "new"},
    {"observed_ms": CLOSE}, {},
])
def test_wrong_revision_clock_or_bar_cannot_refresh_an_old_seed(tmp_path, monkeypatch, change):
    store, source, clock = seed(tmp_path, monkeypatch)
    clock[0] = CLOSE + 3 * TIMEFRAMES["1H"]
    receipt = dict(observed_ms=clock[0], checkpoint_updated_ms=CLOSE + 1_000, close_ms=CLOSE)
    receipt.update(change)
    if not change:
        receipt = []
    store.set_meta(f"candle_observation:{SYMBOL}:4H", receipt)
    with pytest.raises(ValueError, match="stale"):
        source.checkpoint(SYMBOL, "4H", clock[0])


def test_recent_observation_does_not_hide_a_missing_completed_candle(tmp_path, monkeypatch):
    store, source, clock = seed(tmp_path, monkeypatch)
    clock[0] = CLOSE + STEP + FRESH_MS
    store.observe_candle_checkpoint(SYMBOL, "4H", CLOSE)
    with pytest.raises(ValueError, match="latest completed candle"):
        source.checkpoint(SYMBOL, "4H", clock[0])


def test_scanner_refreshes_only_successful_reads_and_skips_unchanged_replay(tmp_path, monkeypatch):
    store, source, clock = seed(tmp_path, monkeypatch)
    store.set_meta(V9_RESET_KEY, dict(activated_ms=CLOSE - 1))
    monkeypatch.setattr(v9_worker, "MONITORED_TIMEFRAMES", ("4H",))
    monkeypatch.setattr(v9_worker, "now_ms", lambda: clock[0])
    replays = []

    class Client:
        fail = False
        def __init__(self, **kwargs): pass
        def synchronize(self): return 0
        def clock(self): return clock[0]
        def instruments(self):
            return [dict(instId=SYMBOL, tickSz="0.01", instType="SWAP", state="live",
                         ctType="linear", settleCcy="USDT")]
        def candles(self, symbol, timeframe, previous=None, limit=720):
            if self.fail:
                raise RuntimeError("synthetic read failure")
            step = TIMEFRAMES[timeframe]
            return [dict(t=CLOSE-step, o=100., h=101., l=99., c=100., v=10.)], 0

    def analyze(candles, higher, timeframe, **kwargs):
        replays.append(timeframe)
        return dict(events=[], chart=[], state=dict(timeframe=timeframe))

    monkeypatch.setattr(v9_worker, "OKX", Client)
    monkeypatch.setattr(v9_worker, "analyze", analyze)
    monkeypatch.setattr(v130_worker, "observe", lambda *args, **kwargs: None)
    scanner = v9_worker.V9Scanner(str(store.path))
    scanner.scan_once()
    with store.connect() as db:
        original = db.execute("SELECT payload,updated_ms FROM candle_checkpoints WHERE timeframe='4H'").fetchone()
    first_replays = list(replays)
    clock[0] = CLOSE + 3 * TIMEFRAMES["1H"]
    scanner.scan_once()
    receipt = store.get_meta(f"candle_observation:{SYMBOL}:4H")
    assert receipt["observed_ms"] == clock[0]
    assert source.checkpoint(SYMBOL, "4H", clock[0])
    assert replays == first_replays
    with store.connect() as db:
        assert db.execute("SELECT payload,updated_ms FROM candle_checkpoints WHERE timeframe='4H'").fetchone() == original
    scanner.client.fail = True
    clock[0] += FRESH_MS + 1
    scanner.scan_once()
    assert store.get_meta(f"candle_observation:{SYMBOL}:4H") == receipt
    with pytest.raises(ValueError, match="stale"):
        source.checkpoint(SYMBOL, "4H", clock[0])

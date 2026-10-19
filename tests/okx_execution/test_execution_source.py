"""Read-only signal identity, all-period scope and no outcome leakage."""
import json
import sqlite3
import pytest

from yoyo.monitor import TIMEFRAMES
from yoyo.monitor.v130_policy import SIGNAL_KIND, SIGNAL_PROTOCOL, VERSION, is_v130_signal
from yoyo.monitor.v130_signals import SOURCE_SHA256
from yoyo.okx_execution.source import Source


def event(tf, symbol):
    step = TIMEFRAMES[tf]
    return dict(id=tf, symbol=symbol, timeframe=tf, timeframe_min=step // 60000, side="long", direction="long",
                protocol=SIGNAL_PROTOCOL, kind=SIGNAL_KIND, strategy_version=VERSION, source="live", confirmation="retest",
                v130_admitted=True, confirmed=True, is_closed=True, is_trade=False, entry_reference="next_open_reference_not_fill",
                candidate_source="ordinary_both", source_sha256=SOURCE_SHA256, anchor_close_ms=step,
                breakout_close_ms=2*step, retest_close_ms=3*step, bar_close_ms=4*step, bar_open_ms=3*step,
                detected_at_ms=4*step+1000, wait_bars=3, price=100., reference_price=100., risk=2., initial_stop=98.,
                performance={"entry_price": 1, "exit_r": 1000})


def populate(path, events):
    with sqlite3.connect(path / "monitor.sqlite3") as db:
        db.execute("CREATE TABLE events(id,symbol,timeframe,kind,side,close_ms,detected_ms,payload)")
        db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", [
            (e["id"], e["symbol"], e["timeframe"], e["kind"], e["side"], e["bar_close_ms"], e["detected_at_ms"], json.dumps(e))
            for e in events])


def test_all_timeframes_altcoins_and_stock_like_instruments_no_outcomes(tmp_path):
    events = [event(tf, symbol) for tf, symbol in zip(TIMEFRAMES, ["DOGE-USDT-SWAP", "PEPE-USDT-SWAP", "AAPL-USDT-SWAP", "BTC-USDT-SWAP"])]
    assert all(is_v130_signal(e) for e in events)
    populate(tmp_path, events)
    before = (tmp_path / "monitor.sqlite3").read_bytes()
    rows = Source(tmp_path).events(0, 100_000_000)
    assert {e["timeframe"] for e in rows} == set(TIMEFRAMES)
    assert all("performance" not in e for e in rows)
    assert (tmp_path / "monitor.sqlite3").read_bytes() == before
    assert Source(tmp_path).events(100_000_000, 100_000_000) == []


def test_db_payload_mismatch_fails_explicitly(tmp_path):
    populate(tmp_path, [event("15m", "DOGE-USDT-SWAP")])
    with sqlite3.connect(tmp_path / "monitor.sqlite3") as db:
        db.execute("UPDATE events SET symbol='OTHER-USDT-SWAP'")
    with pytest.raises(ValueError, match="mismatch"): Source(tmp_path).events(0, 100_000_000)


def test_exclusions_filter_admission_without_deleting_history_or_blocking_checkpoints(tmp_path, monkeypatch):
    events = [event(tf, symbol) for tf, symbol in zip(TIMEFRAMES,
        ['GMT-USDT-SWAP', 'ETH-USDT-SWAP', 'BTC-USD-SWAP', 'ICP-USDT-SWAP'])]
    populate(tmp_path, events)
    before = (tmp_path / 'monitor.sqlite3').read_bytes()
    source = Source(tmp_path)
    assert [e['symbol'] for e in source.events(0, 100_000_000)] == ['ETH-USDT-SWAP']
    assert (tmp_path / 'monitor.sqlite3').read_bytes() == before
    monkeypatch.setattr(source, 'checkpoint', lambda *args: dict(candles=[dict(t=0)]))
    assert source.bars('ICP-USDT-SWAP', '15m', 900000, 1000000) == []


def test_checkpoint_freshness_respects_long_timeframe(monkeypatch, tmp_path):
    source = Source(tmp_path)
    now = 50_000_000
    monkeypatch.setattr(source.checkpoints, "checkpoint", lambda *args: dict(
        candles=[dict(t=28_800_000)], tick=.1, updated_ms=now-1000))
    assert source.checkpoint("DOGE-USDT-SWAP", "4H", now)
    with pytest.raises(ValueError, match="stale"):
        source.checkpoint("DOGE-USDT-SWAP", "4H", now+1_800_000)

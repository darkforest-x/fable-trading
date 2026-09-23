from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest

from yoyo.research_workspace.paper_source import MonitorSource, evaluate_trade, source_manifest
from yoyo.research_workspace.strategies import catalog, get_plugin


PERIOD = 900_000


def _raw_database(path: Path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE events(id TEXT PRIMARY KEY, symbol TEXT, timeframe TEXT, kind TEXT,
          side TEXT, close_ms INTEGER, detected_ms INTEGER, payload TEXT);
        CREATE TABLE markets(symbol TEXT, timeframe TEXT, payload TEXT, PRIMARY KEY(symbol,timeframe));
        CREATE TABLE candle_checkpoints(symbol TEXT, timeframe TEXT, payload BLOB,
          updated_ms INTEGER, PRIMARY KEY(symbol,timeframe));
        CREATE TABLE meta(key TEXT PRIMARY KEY, payload TEXT);
    """)
    return db


def _raw_payload(event_id: str, *, protocol="spike-burst-v128-monitor-v1", source="live",
                 confirmation="raw", is_closed=True, side="long", symbol="BTC-USDT-SWAP",
                 close_ms=None, detected_ms=None, initial_stop=99.0):
    return {
        "id": event_id, "protocol": protocol, "kind": "spike_burst_v128", "source": source,
        "confirmation": confirmation, "is_closed": is_closed, "side": side, "direction": side,
        "symbol": symbol, "timeframe": "15m", "bar_open_ms": close_ms - PERIOD,
        "bar_close_ms": close_ms, "detected_at_ms": detected_ms,
        "initial_stop": initial_stop, "performance": {"status": "active", "net_r": 9999},
    }


def test_catalog_is_static_lightweight_and_fails_closed_for_unsupported_plugins(tmp_path):
    items = catalog(tmp_path)
    assert [item["id"] for item in items] == [
        "spike-v128", "spike-v128-joint", "trendline-breakout", "yolo-confirmed"
    ]
    assert all(item["production_eligible"] is False for item in items)
    assert all(isinstance(item["notes"], str) for item in items)
    assert get_plugin("spike-v128").exit_adapter == "spike-v128-frozen"
    assert get_plugin("spike-v128-joint").side == "long"
    assert get_plugin("trendline-breakout").paper_supported is False
    assert get_plugin("yolo-confirmed").paper_supported is False
    with pytest.raises(ValueError, match="frozen paper exit adapter"):
        get_plugin("trendline-breakout").evaluate_trade([], "15m", "BTC-USDT-SWAP", .01, {}, 1)


def test_raw_source_filters_foreign_warmup_and_pre_activation_rows_read_only(tmp_path):
    path = tmp_path / "monitor.sqlite3"
    db = _raw_database(path)
    now = time.time_ns() // 1_000_000
    close = now // PERIOD * PERIOD - PERIOD
    detected = close + 1_000
    db.execute("INSERT INTO markets VALUES(?,?,?)", (
        "BTC-USDT-SWAP", "15m", json.dumps({"symbol": "BTC-USDT-SWAP", "timeframe": "15m", "tick_size": "0.01"})
    ))
    valid = _raw_payload("valid", close_ms=close, detected_ms=detected)
    foreign = _raw_payload("foreign", protocol="some-other-monitor", close_ms=close, detected_ms=detected)
    warmup = _raw_payload("warmup", source="history", close_ms=close, detected_ms=detected)
    unclosed = _raw_payload("unclosed", is_closed=False, close_ms=close, detected_ms=detected)
    for event in (valid, foreign, warmup, unclosed):
        db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (
            event["id"], event["symbol"], "15m", event["kind"], event["side"], close,
            detected, json.dumps(event),
        ))
    db.commit()
    db.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    got = MonitorSource(tmp_path).events(get_plugin("spike-v128"), after_ms=detected - 1)

    assert len(got) == 1
    assert got[0]["id"] == "valid"
    assert got[0]["initial_stop"] == 99.0
    assert got[0]["tick"] == pytest.approx(.01)
    assert got[0]["source_payload"]["performance"]["net_r"] == 9999
    assert got[0]["cursor"] == (detected, "valid")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_raw_source_paginates_tied_detection_times_without_omissions(tmp_path):
    path = tmp_path / "monitor.sqlite3"
    db = _raw_database(path)
    now = time.time_ns() // 1_000_000
    close = now // PERIOD * PERIOD - PERIOD
    detected = close + 1_000
    symbol = "BTC-USDT-SWAP"
    db.execute("INSERT INTO markets VALUES(?,?,?)", (
        symbol, "15m", json.dumps({"tick_size": .01})
    ))
    for i in range(2_005):
        event_id = f"event-{i:04d}"
        event = _raw_payload(event_id, symbol=symbol, close_ms=close, detected_ms=detected)
        db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (
            event_id, symbol, "15m", event["kind"], event["side"], close, detected, json.dumps(event),
        ))
    db.commit()
    db.close()
    source = MonitorSource(tmp_path)
    plugin = get_plugin("spike-v128")
    first = source.events(plugin, after_ms=detected)
    second = source.events(plugin, after_ms=first[-1]["cursor"])
    ids = [item["id"] for item in first + second]
    assert len(first) == 2_000
    assert len(second) == 5
    assert len(ids) == len(set(ids)) == 2_005


def test_joint_source_uses_its_actual_schema_and_signal_stop(tmp_path):
    path = tmp_path / "spike-lines-v1.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE events(id TEXT PRIMARY KEY, kind TEXT, symbol TEXT, timeframe TEXT,
          bar_open_ms INTEGER, bar_close_ms INTEGER, detected_at_ms INTEGER, payload TEXT);
        CREATE TABLE meta(key TEXT PRIMARY KEY, payload TEXT);
    """)
    now = time.time_ns() // 1_000_000
    close = now // PERIOD * PERIOD - PERIOD
    detected = close + 1_000
    event = {
        "id": "joint-1", "kind": "joint", "protocol": "spike-v128-lines-monitor-v1",
        "symbol": "BTC-USDT-SWAP", "timeframe": "15m", "side": "long",
        "bar_open_ms": close - PERIOD, "bar_close_ms": close, "detected_at_ms": detected,
        "close": 100.0, "source": "chart", "v9_signal_open_ms": close - PERIOD,
        "v9_signal_close_ms": close, "reference_stop": 98.0, "tick": .1,
    }
    db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (
        event["id"], event["kind"], event["symbol"], event["timeframe"],
        event["bar_open_ms"], event["bar_close_ms"], detected, json.dumps(event),
    ))
    db.commit()
    db.close()

    got = MonitorSource(tmp_path).events(get_plugin("spike-v128-joint"), after_ms=detected)
    assert len(got) == 1
    assert got[0]["side"] == "long"
    assert got[0]["initial_stop"] == 98.0
    assert got[0]["tick"] == pytest.approx(.1)


def _candle(stamp: int, *, open_=100.0, high=101.0, low=99.0, close=100.0, volume=100.0):
    return {"t": stamp, "o": open_, "h": high, "l": low, "c": close, "v": volume}


def test_checkpoint_readonly_filters_developing_and_future_deduplicates_identical_rows(tmp_path):
    path = tmp_path / "monitor.sqlite3"
    db = _raw_database(path)
    rows = [_candle(0), _candle(0), _candle(PERIOD), _candle(2 * PERIOD)]
    db.execute("INSERT INTO candle_checkpoints VALUES(?,?,?,?)", (
        "BTC-USDT-SWAP", "15m", gzip.compress(json.dumps(rows).encode()), 12345,
    ))
    db.execute("INSERT INTO markets VALUES(?,?,?)", (
        "BTC-USDT-SWAP", "15m", json.dumps({"tick_size": "0.01"}),
    ))
    db.commit()
    db.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    checkpoint = MonitorSource(tmp_path).checkpoint("BTC-USDT-SWAP", "15m", PERIOD)

    assert checkpoint == {"candles": [_candle(0)], "tick": .01, "updated_ms": 12345}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert MonitorSource(tmp_path).checkpoint("ETH-USDT-SWAP", "15m", PERIOD) is None


def test_checkpoint_rejects_conflicting_duplicate_clocks_and_symlink_database(tmp_path):
    path = tmp_path / "monitor.sqlite3"
    db = _raw_database(path)
    rows = [_candle(0), _candle(0, close=100.5)]
    db.execute("INSERT INTO candle_checkpoints VALUES(?,?,?,?)", (
        "BTC-USDT-SWAP", "15m", gzip.compress(json.dumps(rows).encode()), 1,
    ))
    db.execute("INSERT INTO markets VALUES(?,?,?)", (
        "BTC-USDT-SWAP", "15m", json.dumps({"tick_size": .01}),
    ))
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="duplicate checkpoint"):
        MonitorSource(tmp_path).checkpoint("BTC-USDT-SWAP", "15m", PERIOD)

    outside = tmp_path / "elsewhere.sqlite3"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="missing or symlinked"):
        MonitorSource(tmp_path).checkpoint("BTC-USDT-SWAP", "15m", PERIOD)


def _series(count=390, target=380, *, target_low=99.0, target_high=101.0):
    rows = []
    for i in range(count):
        low, high = (target_low, target_high) if i == target else (99.0, 101.0)
        rows.append(_candle(i * PERIOD, low=low, high=high, close=100.0))
    return rows


def _decision(target=380, *, side="long", stop=99.0):
    scheduled = target * PERIOD
    return {"side": side, "signal_close_ms": scheduled,
            "scheduled_entry_ms": scheduled, "initial_stop": stop}


def test_evaluate_trade_calls_fixed_kernel_uses_signal_stop_20bp_and_stop_first(monkeypatch):
    from yoyo.evaluation import spike_v1_v8_be05 as fixed

    original = fixed.replay_fixed_entry
    kernel = {}

    def capture(*args, **kwargs):
        value = original(*args, **kwargs)
        kernel.update(value)
        return value

    monkeypatch.setattr(fixed, "replay_fixed_entry", capture)
    result = evaluate_trade(_series(target_low=98.0), "15m", "BTC-USDT-SWAP", .01,
                            _decision(stop=99.0), 381 * PERIOD)
    assert result["status"] == "closed"
    assert result["kernel_reason"] == "initial_stop"
    assert result["exit_price"] == pytest.approx(99.0)
    assert result["initial_stop"] == pytest.approx(99.0)  # never rederived from the bars
    assert result["initial_risk"] == pytest.approx(1.0)
    assert result["exit_time_precision"] == "within_bar"
    assert result["exit_time_ms"] == 380 * PERIOD
    assert result["exit_bar_end_ms"] == 381 * PERIOD
    assert result["net_r"] == pytest.approx(kernel["net_r"])
    assert result["net_r"] == pytest.approx(-1.2)  # 1% stop risk plus 20bp round-trip cost
    assert result["kernel_exit_time_precision"] == "bar_open_or_intrabar_window"


def test_evaluate_trade_supports_short_and_rejects_missing_or_wrong_stop():
    short = evaluate_trade(_series(target_high=102.0), "15m", "BTC-USDT-SWAP", .01,
                           _decision(side="short", stop=101.0), 381 * PERIOD)
    assert short["status"] == "closed"
    assert short["kernel_reason"] == "initial_stop"
    assert short["exit_price"] == pytest.approx(101.0)
    assert short["net_r"] == pytest.approx(-1.2)

    missing = evaluate_trade(_series(), "15m", "BTC-USDT-SWAP", .01,
                             _decision(stop=None), 381 * PERIOD)
    wrong_side = evaluate_trade(_series(), "15m", "BTC-USDT-SWAP", .01,
                                _decision(side="long", stop=101.0), 381 * PERIOD)
    assert (missing["status"], missing["reason"]) == ("rejected", "missing_initial_stop")
    assert (wrong_side["status"], wrong_side["reason"]) == ("rejected", "invalid_initial_risk")


def test_evaluate_trade_pending_missing_entry_gap_censor_and_holding_gap():
    target = 380
    bars_before = _series(count=target, target=target)
    not_open = evaluate_trade(bars_before, "15m", "BTC-USDT-SWAP", .01,
                              _decision(target=target), target * PERIOD - 1)
    awaiting = evaluate_trade(bars_before, "15m", "BTC-USDT-SWAP", .01,
                              _decision(target=target), target * PERIOD + PERIOD - 1)
    assert (not_open["status"], not_open["reason"]) == ("pending", "entry_not_open")
    assert (awaiting["status"], awaiting["reason"]) == ("pending", "awaiting_closed_entry_bar")

    missing_entry = bars_before + [_candle((target + 1) * PERIOD)]
    censored_entry = evaluate_trade(missing_entry, "15m", "BTC-USDT-SWAP", .01,
                                   _decision(target=target), (target + 2) * PERIOD)
    assert (censored_entry["status"], censored_entry["reason"]) == ("censored", "scheduled_entry_bar_missing")

    holding_gap = _series(count=target + 1, target=target, target_low=99.5, target_high=100.5)
    holding_gap.append(_candle((target + 2) * PERIOD, low=99.5, high=100.5))
    censored = evaluate_trade(holding_gap, "15m", "BTC-USDT-SWAP", .01,
                              _decision(target=target, stop=90.0), (target + 3) * PERIOD)
    assert (censored["status"], censored["reason"]) == ("censored", "data_gap_censored")


def test_evaluate_trade_future_candle_cannot_change_open_mark_and_has_no_exit_time():
    rows = _series(target_low=99.5, target_high=100.5)[:381]
    decision = _decision(stop=90.0)
    now = 381 * PERIOD
    plain = evaluate_trade(rows, "15m", "BTC-USDT-SWAP", .01, decision, now)
    mutated = evaluate_trade(rows + [_candle(381 * PERIOD, open_=100.0, high=500.0, low=1.0, close=300.0)],
                             "15m", "BTC-USDT-SWAP", .01, decision, now)
    assert plain["status"] == mutated["status"] == "open"
    assert plain["exit_time_ms"] is None
    assert plain["mark_time_ms"] == 381 * PERIOD
    assert plain["mark_price"] == mutated["mark_price"] == pytest.approx(100.0)
    assert plain["unrealized_r"] == mutated["unrealized_r"]


def test_source_manifest_pins_code_locks_and_runtime_versions_without_git_hashing(tmp_path):
    with pytest.raises(ValueError, match="source files are missing"):
        source_manifest(tmp_path)
    root = Path(__file__).resolve().parents[2]
    manifest = source_manifest(root)
    assert "yoyo/evaluation/spike_v1_v8_be05.py" in manifest["files"]
    assert "yoyo/monitor/v128_signals.py" in manifest["files"]
    assert "requirements.txt" in manifest["files"]
    assert "constraints-ci.txt" in manifest["files"]
    assert set(manifest["environment"]["packages"]) == {"numpy", "pandas", "numba"}
    assert len(manifest["hash"]) == len(manifest["dependency_hash"]) == 64


def test_missing_monitor_source_information_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="database missing"):
        MonitorSource(tmp_path).events(get_plugin("spike-v128"), after_ms=0)
    with pytest.raises(ValueError, match="database missing"):
        MonitorSource(tmp_path).checkpoint("BTC-USDT-SWAP", "15m", PERIOD)

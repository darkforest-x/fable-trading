"""Current-Pine V1 short display stream: causal, separate, and notification-free."""
from __future__ import annotations

import pandas as pd
import pytest

from yoyo.monitor import (SHORT_DISPLAY_CUTOVER_KEY, SHORT_SIGNAL_KIND,
                          SHORT_SIGNAL_PROTOCOL, SIGNAL_PROTOCOL, TIMEFRAMES)
from yoyo.monitor import signals, v1_worker
from yoyo.monitor.notification_policy import delivery_error
from yoyo.monitor.store import Store
from yoyo.monitor.v1_short_replay import replay_short


STEP = TIMEFRAMES["1H"]


def _feature_frame(count: int = 355) -> pd.DataFrame:
    """A Pine-equivalent qualified quiet box followed by a true short burst."""
    index = pd.to_datetime([i * STEP for i in range(count)], unit="ms", utc=True)
    frame = pd.DataFrame({
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "md": 0.0, "sb": 0.0, "middle": 0.0, "atr": 1.0,
        "pastWidth": 0.0, "pastCrosses": 2.0, "ropeHigh": 100.0, "ropeLow": 100.0,
        "recentLow": 99.0, "recentHigh": 101.0, "rv": 1.0, "expansion": 1.0, "ready": False,
    }, index=index)
    frame.loc[index[340]:, "ready"] = True
    # Bar 352 closes below the frozen 12-bar box and the six-MA rope with all
    # current force gates.  Its stop must use the high, never a long low.
    frame.iloc[352, frame.columns.get_loc("high")] = 101.0
    frame.iloc[352, frame.columns.get_loc("low")] = 59.0
    frame.iloc[352, frame.columns.get_loc("close")] = 60.0
    frame.iloc[352, frame.columns.get_loc("md")] = -1.0
    frame.iloc[352, frame.columns.get_loc("sb")] = -1.0
    frame.iloc[352, frame.columns.get_loc("middle")] = -1.0
    frame.iloc[352, frame.columns.get_loc("rv")] = 4.0
    frame.iloc[352, frame.columns.get_loc("expansion")] = 3.0
    if count > 353:
        # A lower close is a positive short R before protection is touched.
        frame.iloc[353, frame.columns.get_loc("close")] = 50.0
        frame.iloc[353, frame.columns.get_loc("low")] = 49.0
    if count > 354:
        # The next high touches the short's upside protection and produces -1R.
        frame.iloc[354, frame.columns.get_loc("high")] = 102.0
        frame.iloc[354, frame.columns.get_loc("low")] = 50.0
        frame.iloc[354, frame.columns.get_loc("close")] = 101.0
    return frame


def _candles(count: int) -> list[dict]:
    return [{"t": i * STEP, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "v": 1.0}
            for i in range(count)]


def test_current_pine_short_replay_uses_high_stop_and_directional_r_path():
    replayed = replay_short(_feature_frame(), .01)
    assert replayed.burst_down.to_numpy().nonzero()[0].tolist() == [352]
    assert not bool(replayed.burst_up.any())
    assert replayed.loc[replayed.index[352], "initial_stop"] == pytest.approx(101.2)
    assert replayed.loc[replayed.index[352], "risk"] == pytest.approx(41.2)
    assert replayed.loc[replayed.index[353], "current_r"] > 0
    assert replayed.loc[replayed.index[354], "exit"]
    assert replayed.loc[replayed.index[354], "current_r"] == pytest.approx(-1.0)


def test_short_adapter_is_closed_bar_causal_and_does_not_change_long(monkeypatch):
    featured = _feature_frame()
    with monkeypatch.context() as patched:
        patched.setattr(signals, "features", lambda raw: featured.iloc[:len(raw)].copy())
        prefix = signals.analyze_short(_candles(354), [], "1H", tick=.01)
        full = signals.analyze_short(_candles(355), [], "1H", tick=.01)
    assert len(prefix["events"]) == len(full["events"]) == 1
    event = full["events"][0]
    assert event["direction"] == event["side"] == "short"
    assert event["bar_open_ms"] == 352 * STEP
    assert event["bar_close_ms"] == 353 * STEP
    assert event["protocol"] == SHORT_SIGNAL_PROTOCOL and event["kind"] == SHORT_SIGNAL_KIND
    assert event["source_sha256"] == signals.SOURCE_SHA256
    assert event["pine_direction_setting"] == "空头"
    assert event["tradingview_default_is_short"] is False
    assert full.event_performance[event["bar_close_ms"]]["basis"] == "v1_short_signal_close_reference"
    assert full.event_performance[event["bar_close_ms"]]["current_r"] == pytest.approx(-1.0)
    # The frozen long public adapter retains its old identity and output path.
    assert signals.PROTOCOL["direction"] == "long_only"
    assert signals.analyze(_candles(354), [], "1H", tick=.01)["events"] == []


def test_warm_restart_backfills_short_history_without_bark_or_yolo(tmp_path, monkeypatch):
    now = 500 * STEP + 1_000
    close = 341 * STEP
    database = tmp_path / "monitor.sqlite3"
    store = Store(database)
    rows = _candles(341)
    for timeframe, step in TIMEFRAMES.items():
        source = [{**row, "t": index * step} for index, row in enumerate(rows)]
        store.save_candle_checkpoint("TEST-USDT-SWAP", timeframe, source)
        store.set_meta(f"v1:last_closed:TEST-USDT-SWAP:{timeframe}", source[-1]["t"] + step)

    class Client:
        def synchronize(self): return 0
        def clock(self): return now
        def instruments(self): return [{"instId": "TEST-USDT-SWAP", "tickSz": ".01"}]
        def candles(self, symbol, timeframe, previous=None, limit=720):
            assert previous is not None
            return previous, 0

    def long_result(candles, higher, timeframe, *, tick, chart_limit):
        return {"events": [], "chart": [], "state": {"phase": "ready", "ready": True, "timeframe": timeframe}}

    def short_result(candles, higher, timeframe, *, tick, chart_limit):
        if timeframe != "1H":
            return {"events": [], "chart": [], "state": {"phase": "ready", "ready": True}}
        event = {"protocol": SHORT_SIGNAL_PROTOCOL, "kind": SHORT_SIGNAL_KIND, "source": "live",
                 "confirmation": "raw", "direction": "short", "side": "short", "timeframe": timeframe,
                 "timeframe_min": 60, "bar_open_ms": close - STEP, "bar_close_ms": close,
                 "signal_close_time": close, "is_closed": True, "confirmed": True, "ready": True,
                 "price": 100.0, "risk": 2.0, "initial_stop": 102.0, "source_sha256": signals.SOURCE_SHA256,
                 "pine_direction_setting": "空头", "tradingview_default_direction": "多头",
                 "tradingview_default_is_short": False, "entry_reference": "next_open",
                 "executable_entry_time": None}
        result = signals.AnalysisResult({"events": [event], "chart": [], "state": {"phase": "ready", "ready": True}})
        result.event_performance = {close: {"status": "active", "current_r": 0.0, "basis": "v1_short_signal_close_reference"}}
        return result

    monkeypatch.setattr(v1_worker, "OKX", Client)
    monkeypatch.setattr(v1_worker, "analyze", long_result)
    monkeypatch.setattr(v1_worker, "analyze_short", short_result)
    scanner = v1_worker.V1Scanner(str(database))
    scanner.scan_once()

    event = store.list_events(protocol=SHORT_SIGNAL_PROTOCOL)[0]
    assert event["bar_close_ms"] == close
    assert event["notification_status"] == event["bark_notification_status"] == "history"
    assert store.bark_status()["pending"] == store.telegram_status()["pending"] == 0
    assert store.list_candidates() == []
    assert store.get_meta(SHORT_DISPLAY_CUTOVER_KEY)["activated_ms"] == now
    assert store.get_meta("v1short:last_closed:TEST-USDT-SWAP:1H") == close
    assert delivery_error(store, event, now, "bark") == "short_display_only"
    assert store.get_event(event["id"])["protocol"] != SIGNAL_PROTOCOL

"""Injected RSI exit projection contracts for the SPIKE line monitor."""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.monitor import spike_lines as subject


def _frame(n=40):
    index = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    close = np.full(n, 100.0)
    return pd.DataFrame({"open": close, "high": close + .5, "low": close - .5,
                         "close": close, "atr": 1.0, "ready": True}, index=index)


def _features(index, *, strong=(), known=True):
    n = len(index)
    side = np.zeros(n, dtype=int)
    streak = pd.array([pd.NA] * n, dtype="Int64")
    for i in strong:
        side[i], streak[i] = -1, 1  # entry-local helper derives the actual run.
    return pd.DataFrame({"strong_side": side, "strong_streak": streak,
                         "known": np.full(n, known, dtype=bool)}, index=index)


def _facts(n):
    return {"gap": np.zeros(n, dtype=bool), "side": np.zeros(n, dtype=int)}


def test_injected_exact_seventh_exits_at_next_open_then_allows_serial_reentry():
    frame = _frame()
    frame.loc[frame.index[18], ["open", "high", "low", "close"]] = [99.0, 100.0, 98.5, 99.0]
    fired = np.zeros(len(frame), dtype=bool); fired[[10, 18]] = True
    got = subject.positions(frame, _facts(len(frame)), fired, minutes=60, tick=.01,
                            rsi_exit_enabled=True, rsi_features=_features(frame.index, strong=range(4, 18)))
    first, second = got[10], got[18]
    assert first["exit_reason"] == "rsi_seventh_reverse_next_open"
    assert first["exit_time_ms"] == int(frame.index[18].value // 1_000_000)
    assert first["exit_time_precision"] == "bar_open"
    assert first["rsi_exit_trigger_close_ms"] == int((frame.index[17] + pd.Timedelta(hours=1)).value // 1_000_000)
    assert first["basis"] == first["performance_version"] == subject.BASIS
    assert first["exit_r"] < 0  # exact seventh exits even though it realizes a loss
    assert first["rsi_counter_start_bar_open_ms"] == int(frame.index[11].value // 1_000_000)
    assert first["rsi_run_count"] == 7  # pre-entry diamonds did not contribute
    assert second["status"] != "unknown" or second.get("reason") != "serial_position_already_open"
    assert second["rsi_counter_known"] and second["rsi_run_side"] == second["rsi_run_count"] == 0


def test_stop_gap_still_beats_injected_rsi_and_no_profit_filter_is_applied():
    frame = _frame()
    frame.loc[frame.index[18], ["open", "high", "low", "close"]] = [97.5, 98.0, 97.0, 97.5]
    fired = np.zeros(len(frame), dtype=bool); fired[10] = True
    got = subject.positions(frame, _facts(len(frame)), fired, minutes=60, tick=.01,
                            rsi_exit_enabled=True, rsi_features=_features(frame.index, strong=range(11, 18)))[10]
    assert got["exit_reason"] == "initial_stop_gap"
    assert got["rsi_exit_trigger_close_ms"] is None
    assert got["rsi_run_side"] == -1 and got["rsi_run_count"] == 7 and got["rsi_counter_known"]


def test_baseline_default_ignores_injected_rsi_and_active_card_reports_pending_only_at_last_close():
    frame = _frame()
    fired = np.zeros(len(frame), dtype=bool); fired[10] = True
    features = _features(frame.index, strong=range(len(frame) - 7, len(frame)))
    baseline = subject.positions(frame, _facts(len(frame)), fired, minutes=60, tick=.01, rsi_features=features)[10]
    active = subject.positions(frame, _facts(len(frame)), fired, minutes=60, tick=.01,
                               rsi_exit_enabled=True, rsi_features=features)[10]
    assert baseline["basis"] == subject.BASELINE_BASIS and "rsi_exit_rule" not in baseline
    assert active["status"] == "active" and active["rsi_exit_pending"] is True
    assert active["rsi_exit_trigger_close_ms"] is None


def test_closed_rsi_metadata_uses_its_trigger_prefix_not_future_counter_state():
    frame = _frame()
    fired = np.zeros(len(frame), dtype=bool); fired[10] = True
    features = _features(frame.index, strong=range(11, 18))
    # A later color/run must not leak into the close already executed at 18's open.
    features.loc[frame.index[19]:, "strong_side"] = 1
    full = subject.positions(frame, _facts(len(frame)), fired, minutes=60, tick=.01,
                             rsi_exit_enabled=True, rsi_features=features)[10]
    short_frame, short_features = frame.iloc[:19], features.iloc[:19]
    short = subject.positions(short_frame, _facts(len(short_frame)), fired[:19], minutes=60, tick=.01,
                              rsi_exit_enabled=True, rsi_features=short_features)[10]
    keys = ("rsi_run_side", "rsi_run_count", "rsi_counter_known", "rsi_exit_trigger_close_ms")
    assert full["exit_reason"] == short["exit_reason"] == "rsi_seventh_reverse_next_open"
    assert {key: full[key] for key in keys} == {key: short[key] for key in keys}
    assert full["rsi_run_side"] == -1 and full["rsi_run_count"] == 7


def test_analyze_builds_the_same_timeframe_rsi_series_once(monkeypatch):
    n = 650
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(0, .003, n)))
    open_ = np.r_[close[0], close[:-1]]
    frame = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.003,
                          "low": np.minimum(open_, close) * .997, "close": close, "volume": 100.0},
                         index=pd.date_range("2025-02-01", periods=n, freq="15min", tz="UTC"))
    actual = subject.rsi_exit.chartprime_strong_side
    calls = []

    def once(chart, *, gap, minutes):
        calls.append((len(chart), minutes))
        return actual(chart, gap=gap, minutes=minutes)

    monkeypatch.setattr(subject.rsi_exit, "chartprime_strong_side", once)
    subject.analyze(frame, "15m", tick=.001, asset="TEST")
    assert calls == [(n, 15)]

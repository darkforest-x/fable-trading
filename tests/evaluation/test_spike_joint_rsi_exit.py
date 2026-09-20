"""Causal feature and frozen-exit adapter contracts for RSI research only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from yoyo.evaluation import spike_joint_rsi_exit as subject
from yoyo.evaluation import spike_v10_4_increment as increment
from yoyo.evaluation import spike_v10_4_study as study


def _prepared(open_, high, low, close, *, raw_side=None, gap=None):
    n = len(close)
    index = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    frame = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                          "atr": 1.0, "ready": True}, index=index)
    return study.prepared_arm(
        frame, np.zeros(n, bool) if gap is None else np.asarray(gap, bool),
        np.zeros(n, int) if raw_side is None else np.asarray(raw_side, int),
        "t:TEST:1h", {"venue": "t", "symbol": "TEST", "asset": "TEST", "timeframe": "1h", "timeframe_min": 60},
        60, 0.01,
    )


def _bars(n=40):
    close = np.full(n, 100.0)
    return close.copy(), close + .5, close - .5, close


def test_no_rsi_trigger_is_exactly_the_frozen_attempt():
    open_, high, low, close = _bars()
    prepared = _prepared(open_, high, low, close)
    expected = increment.attempt(prepared, 10)
    actual = subject.attempt_with_rsi_exit(prepared, 10, np.zeros(len(close), dtype=bool))
    assert actual == expected


def test_rsi_close_exit_fills_at_next_open_and_records_its_trigger():
    open_, high, low, close = _bars()
    # The RSI order exits at this open before the same bar's later low could
    # touch the old stop.  Keep the synthetic OHLC geometry valid.
    open_[13], high[13], low[13] = 101.25, 101.5, 97.0
    prepared = _prepared(open_, high, low, close)
    mask = np.zeros(len(close), dtype=bool); mask[12] = True
    status, trade = subject.attempt_with_rsi_exit(prepared, 10, mask)
    assert status == "closed"
    assert (trade["exit_i"], trade["exit_price"], trade["exit_reason"]) == (13, pytest.approx(101.25), "rsi_seventh_reverse_next_open")
    assert trade["rsi_exit_trigger_i"] == 12
    assert trade["rsi_exit_trigger_close_time"] == prepared.frame.index[12] + pd.Timedelta(hours=1)


def test_current_bar_stop_beats_its_close_rsi_trigger():
    open_, high, low, close = _bars()
    low[12] = 97.9
    prepared = _prepared(open_, high, low, close)
    mask = np.zeros(len(close), dtype=bool); mask[12] = True
    status, trade = subject.attempt_with_rsi_exit(prepared, 10, mask)
    assert status == "closed"
    assert (trade["exit_i"], trade["exit_reason"], trade["exit_price"]) == (12, "initial_stop", pytest.approx(98.0))
    assert "rsi_exit_trigger_i" not in trade


def test_stop_gap_at_scheduled_rsi_open_beats_the_rsi_exit():
    open_, high, low, close = _bars()
    open_[13], low[13] = 97.5, 97.0
    prepared = _prepared(open_, high, low, close)
    mask = np.zeros(len(close), dtype=bool); mask[12] = True
    status, trade = subject.attempt_with_rsi_exit(prepared, 10, mask)
    assert status == "closed"
    assert (trade["exit_i"], trade["exit_reason"], trade["exit_price"]) == (13, "initial_stop_gap", pytest.approx(97.5))


@pytest.mark.parametrize("also_rsi", [False, True])
def test_existing_opposite_reverse_keeps_its_frozen_reason_on_earlier_or_tied_rsi(also_rsi):
    open_, high, low, close = _bars()
    raw = np.zeros(len(close), int); raw[12] = -1
    prepared = _prepared(open_, high, low, close, raw_side=raw)
    mask = np.zeros(len(close), dtype=bool); mask[12 if also_rsi else 13] = True
    status, trade = subject.attempt_with_rsi_exit(prepared, 10, mask)
    assert status == "closed"
    assert (trade["exit_i"], trade["exit_reason"]) == (13, "opposite_v6_next_open")
    assert "rsi_exit_trigger_i" not in trade


def test_terminal_rsi_signal_and_pre_entry_rsi_signal_do_not_change_a_censored_trade():
    open_, high, low, close = _bars()
    prepared = _prepared(open_, high, low, close)
    for trigger_i in (5, len(close) - 1):
        mask = np.zeros(len(close), dtype=bool); mask[trigger_i] = True
        status, trade = subject.attempt_with_rsi_exit(prepared, 10, mask)
        assert status == "censored_boundary" and trade["exit_reason"] == "boundary_mark"


def test_gap_censor_is_retained_when_no_rsi_exit_is_scheduled_first():
    open_, high, low, close = _bars()
    gap = np.zeros(len(close), dtype=bool); gap[15] = True
    status, trade = subject.attempt_with_rsi_exit(_prepared(open_, high, low, close, gap=gap), 10,
                                                  np.zeros(len(close), dtype=bool))
    assert (status, trade["censored"], trade["exit_i"], trade["exit_reason"]) == ("censored_gap", True, 15, "data_gap_censored")


def test_adapter_never_mutates_the_raw_sides_or_caller_mask():
    open_, high, low, close = _bars()
    raw = np.zeros(len(close), int)
    prepared = _prepared(open_, high, low, close, raw_side=raw)
    original = prepared.raw_side.copy()
    mask = np.zeros(len(close), dtype=bool); mask[12] = True
    before_mask = mask.copy()
    subject.attempt_with_rsi_exit(prepared, 10, mask)
    np.testing.assert_array_equal(prepared.raw_side, original)
    np.testing.assert_array_equal(mask, before_mask)


def _feature_frame(n=80):
    index = pd.date_range("2025-03-01", periods=n, freq="h", tz="UTC")
    close = 100 + np.cumsum(np.sin(np.arange(n) / 3) + .15)
    return pd.DataFrame({"close": close}, index=index)


def test_chartprime_features_are_prefix_causal():
    frame = _feature_frame()
    changed = frame.copy(); changed.iloc[55:, 0] += 20
    left = subject.chartprime_strong_side(frame, minutes=60)
    right = subject.chartprime_strong_side(changed, minutes=60)
    pdt.assert_frame_equal(left.iloc[:55], right.iloc[:55])


def test_gap_and_timestamp_discontinuity_reseed_against_an_isolated_segment():
    frame = _feature_frame()
    gap = np.zeros(len(frame), dtype=bool); gap[30] = True
    joined = subject.chartprime_strong_side(frame, gap=gap, minutes=60)
    isolated = subject.chartprime_strong_side(frame.iloc[30:], minutes=60)
    pdt.assert_frame_equal(joined.iloc[30:], isolated)
    discontinuous = pd.concat([frame.iloc[:30], frame.iloc[30:].set_axis(frame.index[30:] + pd.Timedelta(hours=3))])
    auto = subject.chartprime_strong_side(discontinuous, minutes=60)
    pdt.assert_frame_equal(auto.iloc[30:], isolated.set_axis(discontinuous.index[30:]))


def test_feature_marks_nonfinite_closes_unknown_and_validates_aligned_bool_inputs():
    frame = _feature_frame()
    frame.iloc[30, 0] = np.nan
    got = subject.chartprime_strong_side(frame, minutes=60)
    assert not got.known.iloc[30] and got.strong_side.iloc[30] == 0
    isolated_successor = subject.chartprime_strong_side(frame.iloc[31:], minutes=60)
    pdt.assert_frame_equal(got.iloc[31:], isolated_successor)
    with pytest.raises(ValueError, match="bool"):
        subject.chartprime_strong_side(_feature_frame(), gap=np.zeros(len(frame), dtype=int), minutes=60)
    with pytest.raises(ValueError, match="bool"):
        subject.attempt_with_rsi_exit(_prepared(*_bars()), 10, np.zeros(40, dtype=int))

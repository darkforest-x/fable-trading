"""Causal contracts for the V11.2 stop-anchor and wick-arm offline ablation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from yoyo.evaluation import spike_v10_4_increment as increment
from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v112_execution_ablation as ablation


def _prepared(open_, high, low, close, *, raw_side=None, gap=None):
    n = len(close)
    index = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    frame = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "atr": 1., "ready": True}, index=index)
    return study.prepared_arm(frame, np.zeros(n, bool) if gap is None else np.asarray(gap, bool),
                              np.zeros(n, int) if raw_side is None else np.asarray(raw_side, int), "t:TEST:1h",
                              {"venue": "t", "symbol": "TEST", "asset": "TEST", "timeframe": "1h", "timeframe_min": 60}, 60, .01)


def _bars(n=40):
    close = np.full(n, 100.)
    return close.copy(), close + .5, close - .5, close


def test_baseline_delegates_to_the_published_increment_attempt_exactly():
    open_, high, low, close = _bars()
    prepared = _prepared(open_, high, low, close)
    expected_status, expected = increment.attempt(prepared, 10)
    actual_status, actual = ablation.attempt_variant(prepared, 10, 7, "baseline")
    assert actual_status == expected_status
    pdt.assert_series_equal(pd.Series(actual).sort_index(), pd.Series(expected).sort_index(), check_dtype=False)


def test_parent_stop_uses_only_a_causal_anchor_and_recomputes_joint_risk():
    open_, high, low, close = _bars()
    low[6] = 95.0  # parent signal 10 sees this; joint 12's five-bar stop does not.
    prepared = _prepared(open_, high, low, close)
    _, baseline = ablation.attempt_variant(prepared, 12, 10, "baseline")
    status, parent = ablation.attempt_variant(prepared, 12, 10, "parent_stop")
    assert status == "censored_boundary"
    assert parent["entry_i"] == baseline["entry_i"] == 13 and parent["entry_price"] == baseline["entry_price"]
    assert parent["initial_stop"] == pytest.approx(94.8)
    assert parent["initial_risk"] == pytest.approx(parent["entry_price"] - parent["initial_stop"])
    assert parent["initial_risk_frac"] == pytest.approx(parent["initial_risk"] / parent["entry_price"])
    assert parent["initial_risk"] > baseline["initial_risk"]
    same_status, same = ablation.attempt_variant(prepared, 12, 12, "parent_stop")
    assert same_status == "censored_boundary"
    assert same["initial_stop"] == baseline["initial_stop"] and same["initial_risk"] == baseline["initial_risk"]
    assert ablation.attempt_variant(prepared, 12, 13, "parent_stop") == ("parent_stop_invalid", None)


def test_parent_surrogate_need_not_be_a_signal_but_invalid_anchor_never_falls_back():
    open_, high, low, close = _bars()
    prepared = _prepared(open_, high, low, close)
    assert ablation.attempt_variant(prepared, 12, np.int64(10), "parent_stop")[0] == "censored_boundary"
    assert ablation.attempt_variant(prepared, 12, 2, "parent_stop") == ("parent_stop_invalid", None)


def test_wick_and_close_arming_disagree_but_use_the_same_close_based_trail_price():
    open_, high, low, close = _bars()
    high[11], close[11], low[11] = 104.2, 103., 99.0  # entry bar: high > 2R, close = 1.5R
    open_[12], high[12], low[12], close[12] = 100., 100.2, 98.5, 100.
    prepared = _prepared(open_, high, low, close)
    _, baseline = ablation.attempt_variant(prepared, 10, 10, "baseline")
    status, wick = ablation.attempt_variant(prepared, 10, 10, "wick_arm")
    assert baseline["censored"] and status == "closed"
    assert (wick["exit_i"], wick["exit_reason"], wick["exit_price"]) == (12, "trailing_stop", pytest.approx(99.0))
    assert wick["protection"] == pytest.approx(99.0)  # close[11] - 4 ATR, not a high-derived stop


def test_stop_before_wick_arm_on_the_same_bar_cannot_be_rescued_by_the_high():
    open_, high, low, close = _bars()
    high[11], low[11] = 104.2, 97.9  # high clears 2R, but existing initial stop is hit first.
    status, wick = ablation.attempt_variant(_prepared(open_, high, low, close), 10, 10, "wick_arm")
    assert status == "closed" and (wick["exit_i"], wick["exit_reason"], wick["exit_price"]) == (11, "initial_stop", pytest.approx(98.0))
    assert wick["mfe_r"] == 0.0


def test_wick_protection_starts_on_the_next_bar_only():
    open_, high, low, close = _bars()
    high[11], low[11], close[11] = 104.2, 98.01, 103.  # survives the old 98 stop; new 99 trail cannot stop this bar
    open_[12], low[12], close[12] = 100., 98.9, 99.
    status, wick = ablation.attempt_variant(_prepared(open_, high, low, close), 10, 10, "wick_arm")
    assert status == "closed" and (wick["exit_i"], wick["exit_price"], wick["exit_reason"]) == (12, pytest.approx(99.0), "trailing_stop")


def test_wick_arm_keeps_raw_opposite_confirmation_at_the_next_open():
    open_, high, low, close = _bars()
    raw = np.zeros(len(close), int); raw[13] = -1
    open_[14] = 101.3
    status, wick = ablation.attempt_variant(_prepared(open_, high, low, close, raw_side=raw), 10, 10, "wick_arm")
    assert status == "closed" and (wick["exit_i"], wick["exit_reason"], wick["exit_price"]) == (14, "opposite_v6_next_open", pytest.approx(101.3))


def test_variant_preserves_no_next_next_gap_and_gap_censor_statuses():
    open_, high, low, close = _bars(20)
    gap = np.zeros(20, bool); gap[16] = True
    prepared = _prepared(open_, high, low, close, gap=gap)
    assert ablation.attempt_variant(prepared, 19, 19, "wick_arm") == ("no_next_bar", None)
    assert ablation.attempt_variant(prepared, 15, 15, "wick_arm") == ("next_bar_is_gap", None)
    status, result = ablation.attempt_variant(prepared, 10, 10, "wick_arm")
    assert (status, result["censored"], result["exit_i"], result["exit_reason"]) == ("censored_gap", True, 16, "data_gap_censored")

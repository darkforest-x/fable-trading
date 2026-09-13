"""Causality contracts for frozen SPIKE V8 entry-process labels."""
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v8_entry_process_study import _repo_relative, h1_stale_no_progress, h2_failed_break_reversal, matched_pairs


def _bars(n: int = 28) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                         "atr": 1.0, "rv": 1.0}, index=index)


def test_h1_only_reads_evidence_through_confirmation_close():
    bars = _bars()
    first = h1_stale_no_progress(bars, confirm_i=14, evidence_i=12, side=1)
    assert first["h1_anchor_available"]
    assert first["h1_stale_no_progress"]
    assert first["h1_confirmation_no_progress"]
    assert first["h1_lag_bars"] == 2
    assert first["h1_close_advance_atr"] == 0.0
    bars.iloc[15:, bars.columns.get_loc("close")] = 150.0
    bars.iloc[15:, bars.columns.get_loc("high")] = 151.0
    assert h1_stale_no_progress(bars, confirm_i=14, evidence_i=12, side=1) == first


def test_h1_requires_stale_and_no_progress_together():
    bars = _bars()
    assert not h1_stale_no_progress(bars, confirm_i=13, evidence_i=12, side=1)["h1_stale_no_progress"]
    bars.iloc[14, bars.columns.get_loc("close")] = 100.5
    assert not h1_stale_no_progress(bars, confirm_i=14, evidence_i=12, side=1)["h1_stale_no_progress"]


def test_h1_confirmation_variant_detects_an_earlier_advance_that_was_given_back():
    bars = _bars()
    bars.iloc[13, bars.columns.get_loc("close")] = 102.0
    bars.iloc[14, bars.columns.get_loc("close")] = 100.0
    found = h1_stale_no_progress(bars, confirm_i=14, evidence_i=12, side=1)
    assert not found["h1_stale_no_progress"]
    assert found["h1_confirmation_no_progress"]
    assert found["h1_close_advance_atr"] == 2.0
    assert found["h1_confirmation_close_advance_atr"] == 0.0


def test_h2_freezes_pre_breakout_range_and_never_reads_after_confirmation():
    bars = _bars()
    # q=14 is a long breakout of the preceding 12 bars; it fails back inside
    # at q+1 and the final short confirmation at 18 breaks the same low.
    bars.iloc[14, bars.columns.get_loc("close")] = 102.0
    bars.iloc[14, bars.columns.get_loc("high")] = 110.0  # must not widen q's frozen range
    bars.iloc[14, bars.columns.get_loc("rv")] = 1.5
    bars.iloc[15, bars.columns.get_loc("close")] = 100.0
    bars.iloc[18, bars.columns.get_loc("close")] = 98.0
    bars.iloc[18, bars.columns.get_loc("low")] = 97.0
    found = h2_failed_break_reversal(bars, confirm_i=18, side=-1)
    assert found["h2_failed_break_reversal"]
    assert found["h2_breakout_i"] == 14
    assert found["h2_failback_i"] == 15
    assert found["h2_range_low"] == 99.0
    assert found["h2_range_high"] == 101.0
    bars.iloc[19:, bars.columns.get_loc("close")] = np.nan
    assert h2_failed_break_reversal(bars, confirm_i=18, side=-1) == found


def test_h2_requires_a_return_inside_the_original_range():
    bars = _bars()
    bars.iloc[14, bars.columns.get_loc("close")] = 102.0
    bars.iloc[14, bars.columns.get_loc("rv")] = 1.5
    bars.iloc[15:18, bars.columns.get_loc("close")] = 102.0
    bars.iloc[18, bars.columns.get_loc("close")] = 98.0
    assert not h2_failed_break_reversal(bars, confirm_i=18, side=-1)["h2_failed_break_reversal"]


def test_h2_rejects_a_sequence_spanning_a_frozen_data_gap():
    bars = _bars()
    bars.iloc[14, bars.columns.get_loc("close")] = 102.0
    bars.iloc[14, bars.columns.get_loc("rv")] = 1.5
    bars.iloc[15, bars.columns.get_loc("close")] = 100.0
    bars.iloc[18, bars.columns.get_loc("close")] = 98.0
    bars.iloc[18, bars.columns.get_loc("low")] = 97.0
    gap = pd.Series(False, index=bars.index)
    gap.iloc[15] = True
    found = h2_failed_break_reversal(bars, confirm_i=18, side=-1, data_gap=gap)
    assert not found["h2_failed_break_reversal"]
    assert found["h2_discontinuous_candidate_windows"] == 1


def test_near_time_pairs_never_cross_the_development_validation_split():
    rows = pd.DataFrame({
        "stream_key": ["s", "s"], "period": ["development", "validation"], "side": [1, 1],
        "signal_confirm_time": pd.to_datetime(["2025-09-09T00:00:00Z", "2025-09-11T00:00:00Z"]),
        "trade_id": ["dev-target", "validation-control"], "net_r": [-1.0, 1.0],
        "scoring_closed": [True, True], "flag": [True, False],
    })
    pairs = matched_pairs(rows, flag="flag", cohort="test")
    assert len(pairs) == 1
    assert not pairs.matched.iloc[0]
    assert pairs.period.iloc[0] == "development"


def test_repo_relative_normalizes_an_absolute_module_path_for_git_head_lookup():
    assert _repo_relative(__import__("yoyo.evaluation.spike_v8_entry_process_study", fromlist=["x"]).__file__) == "yoyo/evaluation/spike_v8_entry_process_study.py"

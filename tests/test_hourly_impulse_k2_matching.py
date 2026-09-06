"""Synthetic mother-level matching checks; no prices or outcomes are read."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_k2_matching import build_matching_frame, assign_controls


START = pd.Timestamp("2024-01-01T00:00:00Z")
END = pd.Timestamp("2024-02-01T00:00:00Z")


def inputs(hours=650, positions=(250, 274)):
    times = pd.date_range(START, periods=hours, freq="h")
    h = pd.DataFrame({
        "open_time": times, "open": 101., "high": 102., "low": 97., "close": 101.,
        "atr": 1., "ma": 100., "ma_side": 1, "ma_slope_atr": .1, "segment_id": 0,
        "body_ratio": .7, "range_atr": 5., "volume_ratio": 1., "cross_count24": 2.,
        "efficiency24": .3, "long_close_location": .8, "short_close_location": .2,
    })
    raw_times = pd.date_range(START, periods=(hours+1)*12, freq="5min")
    raw = pd.DataFrame({"open_time": raw_times, "open": 100., "high": 102., "low": 98., "close": 100., "segment_id": 0})
    mg = pd.DataFrame({"open_time": raw_times, "ma_side": 1, "segment_id": 0})
    rows = []
    for position in positions:
        h.loc[position, "open"] = 99.
        rows.append({"event_id": "mother_{}".format(position), "decision_time": times[position]+pd.Timedelta(hours=1),
                     "direction": 1, "initial_stop": 98., "signal_atr": 1., "fold": "dev"})
    mothers = pd.DataFrame(rows, columns=["event_id", "decision_time", "direction", "initial_stop", "signal_atr", "fold"])
    return raw, h, mg, mothers


def test_matching_is_at_mother_level_with_exact_keys_and_unique_control_times():
    raw, h, mg, mothers = inputs()
    frame = build_matching_frame(raw, h, mg, mothers)
    controls, assignments, diagnostics = assign_controls(mothers, frame, end_exclusive=END)
    assert len(controls) == 6
    assert assignments.match_status.eq("matched").all()
    assert controls.decision_time.is_unique
    assert controls.event_id.is_unique
    assert not controls.decision_time.isin(mothers.decision_time).any()
    assert controls.parent_event_id.equals(controls.matched_event_id)
    assert diagnostics["mother_rows_removed"] == 0
    assert diagnostics["outcomes_used"] is False
    assert diagnostics["k2_success_used"] is False
    known = frame.set_index("decision_time")
    for mother in mothers.to_dict("records"):
        own = known.loc[mother["decision_time"]]
        for row in controls.loc[controls.parent_event_id.eq(mother["event_id"])].to_dict("records"):
            candidate = known.loc[row["decision_time"]]
            for key in ("month", "utc_6h_bucket", "vol_bucket", "known_5m_colour", "known_hourly_colour", "unsigned_hourly_slope_sign"):
                assert candidate[key] == own[key]
            assert candidate.candidate_eligible
            assert row["known_5m_available"] == row["decision_time"]
            assert row["signal_time"] == row["decision_time"] - pd.Timedelta(hours=1)
            assert (row["entry_open"]-row["initial_stop"])/row["signal_atr"] == pytest.approx(2.)


@pytest.mark.parametrize("seed", [1, 7, 20260906])
def test_wait_success_closed_and_profit_cannot_change_assignments(seed):
    raw, h, mg, mothers = inputs()
    frame = build_matching_frame(raw, h, mg, mothers)
    controls, assignments, receipt = assign_controls(mothers, frame, end_exclusive=END, seed=seed)
    altered = mothers.iloc[::-1].copy()
    altered["k2_success"] = [True, False]
    altered["closed"] = [False, True]
    altered["net_return"] = [999., -999.]
    repeated_controls, repeated_assignments, repeated = assign_controls(altered, frame, end_exclusive=END, seed=seed)
    assert receipt["assignment_hash"] == repeated["assignment_hash"]
    pd.testing.assert_frame_equal(controls, repeated_controls)
    pd.testing.assert_frame_equal(assignments, repeated_assignments)
    assert not {"closed", "net_return", "k2_success"}.intersection(controls)


def test_invalid_direct_k1_risk_only_marks_unmatchable_and_preserves_wait_mother():
    raw, h, mg, mothers = inputs()
    mothers.loc[0, "initial_stop"] = 101.
    original = deepcopy(mothers)
    frame = build_matching_frame(raw, h, mg, mothers)
    controls, assignments, receipt = assign_controls(mothers, frame, end_exclusive=END)
    assert len(assignments) == len(mothers) == 2
    assert assignments.match_status.tolist() == ["invalid_mother_risk", "matched"]
    assert len(controls) == 3
    assert receipt["mother_rows_removed"] == 0
    pd.testing.assert_frame_equal(original, mothers)


def test_risk_transfer_uses_known_next_open_not_maternal_close_or_future_extrema():
    raw, h, mg, mothers = inputs(positions=(250,))
    raw.loc[raw.open_time.eq(mothers.decision_time.iloc[0]), "open"] = 100.5
    frame = build_matching_frame(raw, h, mg, mothers)
    controls, _, receipt = assign_controls(mothers, frame, end_exclusive=END)
    assert controls.transferred_risk_atr.eq(2.5).all()
    raw[["high", "low", "close"]] = [1e9, 1e-9, -1000.]
    mutated = build_matching_frame(raw, h, mg, mothers)
    again, _, repeated = assign_controls(mothers, mutated, end_exclusive=END)
    assert receipt["assignment_hash"] == repeated["assignment_hash"]
    pd.testing.assert_frame_equal(controls, again)


def test_signed_hourly_slope_and_hourly_colour_must_match_without_fallback():
    raw, h, mg, mothers = inputs(positions=(250,))
    h["ma_slope_atr"] = -.1
    h.loc[250, "ma_slope_atr"] = .1
    frame = build_matching_frame(raw, h, mg, mothers)
    controls, assignments, _ = assign_controls(mothers, frame, end_exclusive=END)
    assert controls.empty and assignments.match_status.iloc[0] == "insufficient_exact_controls"
    h["ma_slope_atr"] = .1
    h["ma_side"] = -1
    h.loc[250, "ma_side"] = 1
    frame = build_matching_frame(raw, h, mg, mothers)
    controls, assignments, _ = assign_controls(mothers, frame, end_exclusive=END)
    assert controls.empty and assignments.match_status.iloc[0] == "insufficient_exact_controls"


def test_short_mothers_transfer_risk_and_sign_slope_once():
    raw, h, mg, mothers = inputs(positions=(250,))
    h[["open", "close", "ma_side", "ma_slope_atr"]] = [99., 99., -1, -.1]
    h.loc[250, "open"] = 101.
    mg["ma_side"] = -1
    mothers[["direction", "initial_stop"]] = [-1, 102.]
    frame = build_matching_frame(raw, h, mg, mothers)
    controls, assignments, _ = assign_controls(mothers, frame, end_exclusive=END)
    assert assignments.match_status.eq("matched").all()
    assert controls.initial_stop.eq(102.).all()
    assert controls.ma_slope_atr.eq(.1).all()
    assert controls.signed_hourly_slope_sign.eq(1.).all()


def test_future_hour_cross_does_not_exclude_current_random_mother():
    raw, h, mg, mothers = inputs()
    before = build_matching_frame(raw, h, mg, mothers)
    h.loc[301, ["open", "close", "high", "low", "atr"]] = [99., 200., 201., 98., 50.]
    after = build_matching_frame(raw, h, mg, mothers)
    keys = ["atr_tercile_low", "atr_tercile_high", "vol_bucket", "known_5m_colour", "known_hourly_colour", "unsigned_hourly_slope_sign", "candidate_eligible"]
    pd.testing.assert_series_equal(before.loc[300, keys], after.loc[300, keys])
    assert not after.loc[300, "current_or_prior_cross_excluded"]
    assert after.loc[301, "current_or_prior_cross_excluded"]
    assert after.loc[302, "current_or_prior_cross_excluded"]


def test_actual_mother_decisions_are_excluded_even_if_raw_shape_is_not_a_cross():
    raw, h, mg, mothers = inputs()
    h.loc[250, "open"] = 101.
    frame = build_matching_frame(raw, h, mg, mothers)
    assert frame.loc[250, "actual_mother_decision_excluded"]
    assert not frame.loc[250, "raw_strict_body_cross"]
    assert not frame.loc[250, "candidate_eligible"]


def test_known_5m_requires_exact_completion_not_stale_or_future_colour():
    raw, h, mg, mothers = inputs(positions=(250,))
    decision = mothers.decision_time.iloc[0]
    mg = mg.loc[mg.open_time.ne(decision-pd.Timedelta(minutes=5))]
    frame = build_matching_frame(raw, h, mg, mothers)
    controls, assignments, _ = assign_controls(mothers, frame, end_exclusive=END)
    assert controls.empty
    assert assignments.match_status.iloc[0] == "missing_causal_matching_support"


def test_two_raw_gaps_with_one_hourly_gap_do_not_permanently_reject_support():
    raw, h, _, mothers = inputs(positions=(400,))
    missing = START+pd.Timedelta(hours=200)
    first, second = missing+pd.Timedelta(minutes=10), missing+pd.Timedelta(minutes=40)
    raw = raw.loc[~raw.open_time.isin([first, second])].copy()
    raw.loc[raw.open_time.gt(first), "segment_id"] = 1
    raw.loc[raw.open_time.gt(second), "segment_id"] = 2
    mg = raw[["open_time", "segment_id"]].copy()
    mg["ma_side"] = 1
    h = h.loc[h.open_time.ne(missing)].copy()
    h.loc[h.open_time.gt(missing), "segment_id"] = 1
    frame = build_matching_frame(raw, h, mg, mothers)
    own = frame.loc[frame.decision_time.eq(mothers.decision_time.iloc[0])].iloc[0]
    assert own.segment_id == 1 and own.source_segment_id == 2
    assert own.matching_support
    controls, assignments, _ = assign_controls(mothers, frame, end_exclusive=END)
    assert len(controls) == 3 and assignments.match_status.iloc[0] == "matched"


def test_atr_tercile_is_shifted_and_obeys_segment_warmup():
    raw, h, mg, mothers = inputs()
    h["atr"] = np.arange(len(h))+1.
    frame = build_matching_frame(raw, h, mg, mothers)
    assert pd.isna(frame.loc[167, "vol_bucket"])
    expected = np.quantile(h.loc[:167, "atr"]/101., 1/3)
    assert frame.loc[168, "atr_tercile_low"] == pytest.approx(expected)
    old = frame.loc[250, "atr_tercile_high"]
    h.loc[250, "atr"] = 1e6
    modified = build_matching_frame(raw, h, mg, mothers)
    assert modified.loc[250, "atr_tercile_high"] == old


def test_embargo_is_on_mother_decision_and_does_not_drop_mother_record():
    raw, h, mg, mothers = inputs(positions=(250,))
    frame = build_matching_frame(raw, h, mg, mothers)
    end = mothers.decision_time.iloc[0]+pd.Timedelta(hours=72)
    controls, assignments, _ = assign_controls(mothers, frame, end_exclusive=end)
    assert controls.empty and len(assignments) == 1
    assert assignments.match_status.iloc[0] == "outside_fold_embargo"


def test_mixed_fold_calls_are_rejected_and_empty_mother_tables_are_supported():
    raw, h, mg, mothers = inputs()
    frame = build_matching_frame(raw, h, mg, mothers)
    mothers.loc[1, "fold"] = "another_fold"
    with pytest.raises(ValueError, match="once per fold"):
        assign_controls(mothers, frame, end_exclusive=END)
    controls, assignments, receipt = assign_controls(mothers.iloc[:0], frame, end_exclusive=END)
    assert controls.empty and assignments.empty
    assert receipt["mother_count"] == 0


def test_control_features_belong_to_random_mother_not_real_mother():
    raw, h, mg, mothers = inputs()
    h["body_ratio"] = np.arange(len(h))/len(h)
    frame = build_matching_frame(raw, h, mg, mothers)
    controls, _, _ = assign_controls(mothers, frame, end_exclusive=END)
    lookup = h.set_index("open_time")
    for row in controls.to_dict("records"):
        assert row["body_ratio"] == lookup.loc[row["signal_time"], "body_ratio"]
        assert row["signal_close"] == lookup.loc[row["signal_time"], "close"]

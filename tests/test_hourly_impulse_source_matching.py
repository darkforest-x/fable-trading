"""Synthetic-only V7 support, causal exact-key assignment and risk transfer."""
import hashlib

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_source_matching import (
    ASSIGNMENT_COLUMNS, CONTROL_COLUMNS, assign_source_controls, build_source_matching_frame,
)


START = pd.Timestamp("2024-01-01T00:00:00Z")
END = pd.Timestamp("2024-02-01T00:00:00Z")
HOUR = pd.Timedelta(hours=1)
FIVE = pd.Timedelta(minutes=5)


def inputs(hours=650, positions=(250, 274), direction=1):
    hourly_times = pd.date_range(START, periods=hours, freq="h")
    hourly = pd.DataFrame({
        "open_time": hourly_times, "open": 99.0, "high": 102.0, "low": 97.0,
        "close": 101.0, "atr": 1.0, "ma": 100.0, "ma_side": 1,
        "ma_slope_atr": 0.1, "segment_id": 4, "body_ratio": 0.7,
        "range_atr": 5.0, "volume_ratio": 1.0, "cross_count24": 2.0,
        "efficiency24": 0.3, "long_close_location": 0.8, "short_close_location": 0.2,
    })
    times = pd.date_range(START, periods=(hours + 1) * 12, freq="5min")
    raw = pd.DataFrame({"open_time": times, "open": 100.0, "high": 102.0,
                        "low": 98.0, "close": 100.0, "segment_id": 11})
    management = pd.DataFrame({"open_time": times, "ma_side": 1, "segment_id": 7})
    rows = [{
        "event_id": "case_{}".format(i), "signal_time": hourly_times[position],
        "decision_time": hourly_times[position] + HOUR, "direction": direction,
        "initial_stop": 98.0 if direction == 1 else 102.0,
        "signal_atr": 1.0, "fold": "development", "zone_id": "zone_{}".format(i),
    } for i, position in enumerate(positions)]
    cases = pd.DataFrame(rows, columns=["event_id", "signal_time", "decision_time", "direction", "initial_stop", "signal_atr", "fold", "zone_id"])
    return raw, hourly, management, cases


def assign(cases, frame, **kwargs):
    options = {"start_inclusive": START, "end_exclusive": END, **kwargs}
    return assign_source_controls(cases, frame, **options)


def test_exact_month_time_colour_vol_keys_risk_and_unique_control_contract():
    raw, hourly, mg, cases = inputs()
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    controls, assignments, receipt = assign(cases, frame)
    assert len(controls) == 6
    assert controls.columns.tolist() == CONTROL_COLUMNS
    assert assignments.columns.tolist() == ASSIGNMENT_COLUMNS
    assert assignments.match_status.eq("matched").all()
    assert controls.event_id.is_unique and controls.decision_time.is_unique
    assert not controls.decision_time.isin(cases.decision_time).any()
    assert controls.parent_event_id.equals(controls.matched_event_id)
    assert receipt["matched_cases"] == 2 and receipt["case_rows_removed"] == 0
    assert receipt["outcomes_used"] is receipt["hourly_colour_gate_used"] is False
    lookup = frame.set_index("decision_time")
    for case in cases.to_dict("records"):
        own = lookup.loc[case["decision_time"]]
        for control in controls.loc[controls.parent_event_id.eq(case["event_id"])].to_dict("records"):
            candidate = lookup.loc[control["decision_time"]]
            for key in ("month", "utc_6h_bucket", "vol_bucket", "known_5m_colour"):
                assert candidate[key] == own[key], key
            assert control["fold"] == case["fold"]
            assert control["parent_zone_id"] == case["zone_id"]
            assert control["known_5m_available"] == control["decision_time"]
            assert control["signal_time"] == control["decision_time"] - HOUR
            assert candidate.candidate_eligible
            assert START <= control["decision_time"] < END - pd.Timedelta(hours=72)
            assert control["direction"] * (control["entry_open"] - control["initial_stop"]) / control["signal_atr"] == pytest.approx(2.0)


@pytest.mark.parametrize("seed", [1, 7, 20260906])
def test_assignment_is_seed_deterministic_and_ignores_outcomes_or_input_order(seed):
    raw, hourly, mg, cases = inputs()
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    before = assign(cases, frame, seed=seed)
    modified_cases = cases.iloc[::-1].assign(closed=[False, True], net_return=[1e9, -1e9], release_success=[True, False])
    modified_frame = frame.assign(net_return=1e9, closed=False, source_zone_success=False)
    after = assign(modified_cases, modified_frame.iloc[::-1], seed=seed)
    pd.testing.assert_frame_equal(before[0], after[0])
    pd.testing.assert_frame_equal(before[1], after[1])
    assert before[2] == after[2]
    assert not {"closed", "net_return", "release_success", "source_zone_success"}.intersection(after[0])


def test_control_order_is_exact_documented_sha256_ranking():
    raw, hourly, mg, cases = inputs(positions=(250,))
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    controls, assignments, _ = assign(cases, frame, seed=7)
    own = frame.loc[frame.decision_time.eq(cases.decision_time.iloc[0])].iloc[0]
    pool = frame.loc[frame.candidate_eligible & frame.decision_time.ge(START) & frame.decision_time.lt(END - pd.Timedelta(hours=72))]
    for key in ("month", "utc_6h_bucket", "vol_bucket", "known_5m_colour"):
        pool = pool.loc[pool[key].eq(own[key])]
    expected = sorted(pool.decision_time, key=lambda time: hashlib.sha256(
        "7|case_0|{}".format(time.isoformat()).encode("utf-8")).hexdigest())[:3]
    assert controls.decision_time.tolist() == expected
    assert assignments.available_controls.iloc[0] == len(pool)


def test_hourly_colour_slope_sma_cross_and_zone_success_are_not_filters():
    raw, hourly, mg, cases = inputs(positions=(250,))
    # Every hour is a strict SMA body cross. A V4 pool would be empty.
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    before, _, _ = assign(cases, frame)
    assert len(before) == 3
    altered = hourly.copy()
    altered["ma_side"] = 0
    altered["ma_slope_atr"] = np.nan
    altered["ma"] = np.nan
    altered["source_zone_success"] = False
    altered["candidate_eligible"] = False
    altered["matching_support"] = False
    modified = build_source_matching_frame(raw, altered, mg, cases)
    after, assignments, receipt = assign(cases, modified)
    assert len(after) == 3 and assignments.match_status.eq("matched").all()
    assert after.decision_time.tolist() == before.decision_time.tolist()
    assert after.ma_slope_atr.isna().all()
    assert after.signed_hourly_slope_sign.isna().all()
    assert not receipt["sma_cross_exclusion_used"]
    assert not receipt["hourly_colour_gate_used"]
    assert not receipt["hourly_slope_gate_used"]


def test_controls_can_have_opposite_hourly_colour_and_slope_to_the_case():
    raw, hourly, mg, cases = inputs(positions=(250,))
    hourly["ma_side"] = -1
    hourly["ma_slope_atr"] = -0.1
    hourly.loc[250, ["ma_side", "ma_slope_atr"]] = [1, 0.1]
    controls, assignments, _ = assign(cases, build_source_matching_frame(raw, hourly, mg, cases))
    assert len(controls) == 3 and assignments.match_status.eq("matched").all()
    assert controls.known_hourly_colour.eq(-1).all()
    assert controls.signed_hourly_slope_sign.eq(-1).all()


def test_only_exact_actual_request_time_is_excluded_without_past_or_future_window():
    raw, hourly, mg, cases = inputs(positions=(250,))
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    assert frame.loc[250, "actual_case_decision_excluded"]
    assert not frame.loc[250, "candidate_eligible"]
    assert frame.loc[249, "candidate_eligible"] and frame.loc[251, "candidate_eligible"]
    # A future request may exclude itself, never alter any earlier row.
    extra = cases.iloc[[0]].copy()
    extra["decision_time"] = hourly.open_time.iloc[301] + HOUR
    modified = build_source_matching_frame(raw, hourly, mg, pd.concat([cases, extra]))
    pd.testing.assert_frame_equal(frame.loc[:300], modified.loc[:300])


def test_assign_defensively_excludes_cases_if_builder_received_empty_request_list():
    raw, hourly, mg, cases = inputs()
    frame = build_source_matching_frame(raw, hourly, mg, pd.DataFrame())
    controls, _, _ = assign(cases, frame)
    assert not controls.decision_time.isin(cases.decision_time).any()


@pytest.mark.parametrize("direction", [1, -1])
def test_risk_transfer_uses_actual_known_next_open_and_control_own_atr(direction):
    raw, hourly, mg, cases = inputs(positions=(250,), direction=direction)
    decision = cases.decision_time.iloc[0]
    raw.loc[raw.open_time.eq(decision), "open"] = 100.5
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    controls, _, _ = assign(cases, frame)
    expected = 2.5 if direction == 1 else 1.5
    assert controls.transferred_risk_atr.eq(expected).all()
    assert np.allclose(direction * (controls.entry_open - controls.initial_stop) / controls.signal_atr, expected)
    assert controls.direction.eq(direction).all()
    assert controls.ma_slope_atr.eq(direction * 0.1).all()
    mutated = raw.copy()
    mutated[["high", "low", "close"]] = [1e9, -1e9, np.nan]
    repeated, _, receipt = assign(cases, build_source_matching_frame(mutated, hourly, mg, cases))
    pd.testing.assert_frame_equal(controls, repeated)
    assert not receipt["outcomes_used"]


def test_control_features_belong_to_control_not_parent_signal():
    raw, hourly, mg, cases = inputs()
    hourly["body_ratio"] = np.arange(len(hourly)) / len(hourly)
    controls, _, _ = assign(cases, build_source_matching_frame(raw, hourly, mg, cases))
    lookup = hourly.set_index("open_time")
    for row in controls.to_dict("records"):
        assert row["body_ratio"] == lookup.loc[row["signal_time"], "body_ratio"]
        assert row["signal_close"] == lookup.loc[row["signal_time"], "close"]


@pytest.mark.parametrize("direction", [1, -1])
def test_transferred_risk_uses_control_atr_not_parent_atr(direction):
    raw, hourly, mg, cases = inputs(positions=(250,), direction=direction)
    # Keep the exact volatility fraction while controls have twice the ATR.
    hourly[["atr", "close", "high"]] = [2.0, 202.0, 203.0]
    hourly.loc[250, ["atr", "close", "high"]] = [1.0, 101.0, 102.0]
    controls, assignments, _ = assign(cases, build_source_matching_frame(raw, hourly, mg, cases))
    assert assignments.match_status.iloc[0] == "matched"
    assert controls.signal_atr.eq(2.0).all()
    assert controls.transferred_risk_atr.eq(2.0).all()
    assert controls.initial_stop.eq(100.0 - direction * 4.0).all()


def test_nonpositive_transferred_control_stop_is_rejected_not_repaired():
    raw, hourly, mg, cases = inputs(positions=(250,))
    hourly[["atr", "close", "high"]] = [2.0, 202.0, 203.0]
    hourly.loc[250, ["atr", "close", "high"]] = [1.0, 101.0, 102.0]
    cases["initial_stop"] = 1.0
    controls, assignments, _ = assign(cases, build_source_matching_frame(raw, hourly, mg, cases))
    assert controls.empty
    assert assignments.match_status.iloc[0] == "insufficient_exact_controls"
    assert assignments.mother_risk_atr.iloc[0] == 99.0


def test_case_without_causal_volatility_warmup_remains_unknown_not_bucket_zero():
    raw, hourly, mg, cases = inputs(positions=(100,))
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    controls, assignments, receipt = assign(cases, frame)
    assert controls.empty and len(assignments) == 1
    assert assignments.match_status.iloc[0] == "missing_causal_matching_support"
    assert pd.isna(assignments.vol_bucket.iloc[0])
    assert receipt["case_rows_removed"] == 0


@pytest.mark.parametrize("change,reason", [
    ({"initial_stop": 101.0}, "invalid_case_risk"),
    ({"initial_stop": np.nan}, "invalid_case_risk"),
    ({"signal_atr": 2.0}, "case_atr_mismatch"),
    ({"signal_atr": 0.0}, "invalid_case_risk"),
    ({"direction": 0}, "invalid_case_risk"),
    ({"decision_time": pd.NaT}, "missing_case_hourly_decision"),
    ({"signal_time": START}, "case_signal_time_mismatch"),
])
def test_invalid_case_remains_unmatched_without_being_removed(change, reason):
    raw, hourly, mg, cases = inputs()
    for key, value in change.items():
        cases.loc[0, key] = value
    original = cases.copy(deep=True)
    controls, assignments, receipt = assign(cases, build_source_matching_frame(raw, hourly, mg, cases))
    assert len(assignments) == len(cases)
    assert assignments.set_index("event_id").at["case_0", "match_status"] == reason
    assert not controls.parent_event_id.eq("case_0").any()
    assert receipt["case_rows_removed"] == 0
    pd.testing.assert_frame_equal(cases, original)


@pytest.mark.parametrize("side", [0, np.nan, np.inf, 2])
def test_unknown_latest_native_colour_is_not_zero_filled(side):
    raw, hourly, mg, cases = inputs(positions=(250,))
    mg["ma_side"] = mg["ma_side"].astype(float)
    mg.loc[mg.open_time.eq(cases.decision_time.iloc[0] - FIVE), "ma_side"] = side
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    controls, assignments, _ = assign(cases, frame)
    assert controls.empty
    assert assignments.match_status.iloc[0] == "missing_causal_matching_support"
    own = frame.loc[250]
    assert not own.known_5m_valid and not own.matching_support
    if not np.isfinite(side):
        assert pd.isna(own.known_5m_colour)


@pytest.mark.parametrize("problem", ["missing", "stale", "future", "unknown_segment"])
def test_latest_native_colour_requires_exact_just_completed_bar(problem):
    raw, hourly, mg, cases = inputs(positions=(250,))
    decision = cases.decision_time.iloc[0]
    index = mg.index[mg.open_time.eq(decision - FIVE)][0]
    if problem == "unknown_segment":
        mg["segment_id"] = mg["segment_id"].astype(float)
        mg.loc[index, "segment_id"] = np.nan
    elif problem == "stale":
        mg.loc[index, "open_time"] -= pd.Timedelta(minutes=1)
    else:
        mg = mg.drop(index=index)
        if problem == "future":
            mg.loc[mg.open_time.eq(decision), "ma_side"] = -1
    controls, assignments, _ = assign(cases, build_source_matching_frame(raw, hourly, mg, cases))
    assert controls.empty
    assert assignments.match_status.iloc[0] == "missing_causal_matching_support"


def test_different_grid_segment_counters_and_two_gaps_in_one_hour_recover():
    raw, hourly, mg, cases = inputs(positions=(400,))
    missing_hour = START + 200 * HOUR
    gap1, gap2 = missing_hour + 2 * FIVE, missing_hour + 8 * FIVE
    raw = raw.loc[~raw.open_time.isin([gap1, gap2])].copy()
    raw.loc[raw.open_time.gt(gap1), "segment_id"] = 12
    raw.loc[raw.open_time.gt(gap2), "segment_id"] = 13
    hourly = hourly.loc[hourly.open_time.ne(missing_hour)].copy()
    hourly.loc[hourly.open_time.gt(missing_hour), "segment_id"] = 5
    mg = mg.loc[~mg.open_time.isin([gap1, gap2])].copy()
    mg.loc[mg.open_time.gt(gap1), "segment_id"] = 8
    mg.loc[mg.open_time.gt(gap2), "segment_id"] = 9
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    own = frame.loc[frame.decision_time.eq(cases.decision_time.iloc[0])].iloc[0]
    assert own.segment_id == 5 and own.source_segment_id == 13 and own.management_segment_id == 9
    assert own.known_5m_valid and own.matching_support
    controls, assignments, _ = assign(cases, frame)
    assert len(controls) == 3 and assignments.match_status.iloc[0] == "matched"


@pytest.mark.parametrize("problem", ["entry", "last_bar", "inside_hour", "new_segment", "invalid_open"])
def test_source_gaps_or_invalid_entry_open_cannot_produce_matching_support(problem):
    raw, hourly, mg, cases = inputs(positions=(250,))
    time = cases.decision_time.iloc[0]
    if problem in ("entry", "last_bar", "inside_hour"):
        remove = time if problem == "entry" else time - (FIVE if problem == "last_bar" else 6 * FIVE)
        # Deliberately do not update segment IDs: physical missing bars suffice.
        raw = raw.loc[raw.open_time.ne(remove)]
    elif problem == "new_segment":
        raw.loc[raw.open_time.ge(time), "segment_id"] = 12
    else:
        raw.loc[raw.open_time.eq(time), "open"] = np.nan
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    controls, assignments, _ = assign(cases, frame)
    assert controls.empty
    assert assignments.match_status.iloc[0] == "missing_or_gapped_case_open"


def test_atr_terciles_are_shifted_720_window_with_minimum_168():
    raw, hourly, mg, cases = inputs(hours=850)
    hourly["atr"] = np.arange(len(hourly)) + 1.0
    cases["signal_atr"] = cases.signal_time.map(hourly.set_index("open_time").atr)
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    assert frame.loc[:167, "vol_bucket"].isna().all()
    assert frame.loc[168, "atr_tercile_low"] == pytest.approx(np.quantile(hourly.atr.iloc[:168] / 101, 1 / 3))
    assert frame.loc[800, "atr_tercile_high"] == pytest.approx(np.quantile(hourly.atr.iloc[80:800] / 101, 2 / 3))
    hourly.loc[800, "atr"] = 1e9
    altered = build_source_matching_frame(raw, hourly, mg, cases)
    assert frame.loc[800, "atr_tercile_high"] == altered.loc[800, "atr_tercile_high"]


def test_future_hourly_and_management_changes_cannot_modify_prior_support():
    raw, hourly, mg, cases = inputs()
    before = build_source_matching_frame(raw, hourly, mg, cases)
    hourly.loc[301:, ["open", "high", "low", "close", "atr", "ma", "ma_side", "ma_slope_atr"]] = [1, 1000, 0.5, 500, 200, 2, -1, -100]
    mg.loc[mg.open_time.ge(START + 301 * HOUR), "ma_side"] = -1
    raw.loc[raw.open_time.gt(START + 301 * HOUR), "open"] = 999
    after = build_source_matching_frame(raw, hourly, mg, cases)
    pd.testing.assert_frame_equal(before.loc[:300], after.loc[:300])


@pytest.mark.parametrize("key", ["month", "utc_6h_bucket", "vol_bucket", "known_5m_colour"])
def test_no_relaxation_of_any_exact_key(key):
    raw, hourly, mg, cases = inputs(positions=(250,))
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    own = frame.decision_time.eq(cases.decision_time.iloc[0])
    frame.loc[~own, key] = "different" if key == "month" else (2 if key == "known_5m_colour" else 99)
    controls, assignments, receipt = assign(cases, frame)
    assert controls.empty and assignments.match_status.iloc[0] == "insufficient_exact_controls"
    assert assignments.assigned_controls.iloc[0] == 0 and not receipt["fallback_used"]


def test_global_control_times_are_not_reused_and_partial_matching_is_forbidden():
    raw, hourly, mg, cases = inputs()
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    own = frame.loc[250]
    candidates = frame.loc[frame.candidate_eligible & frame.utc_6h_bucket.eq(own.utc_6h_bucket)].head(3).decision_time
    frame["candidate_eligible"] &= frame.decision_time.isin(candidates)
    controls, assignments, receipt = assign(cases, frame)
    assert len(controls) == 3 and controls.decision_time.is_unique
    assert assignments.match_status.tolist() == ["matched", "insufficient_exact_controls"]
    assert assignments.eligible_controls_before_reuse.tolist() == [3, 3]
    assert assignments.available_controls.tolist() == [3, 0]
    assert receipt["unique_control_times"] == 3 and not receipt["control_time_reuse_allowed"]
    frame.loc[frame.decision_time.eq(candidates.iloc[-1]), "candidate_eligible"] = False
    controls, assignments, _ = assign(cases, frame)
    assert controls.empty and assignments.assigned_controls.eq(0).all()
    assert assignments.available_controls.eq(2).all()


def test_explicit_fold_start_and_end_embargo_are_exact_even_within_same_month():
    raw, hourly, mg, cases = inputs(positions=(350,))
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    start = START + 300 * HOUR
    end = START + 500 * HOUR
    controls, assignments, _ = assign(cases, frame, start_inclusive=start, end_exclusive=end)
    assert assignments.match_status.iloc[0] == "matched"
    assert controls.decision_time.ge(start).all()
    assert controls.decision_time.lt(end - 72 * HOUR).all()
    for begin, finish in ((cases.decision_time.iloc[0] + HOUR, END), (START, cases.decision_time.iloc[0] + 72 * HOUR)):
        controls, assignments, _ = assign(cases, frame, start_inclusive=begin, end_exclusive=finish)
        assert controls.empty and assignments.match_status.iloc[0] == "outside_fold_embargo"


@pytest.mark.parametrize("kwargs", [
    {"start_inclusive": None}, {"end_exclusive": None}, {"start_inclusive": pd.NaT},
    {"end_exclusive": START}, {"embargo_hours": np.nan}, {"count": 0}, {"count": True},
])
def test_invalid_bounds_or_assignment_configuration_is_rejected(kwargs):
    raw, hourly, mg, cases = inputs()
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    with pytest.raises(ValueError):
        assign(cases, frame, **kwargs)


def test_mixed_fold_and_duplicate_cases_reject_and_empty_outputs_have_fixed_schema():
    raw, hourly, mg, cases = inputs()
    frame = build_source_matching_frame(raw, hourly, mg, cases)
    modified = cases.copy()
    modified.loc[1, "fold"] = "other"
    with pytest.raises(ValueError, match="once per fold"):
        assign(modified, frame)
    with pytest.raises(ValueError, match="unique"):
        assign(pd.concat([cases, cases]), frame)
    for empty in (cases.iloc[:0], pd.DataFrame()):
        controls, assignments, receipt = assign(empty, frame)
        assert controls.empty and assignments.empty
        assert controls.columns.tolist() == CONTROL_COLUMNS
        assert assignments.columns.tolist() == ASSIGNMENT_COLUMNS
        assert receipt["case_count"] == receipt["control_count"] == 0


def test_empty_matching_inputs_do_not_invent_support():
    raw, hourly, mg, cases = inputs(positions=())
    frame = build_source_matching_frame(raw.iloc[:0], hourly.iloc[:0], mg.iloc[:0], cases)
    assert frame.empty
    assert {"vol_bucket", "matching_support", "candidate_eligible", "known_5m_valid"}.issubset(frame)
    controls, assignments, receipt = assign(cases, frame)
    assert controls.empty and assignments.empty and receipt["candidate_count_before_exact_keys"] == 0

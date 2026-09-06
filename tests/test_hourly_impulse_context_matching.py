"""Pure synthetic causal matching tests; never read real prices or outcomes."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_context_matching import (
    arm_mask, assign_controls, build_matching_frame,
)


START = pd.Timestamp("2024-01-01T00:00:00Z")
FOLDS = [("dev", "2024-01-01", "2024-02-01")]
BASE_ARM = {"id": "base", "require_hourly_slope": False, "require_context_trend": False, "max_extension_atr": np.inf}


def source_frames(hours=650):
    times = pd.date_range(START, periods=hours, freq="h")
    hourly = pd.DataFrame({
        "open_time": times, "open": 101., "high": 102., "low": 99., "close": 101.,
        "atr": 1., "ma": 100., "ma_side": 1, "ma_slope_atr": .1, "segment_id": 0,
        "context_valid": True, "context_side": 1, "context_slope_atr": .05,
        "context_available": times.floor("4h"), "body_ratio": .5, "range_atr": 3.,
        "volume_ratio": 1., "cross_count24": 2., "efficiency24": .4,
        "long_close_location": 2/3, "short_close_location": 1/3,
    })
    raw_times = pd.date_range(START, periods=(hours+1)*12, freq="5min")
    raw = pd.DataFrame({"open_time": raw_times, "open": 100., "high": 101., "low": 99., "close": 100., "segment_id": 0})
    mg = pd.DataFrame({"open_time": raw_times, "ma_side": 1, "segment_id": 0})
    return hourly, raw, mg


def events_at(hourly, positions=(250, 274)):
    rows = []
    for position in positions:
        hourly.loc[position, "open"] = 99.0  # A completed source K1 body cross.
        rows.append({
            "event_id": "event_{}".format(position), "decision_time": hourly.loc[position, "open_time"]+pd.Timedelta(hours=1),
            "direction": 1, "initial_stop": 98., "signal_atr": hourly.loc[position, "atr"], "fold": "dev",
            "context_valid": True, "context_side": 1, "context_slope_atr": .05,
        })
    return pd.DataFrame(rows)


def test_requests_enforce_all_exact_keys_and_unique_control_times():
    h, raw, mg = source_frames()
    events = events_at(h)
    before = deepcopy(events)
    requests, pairs, receipt = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS)
    assert len(requests) == 6
    assert pairs.match_status.eq("matched").all()
    assert requests.decision_time.nunique() == 6
    assert receipt["outcomes_used"] is False and receipt["fallback_used"] is False
    assert receipt["unique_control_times"] == receipt["request_count"]
    annotated = build_matching_frame(h, raw, mg, FOLDS).set_index("decision_time")
    for event in events.to_dict("records"):
        own = annotated.loc[event["decision_time"]]
        controls = requests.loc[requests.parent_event_id.eq(event["event_id"])]
        for row in controls.to_dict("records"):
            candidate = annotated.loc[row["decision_time"]]
            for key in ("month", "utc_session", "vol_bucket", "ltf_side", "context_side", "context_slope_sign", "fold"):
                assert candidate[key] == own[key]
            assert candidate.control_eligible
            assert row["context_available"] <= row["signal_time"]
            assert row["ltf_available"] == row["decision_time"]
            assert (row["entry_open"]-row["initial_stop"])/row["signal_atr"] == pytest.approx(2.)
    pd.testing.assert_frame_equal(events, before)


def test_same_arm_shared_mask_signs_slope_once_and_applies_context_and_extension():
    h, _, _ = source_frames(8)
    h.loc[1, "ma_slope_atr"] = -.1
    h.loc[2, "context_side"] = -1
    h.loc[3, "context_slope_atr"] = -.05
    h.loc[4, "context_available"] = h.loc[4, "open_time"] + pd.Timedelta(minutes=1)
    h.loc[5, "close"] = 102.
    h.loc[6, "close"] = 99.
    h.loc[7, "context_valid"] = False
    strict = {"require_hourly_slope": True, "require_context_trend": True, "max_extension_atr": 1.0}
    assert arm_mask(h, 1, strict).tolist() == [True, False, False, False, False, False, False, False]
    mirrored = h.iloc[[0]].copy()
    mirrored[["ma_slope_atr", "context_side", "context_slope_atr"]] *= -1
    mirrored["close"] = 99.
    assert arm_mask(mirrored, pd.Series(-1, index=mirrored.index), strict).all()


@pytest.mark.parametrize("seed", [1, 7, 20260906])
def test_assignment_hash_ignores_pnl_closed_and_event_input_order(seed):
    h, raw, mg = source_frames()
    events = events_at(h)
    clean_requests, clean_pairs, clean_receipt = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS, seed=seed)
    contaminated = events.iloc[::-1].copy()
    contaminated["net_return"] = [1000., -1000.]
    contaminated["closed"] = [True, False]
    contaminated["outcome"] = ["winner", "right_censored"]
    contaminated["future_pnl"] = [np.inf, np.nan]
    dirty_requests, dirty_pairs, dirty_receipt = assign_controls(h, raw, mg, contaminated, BASE_ARM, FOLDS, seed=seed)
    assert clean_receipt["assignment_hash"] == dirty_receipt["assignment_hash"]
    pd.testing.assert_frame_equal(clean_requests, dirty_requests)
    pd.testing.assert_frame_equal(clean_pairs, dirty_pairs)
    assert "net_return" not in dirty_requests
    assert "closed" not in dirty_requests


@pytest.mark.parametrize("future_offset", [1, 2, 24])
def test_future_ohlc_and_future_cross_cannot_change_a_candidates_known_features(future_offset):
    h, raw, mg = source_frames()
    candidate_hour = 300
    first = build_matching_frame(h, raw, mg, FOLDS)
    future = h.copy()
    changed_hour = candidate_hour + future_offset
    future.loc[changed_hour:, ["open", "high", "low", "close", "atr"]] = [50., 1000., 1., 200., 99.]
    second = build_matching_frame(future, raw, mg, FOLDS)
    known = ["atr_cut_low", "atr_cut_high", "vol_bucket", "context_side", "context_slope_sign", "entry_open", "ltf_side", "past_or_current_cross_banned", "control_eligible"]
    pd.testing.assert_series_equal(first.loc[candidate_hour, known], second.loc[candidate_hour, known])
    assert not second.loc[candidate_hour, "past_or_current_cross_banned"]


def test_current_and_prior_crosses_are_banned_but_not_the_preceding_candidate():
    h, raw, mg = source_frames()
    h.loc[300, "open"] = 99.
    result = build_matching_frame(h, raw, mg, FOLDS)
    assert result.loc[299:302, "past_or_current_cross_banned"].tolist() == [False, True, True, False]


def test_atr_tercile_uses_only_shifted_prior_hours_and_requires_168():
    h, raw, mg = source_frames()
    h["atr"] = np.arange(len(h), dtype=float) + 1
    result = build_matching_frame(h, raw, mg, FOLDS)
    assert pd.isna(result.loc[167, "vol_bucket"])
    assert result.loc[168, "atr_cut_low"] == pytest.approx(np.quantile(h.loc[:167, "atr"] / 101., 1/3))
    original_cut = result.loc[300, "atr_cut_high"]
    h.loc[300, "atr"] = 1e6
    mutated = build_matching_frame(h, raw, mg, FOLDS)
    assert mutated.loc[300, "atr_cut_high"] == original_cut


def test_all_arms_require_context_known_by_signal_hour_open():
    h, raw, mg = source_frames()
    events = events_at(h, (250,))
    h.loc[250, "context_available"] = h.loc[250, "open_time"] + pd.Timedelta(minutes=1)
    requests, pairs, _ = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS)
    assert requests.empty
    assert pairs.match_status.iloc[0] == "invalid_prior_context"


@pytest.mark.parametrize("stop,atr,direction", [(101., 1., 1), (98., 0., 1), (98., 1., 0), (np.nan, 1., 1)])
def test_invalid_source_risk_is_reported_without_assigning_controls(stop, atr, direction):
    h, raw, mg = source_frames()
    events = events_at(h, (250,))
    events.loc[0, ["initial_stop", "signal_atr", "direction"]] = [stop, atr, direction]
    requests, pairs, receipt = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS)
    assert requests.empty
    assert pairs.match_status.iloc[0] == "invalid_event_risk"
    assert receipt["matched_events"] == 0


def test_missing_current_five_minute_colour_does_not_use_stale_or_future_bar():
    h, raw, mg = source_frames()
    events = events_at(h, (250,))
    end = events.decision_time.iloc[0]
    mg = mg.loc[mg.open_time.ne(end-pd.Timedelta(minutes=5))].copy()
    # A future bar has the desired colour, but is not completed at entry.
    mg.loc[mg.open_time.eq(end), "ma_side"] = 1
    requests, pairs, _ = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS)
    assert requests.empty
    assert pairs.match_status.iloc[0] == "missing_completed_5m_colour"


def test_entry_high_low_close_are_irrelevant_to_assignment_but_entry_open_sets_risk():
    h, raw, mg = source_frames()
    events = events_at(h, (250,))
    baseline, _, receipt = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS)
    changed = raw.copy()
    changed[["high", "low", "close"]] = [1e6, 1e-6, -100.]
    replay, _, repeated = assign_controls(h, changed, mg, events, BASE_ARM, FOLDS)
    assert repeated["assignment_hash"] == receipt["assignment_hash"]
    pd.testing.assert_frame_equal(baseline, replay)
    changed.loc[changed.open_time.eq(events.decision_time.iloc[0]), "open"] = 101.
    risk_changed, _, _ = assign_controls(h, changed, mg, events, BASE_ARM, FOLDS)
    assert risk_changed.transferred_risk_atr.eq(3.).all()


def test_exact_context_slope_sign_and_arm_filter_have_no_relaxation_fallback():
    h, raw, mg = source_frames()
    events = events_at(h, (250,))
    h["context_slope_atr"] = -.05
    h.loc[250, "context_slope_atr"] = .05
    requests, pairs, receipt = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS)
    assert requests.empty
    assert pairs.match_status.iloc[0] == "insufficient_exact_controls"
    assert receipt["fallback_used"] is False
    h["context_slope_atr"] = .05
    h["ma_slope_atr"] = -.1
    h.loc[250, "ma_slope_atr"] = .1
    strict = {**BASE_ARM, "require_hourly_slope": True}
    requests, pairs, _ = assign_controls(h, raw, mg, events, strict, FOLDS)
    assert requests.empty
    assert pairs.match_status.iloc[0] == "insufficient_exact_controls"


def test_control_features_are_own_features_with_direction_signed_once():
    h, raw, mg = source_frames()
    events = events_at(h, (250,))
    h["body_ratio"] = np.arange(len(h), dtype=float) / len(h)
    requests, _, _ = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS)
    lookup = h.set_index("open_time")
    for control in requests.to_dict("records"):
        own = lookup.loc[control["signal_time"]]
        assert control["body_ratio"] == own["body_ratio"]
        assert control["signal_close"] == own["close"]
        assert control["ma_slope_atr"] == control["direction"] * own["ma_slope_atr"]


def test_fold_embargo_and_source_gaps_are_not_silently_bridged():
    h, raw, mg = source_frames()
    events = events_at(h, (250,))
    short_fold = [("dev", "2024-01-01", events.decision_time.iloc[0]+pd.Timedelta(hours=72))]
    requests, pairs, _ = assign_controls(h, raw, mg, events, BASE_ARM, short_fold)
    assert requests.empty
    assert pairs.match_status.iloc[0] == "outside_registered_fold_horizon"
    raw.loc[raw.open_time.eq(events.decision_time.iloc[0]), "segment_id"] = 1
    requests, pairs, _ = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS)
    assert requests.empty
    assert pairs.match_status.iloc[0] == "missing_or_gapped_entry_open"


def test_two_raw_gaps_in_one_missing_hour_do_not_permanently_reject_later_support():
    h, raw, _ = source_frames()
    missing_hour = START + pd.Timedelta(hours=200)
    first_gap, second_gap = missing_hour + pd.Timedelta(minutes=10), missing_hour + pd.Timedelta(minutes=40)
    raw = raw.loc[~raw.open_time.isin([first_gap, second_gap])].copy()
    raw.loc[raw.open_time.gt(first_gap), "segment_id"] = 1
    raw.loc[raw.open_time.gt(second_gap), "segment_id"] = 2
    mg = raw[["open_time", "segment_id"]].copy()
    mg["ma_side"] = 1
    h = h.loc[h.open_time.ne(missing_hour)].reset_index(drop=True)
    h.loc[h.open_time.gt(missing_hour), "segment_id"] = 1
    events = events_at(h, (400,))  # More than 168 clean hourly observations later.
    annotated = build_matching_frame(h, raw, mg, FOLDS)
    source = annotated.loc[annotated.decision_time.eq(events.decision_time.iloc[0])].iloc[0]
    assert source.segment_id == 1
    assert source.signal_source_segment_id == 2
    assert source.known_ltf_colour and source.entry_segment_valid and source.common_eligible
    requests, pairs, _ = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS)
    assert len(requests) == 3
    assert pairs.match_status.iloc[0] == "matched"


def test_zero_context_or_unknown_ltf_colour_cannot_pass_support():
    h, raw, mg = source_frames()
    events = events_at(h, (250,))
    h.loc[250, "context_side"] = 0
    assert not arm_mask(h, 1, BASE_ARM).loc[250]
    _, pairs, _ = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS)
    assert pairs.match_status.iloc[0] == "invalid_prior_context"
    h.loc[250, "context_side"] = 1
    mg.loc[mg.open_time.eq(events.decision_time.iloc[0]-pd.Timedelta(minutes=5)), "ma_side"] = 0
    _, pairs, _ = assign_controls(h, raw, mg, events, BASE_ARM, FOLDS)
    assert pairs.match_status.iloc[0] == "missing_completed_5m_colour"

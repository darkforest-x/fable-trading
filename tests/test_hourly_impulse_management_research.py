"""Synthetic V8 full-parity, paired-return and fixed-zone unit contracts.

No price archives or saved economic outcomes are loaded. Deliberately extreme
unmatched returns expose accidental denominator changes in paired contrasts.
"""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_management_research as research


def episodes(values):
    values = list(values)
    stamps = pd.date_range("2024-01-01", periods=len(values), freq="MS", tz="UTC")
    return pd.DataFrame({
        "event_id": list("abcdef")[:len(values)],
        "mother_decision_time": stamps,
        "mother_signal_time": stamps-pd.Timedelta(hours=1),
        "mother_deadline": stamps+pd.Timedelta(hours=72),
        "fold": ["F0"]*len(values),
        "episode_net_return": values,
        "executed": [True]*len(values),
        "episode_status": ["transition_colour_exit"]*len(values),
        "observed": np.isfinite(values),
    })


def matched(case, control_means, assignments=None):
    result = case[["event_id", "mother_decision_time", "fold"]].copy()
    result["event_net_return"] = case["episode_net_return"].to_numpy()
    result["assigned_controls"] = assignments if assignments is not None else [3, 3, 0]
    result["control_mean_return"] = control_means
    result["excess"] = result.event_net_return-result.control_mean_return
    return result


def serial(values, selected=None):
    result = episodes(values)
    result["event_id"] = "zone_"+result.event_id
    result["portfolio_selected"] = selected if selected is not None else [True]*len(result)
    return result


def prepared():
    before = episodes([.01, -.02, .1])
    after = episodes([.02, -.025, .9])
    match_before = matched(before, [.002, -.005, np.nan])
    match_after = matched(after, [.007, .005, np.nan])
    return [before, after, match_before, match_after,
            serial([.01, -.02, .1]), serial([.02, -.025, .9])]


def test_parity_accepts_csv_nulls_timezone_equivalence_and_additional_columns():
    before = pd.DataFrame({
        "event_id": ["a", "b"],
        "entry_time": ["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"],
        "armed_at": [np.nan, "2024-01-02T00:00:00Z"],
        "mother_deadline": ["2024-01-04T00:00:00Z", "2024-01-05T00:00:00Z"],
        "occupied_until": ["2024-01-01T01:00:00Z", "2024-01-02T01:00:00Z"],
        "ltf_entry_bar_open": ["2023-12-31T23:55:00Z", "2024-01-01T23:55:00Z"],
        "net_return": [.005, -.003],
        "old_diagnostic": ["", np.nan],
    })
    after = before.copy(deep=True)
    after["entry_time"] = pd.to_datetime(before.entry_time, utc=True).dt.tz_convert("Asia/Shanghai")
    after["mother_deadline"] = pd.to_datetime(before.mother_deadline, utc=True)
    after["occupied_until"] = pd.to_datetime(before.occupied_until, utc=True)
    after["ltf_entry_bar_open"] = pd.to_datetime(before.ltf_entry_bar_open, utc=True)
    after["armed_at"] = pd.Series([pd.NaT, pd.Timestamp("2024-01-02", tz="UTC")], dtype=object)
    after["old_diagnostic"] = [np.nan, ""]
    after["new_diagnostic"] = True
    research.assert_saved_parity(before, after.iloc[::-1].reset_index(drop=True))


@pytest.mark.parametrize("column", ["entry_time", "armed_at", "mother_deadline", "occupied_until", "ltf_entry_bar_open"])
def test_parity_rejects_one_nanosecond_temporal_drift(column):
    before = pd.DataFrame({"event_id": ["a"], column: ["2024-01-01T00:00:00Z"]})
    after = before.copy()
    after[column] = pd.to_datetime(after[column], utc=True)+pd.Timedelta(nanoseconds=1)
    with pytest.raises((AssertionError, ValueError)):
        research.assert_saved_parity(before, after)


def test_parity_compares_every_old_column_not_only_known_economic_fields():
    before = pd.DataFrame({"event_id": ["a"], "net_return": [.005], "future_added_contract": [17.]})
    after = before.assign(extra=True)
    research.assert_saved_parity(before, after)
    with pytest.raises((AssertionError, ValueError)):
        research.assert_saved_parity(before, after.assign(future_added_contract=18.))
    with pytest.raises((AssertionError, ValueError, KeyError)):
        research.assert_saved_parity(before, after.drop(columns="future_added_contract"))


def test_parity_float_tolerance_does_not_hide_material_return_change():
    before = pd.DataFrame({"event_id": ["a"], "net_return": [.005]})
    research.assert_saved_parity(before, before.assign(net_return=.005+5e-13))
    with pytest.raises((AssertionError, ValueError)):
        research.assert_saved_parity(before, before.assign(net_return=.005+1e-8))


@pytest.mark.parametrize("replacement", [" ", "nan", "None", "different_reason"])
def test_parity_does_not_normalize_nonempty_object_strings(replacement):
    before = pd.DataFrame({"event_id": ["a"], "reason": [""]})
    after = before.assign(reason=replacement)
    with pytest.raises((AssertionError, ValueError)):
        research.assert_saved_parity(before, after)


@pytest.mark.parametrize("corruption", ["missing", "foreign", "duplicate"])
def test_parity_rejects_identity_corruption(corruption):
    before = pd.DataFrame({"event_id": ["a", "b"], "net_return": [.01, -.02]})
    after = before.copy()
    if corruption == "missing":
        after = after.iloc[:1]
    elif corruption == "foreign":
        after.loc[0, "event_id"] = "foreign"
    else:
        after = pd.concat([after, after.iloc[:1]], ignore_index=True)
    with pytest.raises((AssertionError, ValueError)):
        research.assert_saved_parity(before, after)


def test_case_and_excess_deltas_have_distinct_fixed_denominators():
    inputs = prepared()
    before = deepcopy(inputs)
    frames, effects = research.paired_effects(*inputs)
    for original, unchanged in zip(before, inputs):
        pd.testing.assert_frame_equal(original, unchanged)
    cases = frames["case_delta"].set_index("event_id")
    excess = frames["excess_delta"].set_index("event_id")
    assert set(cases.index) == set(excess.index) == {"a", "b", "c"}
    assert cases.loc[["a", "b", "c"], "difference"].tolist() == pytest.approx([.01, -.005, .8])
    assert effects["case_delta"]["n"] == 3
    assert effects["case_delta"]["mean_bp"] == pytest.approx((.01-.005+.8)/3*10000)
    assert excess.loc[["a", "b"], "difference"].tolist() == pytest.approx([.005, -.015])
    assert pd.isna(excess.loc["c", "difference"])
    assert effects["excess_delta"]["n"] == 2
    assert effects["excess_delta"]["mean_bp"] == pytest.approx(-50.)
    assert effects["case_delta"]["improved"] == 2
    assert effects["case_delta"]["worsened"] == 1
    assert effects["case_delta"]["unchanged"] == 0
    assert effects["case_delta"]["total_pairs"] == 3
    assert effects["case_delta"]["unknown_pairs"] == 0
    assert effects["excess_delta"]["total_pairs"] == 3
    assert effects["excess_delta"]["unknown_pairs"] == 1


def test_excess_difference_equals_same_case_delta_minus_control_delta():
    inputs = prepared()
    frames, _ = research.paired_effects(*inputs)
    case_delta = frames["case_delta"].set_index("event_id").difference
    excess_delta = frames["excess_delta"].set_index("event_id").difference
    old, new = (frame.set_index("event_id") for frame in inputs[2:4])
    control_delta = new.control_mean_return-old.control_mean_return
    assert np.allclose(excess_delta, case_delta-control_delta, equal_nan=True)
    assert np.allclose(excess_delta, new.excess-old.excess, equal_nan=True)


def test_unmatched_extreme_winner_cannot_influence_matched_effect():
    inputs = prepared()
    frames, effects = research.paired_effects(*inputs)
    inputs[1].loc[2, "episode_net_return"] = 1000.
    inputs[3].loc[2, "event_net_return"] = 1000.
    changed_frames, changed_effects = research.paired_effects(*inputs)
    old = frames["excess_delta"].set_index("event_id").difference.sort_index()
    new = changed_frames["excess_delta"].set_index("event_id").difference.sort_index()
    pd.testing.assert_series_equal(old, new)
    assert changed_effects["excess_delta"]["mean_bp"] == effects["excess_delta"]["mean_bp"]
    assert changed_effects["case_delta"]["mean_bp"] > effects["case_delta"]["mean_bp"]


def test_serial_skipped_zone_is_zero_but_selected_unknown_is_not():
    inputs = prepared()
    inputs[4] = serial([.01, 123., .02], [True, False, True])
    inputs[5] = serial([.04, np.nan, np.nan], [True, False, True])
    frames, effects = research.paired_effects(*inputs)
    result = frames["serial_delta"].set_index("event_id")
    assert result.loc["zone_a", "difference"] == pytest.approx(.03)
    assert result.loc["zone_b", "difference"] == 0
    assert pd.isna(result.loc["zone_c", "difference"])
    assert len(result) == 3 and effects["serial_delta"]["n"] == 2
    assert effects["serial_delta"]["mean_bp"] == pytest.approx(150.)
    assert effects["serial_delta"]["improved"] == 1
    assert effects["serial_delta"]["worsened"] == 0
    assert effects["serial_delta"]["unchanged"] == 1
    assert effects["serial_delta"]["unknown_pairs"] == 1
    assert effects["serial_delta"]["total_pairs"] == 3


def test_serial_participation_change_keeps_lost_winner_and_avoided_loser():
    inputs = prepared()
    inputs[4] = serial([.01, -.02, .03], [True, True, False])
    inputs[5] = serial([999., -999., .03], [False, False, True])
    frames, effects = research.paired_effects(*inputs)
    result = frames["serial_delta"].set_index("event_id")
    assert result.loc[["zone_a", "zone_b", "zone_c"], "difference"].tolist() == pytest.approx([-.01, .02, .03])
    assert effects["serial_delta"]["n"] == 3
    assert effects["serial_delta"]["mean_bp"] == pytest.approx(.04/3*10000)


def test_unknown_control_outcome_is_retained_and_not_zero_filled():
    inputs = prepared()
    inputs[3].loc[0, ["control_mean_return", "excess"]] = np.nan
    frames, effects = research.paired_effects(*inputs)
    result = frames["excess_delta"].set_index("event_id")
    assert len(result) == 3
    assert pd.isna(result.loc["a", "difference"])
    assert pd.isna(result.loc["c", "difference"])
    assert effects["excess_delta"]["n"] == 1
    assert effects["excess_delta"]["mean_bp"] == pytest.approx(-150.)


def test_unknown_case_outcome_remains_unknown_in_both_contrasts():
    inputs = prepared()
    inputs[1].loc[0, "episode_net_return"] = np.nan
    inputs[1].loc[0, "observed"] = False
    inputs[3].loc[0, ["event_net_return", "excess"]] = np.nan
    frames, effects = research.paired_effects(*inputs)
    for key in ("case_delta", "excess_delta"):
        result = frames[key].set_index("event_id")
        assert len(result) == 3
        assert pd.isna(result.loc["a", "difference"])
    assert effects["case_delta"]["n"] == 2
    assert effects["excess_delta"]["n"] == 1


def test_pairing_uses_identity_not_row_order():
    inputs = prepared()
    frames, effects = research.paired_effects(*inputs)
    reordered = [frame.iloc[::-1].reset_index(drop=True) if i % 2 else frame
                 for i, frame in enumerate(inputs)]
    actual_frames, actual_effects = research.paired_effects(*reordered)
    for key in ("case_delta", "excess_delta", "serial_delta"):
        expected = frames[key].set_index("event_id").difference.sort_index()
        actual = actual_frames[key].set_index("event_id").difference.sort_index()
        pd.testing.assert_series_equal(expected, actual)
        assert effects[key]["mean_bp"] == actual_effects[key]["mean_bp"]


def test_identical_policies_keep_all_zero_deltas_and_explicit_unpaired_row():
    inputs = prepared()
    inputs[1] = inputs[0].copy(deep=True)
    inputs[3] = inputs[2].copy(deep=True)
    inputs[5] = inputs[4].copy(deep=True)
    _, effects = research.paired_effects(*inputs)
    for key in ("case_delta", "excess_delta", "serial_delta"):
        result = effects[key]
        assert result["mean_bp"] == 0
        assert result["improved"] == result["worsened"] == 0
        assert result["total_pairs"] == 3
        assert result["unchanged"]+result["unknown_pairs"] == 3
    assert effects["case_delta"]["unchanged"] == 3
    assert effects["serial_delta"]["unchanged"] == 3
    assert effects["excess_delta"]["unchanged"] == 2


@pytest.mark.parametrize("frame_index", range(6))
@pytest.mark.parametrize("corruption", ["missing", "foreign", "duplicate"])
def test_paired_effects_rejects_identity_corruption_in_every_input(frame_index, corruption):
    inputs = prepared()
    frame = inputs[frame_index].copy()
    if corruption == "missing":
        frame = frame.iloc[:2]
    elif corruption == "foreign":
        frame.loc[0, "event_id"] = "foreign"
    else:
        frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    inputs[frame_index] = frame
    with pytest.raises((AssertionError, ValueError)):
        research.paired_effects(*inputs)


@pytest.mark.parametrize("frame_index", range(6))
def test_paired_effects_rejects_changed_decision_time_even_by_one_ns(frame_index):
    inputs = prepared()
    inputs[frame_index].loc[0, "mother_decision_time"] += pd.Timedelta(nanoseconds=1)
    with pytest.raises((AssertionError, ValueError)):
        research.paired_effects(*inputs)


@pytest.mark.parametrize("frame_index", [2, 3])
@pytest.mark.parametrize("count", [0, 1, 2, 4])
def test_paired_effects_rejects_changed_or_partial_control_assignments(frame_index, count):
    inputs = prepared()
    inputs[frame_index].loc[0, "assigned_controls"] = count
    with pytest.raises((AssertionError, ValueError)):
        research.paired_effects(*inputs)


@pytest.mark.parametrize("frame_index", [2, 3])
def test_paired_effects_rejects_excess_inconsistent_with_its_components(frame_index):
    inputs = prepared()
    inputs[frame_index].loc[0, "excess"] += .001
    with pytest.raises((AssertionError, ValueError)):
        research.paired_effects(*inputs)


@pytest.mark.parametrize("frame_index", [2, 3])
def test_paired_effects_rejects_matching_case_return_different_from_episode(frame_index):
    inputs = prepared()
    inputs[frame_index].loc[0, "event_net_return"] += .001
    inputs[frame_index].loc[0, "excess"] += .001
    with pytest.raises((AssertionError, ValueError)):
        research.paired_effects(*inputs)


@pytest.mark.parametrize("frame_index", [2, 3])
def test_paired_effects_rejects_finite_control_effect_for_unassigned_request(frame_index):
    inputs = prepared()
    frame = inputs[frame_index]
    frame.loc[2, "control_mean_return"] = 0.
    frame.loc[2, "excess"] = frame.loc[2, "event_net_return"]
    with pytest.raises((AssertionError, ValueError)):
        research.paired_effects(*inputs)

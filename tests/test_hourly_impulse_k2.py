"""Synthetic fixed-mother, first-K2 and completed-bar causality contracts."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.data.hourly_impulse_k2 import build_entry_requests


START = pd.Timestamp("2024-01-01", tz="UTC")


def fixture(direction=1, count=11):
    hourly = pd.DataFrame({
        "open_time": pd.date_range(START, periods=count, freq="1h"),
        "open": 101.0, "high": 102.0, "low": 100.5, "close": 101.5,
        "ma": 100.0, "atr": 3.0, "segment_id": 0,
    })
    if direction == -1:
        hourly = mirror(hourly)
    hourly.attrs.update(bar_minutes=60, ma_kind="SMA", ma_length=40)
    raw = pd.DataFrame({"open_time": pd.date_range(START, periods=count * 12, freq="5min")})
    mothers = pd.DataFrame([{
        "event_id": "mother-a", "signal_time": START,
        "decision_time": START + pd.Timedelta(hours=1), "direction": direction,
        "initial_stop": 95.0 if direction == 1 else 105.0,
        "signal_atr": 2.0, "body_ratio": 0.75, "ma_slope_atr": 0.2,
    }])
    return hourly, raw, mothers


def mirror(frame):
    result = frame.copy()
    result["open"] = 200.0 - frame["open"]
    result["close"] = 200.0 - frame["close"]
    result["high"] = 200.0 - frame["low"]
    result["low"] = 200.0 - frame["high"]
    result["ma"] = 200.0 - frame["ma"]
    return result


def put_k2(hourly, index=1, direction=1, *, opposite_colour=False):
    values = [101.0, 102.0, 97.0, 101.5] if opposite_colour else [100.0, 102.0, 99.0, 100.0]
    if direction == -1:
        o, h, low, c = values
        values = [200 - o, 200 - low, 200 - h, 200 - c]
    hourly.loc[index, ["open", "high", "low", "close"]] = values


def run(hourly, raw, mothers, cutoff=10, **kwargs):
    return build_entry_requests(hourly, raw, mothers, observed_through=START + pd.Timedelta(hours=cutoff), **kwargs)


@pytest.mark.parametrize("direction", [1, -1])
def test_first_hour_k2_body_equality_is_allowed_and_mother_contract_is_frozen(direction):
    h, raw, mothers = fixture(direction)
    put_k2(h, 1, direction)
    requests, statuses = run(h, raw, mothers)
    assert len(requests) == len(statuses) == 1
    request = requests.iloc[0]
    assert request["event_id"] == "mother-a"
    assert request["initial_stop"] == mothers.loc[0, "initial_stop"]
    assert request["signal_atr"] == 2.0
    assert request["k2_atr"] == 3.0
    assert request["body_ratio"] == 0.75
    assert request["mother_signal_time"] == START
    assert request["mother_decision_time"] == START + pd.Timedelta(hours=1)
    assert request["mother_deadline"] == START + pd.Timedelta(hours=73)
    assert request["signal_time"] == START + pd.Timedelta(hours=1)
    assert request["decision_time"] == START + pd.Timedelta(hours=2)
    assert request["k2_initial_stop"] == h.loc[1, "low" if direction == 1 else "high"]
    assert request["wait_hours"] == 1
    assert statuses.loc[0, "status"] == "request_emitted"


@pytest.mark.parametrize("direction", [1, -1])
def test_valid_k2_own_opposite_hl2_colour_does_not_invalidate(direction):
    h, raw, mothers = fixture(direction)
    put_k2(h, 1, direction, opposite_colour=True)
    side = 1 if (h.loc[1, "high"] + h.loc[1, "low"]) / 2 >= h.loc[1, "ma"] else -1
    assert side == -direction
    requests, statuses = run(h, raw, mothers)
    assert len(requests) == 1
    assert statuses.loc[0, "status"] == "request_emitted"


@pytest.mark.parametrize("problem", ["missed_line", "body_cross", "deep_touch", "wide_body", "small_wick", "zero_range", "zero_atr"])
def test_k2_requires_exact_owner_geometry(problem):
    h, raw, mothers = fixture()
    put_k2(h, 1)
    if problem == "missed_line":
        h.loc[1, ["open", "high", "low", "close"]] = [101, 103, 100.1, 101]
    elif problem == "body_cross":
        h.loc[1, ["open", "high", "low", "close"]] = [99.9, 102, 99, 101]
    elif problem == "deep_touch":
        h.loc[1, ["open", "high", "low", "close"]] = [101, 110, 95, 101.5]
    elif problem == "wide_body":
        h.loc[1, ["open", "high", "low", "close"]] = [100, 104, 99, 103]
    elif problem == "small_wick":
        h.loc[1, ["open", "high", "low", "close"]] = [101, 110, 99, 102]
    elif problem == "zero_range":
        h.loc[1, ["open", "high", "low", "close"]] = 101
    else:
        h.loc[1, "atr"] = 0
    requests, statuses = run(h, raw, mothers)
    assert requests.empty
    assert statuses.loc[0, "status"] == "expired_no_k2"


def test_touch_depth_boundaries_zero_and_one_point_five_are_inclusive():
    for low, atr in ((100.0, 3.0), (95.5, 3.0)):
        h, raw, mothers = fixture()
        h.loc[1, ["open", "high", "low", "close", "atr"]] = [101, 102, low, 101.5, atr]
        requests, _ = run(h, raw, mothers)
        assert len(requests) == 1
        assert requests.loc[0, "k2_touch_depth_atr"] == (100 - low) / atr


@pytest.mark.parametrize("direction", [1, -1])
def test_failed_k2_wrong_close_terminates_without_later_rescue(direction):
    h, raw, mothers = fixture()
    h.loc[1, ["open", "high", "low", "close"]] = [101, 102, 98, 99]
    put_k2(h, 2)
    if direction == -1:
        h = mirror(h)
        mothers["direction"] = -1
    requests, statuses = run(h, raw, mothers)
    assert requests.empty
    assert statuses.loc[0, "status"] == "invalidated_wrong_close"
    assert statuses.loc[0, "terminal_time"] == START + pd.Timedelta(hours=2)


def test_failed_k2_wrong_colour_terminates_even_with_close_on_correct_side():
    h, raw, mothers = fixture()
    h.loc[1, ["open", "high", "low", "close"]] = [101, 102, 90, 101.5]
    put_k2(h, 2)
    requests, statuses = run(h, raw, mothers)
    assert requests.empty
    assert statuses.loc[0, "status"] == "invalidated_ma_colour"


def test_earliest_k2_wins_not_later_better_shape_and_no_look_beyond_terminal():
    h, raw, mothers = fixture()
    put_k2(h, 1)
    put_k2(h, 2, opposite_colour=True)
    expected = run(h, raw, mothers)
    h.loc[2:, ["open", "high", "low", "close", "ma", "atr"]] = np.nan
    raw = raw.loc[raw["open_time"] < START + pd.Timedelta(hours=2)]
    actual = run(h, raw, mothers)
    for before, after in zip(expected, actual):
        pd.testing.assert_frame_equal(before, after)
    assert actual[0].loc[0, "k2_time"] == START + pd.Timedelta(hours=1)


def test_eighth_hour_is_inclusive_and_ninth_is_too_late():
    h, raw, mothers = fixture()
    put_k2(h, 8)
    requests, statuses = run(h, raw, mothers)
    assert requests.loc[0, "wait_hours"] == 8
    assert requests.loc[0, "decision_time"] == START + pd.Timedelta(hours=9)
    h, raw, mothers = fixture()
    put_k2(h, 9)
    requests, statuses = run(h, raw, mothers)
    assert requests.empty
    assert statuses.loc[0, "status"] == "expired_no_k2"
    assert statuses.loc[0, "terminal_time"] == START + pd.Timedelta(hours=9)


def test_gap_min_two_treats_first_hour_as_intermediate_not_eligible_k2():
    h, raw, mothers = fixture()
    put_k2(h, 1)
    put_k2(h, 2)
    requests, _ = run(h, raw, mothers, gap_min=2)
    assert requests.loc[0, "wait_hours"] == 2


def test_raw_five_minute_gap_censors_before_hourly_signal_even_if_hourly_row_exists():
    h, raw, mothers = fixture()
    put_k2(h, 1)
    missing = START + pd.Timedelta(hours=1, minutes=15)
    raw = raw.loc[raw["open_time"].ne(missing)]
    requests, statuses = run(h, raw, mothers)
    assert requests.empty
    assert statuses.loc[0, "status"] == "data_gap"
    assert statuses.loc[0, "terminal_time"] == missing + pd.Timedelta(minutes=5)
    # The gap is already knowable during the unfinished candidate hour.
    partial = build_entry_requests(h, raw, mothers, observed_through=missing + pd.Timedelta(minutes=10))
    pd.testing.assert_frame_equal(statuses, partial[1])


@pytest.mark.parametrize("problem", ["hour_missing", "segment_changed"])
def test_hourly_clock_gap_or_segment_change_censors(problem):
    h, raw, mothers = fixture()
    put_k2(h, 2)
    if problem == "hour_missing":
        h = h.drop(index=1)
    else:
        h.loc[1:, "segment_id"] = 1
    requests, statuses = run(h, raw, mothers)
    assert requests.empty
    assert statuses.loc[0, "status"] == "data_gap"


def test_waiting_is_not_a_position_and_mother_stop_does_not_cancel_or_get_validated():
    h, raw, mothers = fixture()
    # Correct-side HL2/close, no K2 due to deep wick, but below mother's stop.
    h.loc[1, ["open", "high", "low", "close"]] = [101, 112, 90, 102]
    put_k2(h, 2)
    mothers["initial_stop"] = 104.0  # Fill/risk validation belongs to L3 only.
    requests, statuses = run(h, raw, mothers)
    assert requests.loc[0, "initial_stop"] == 104
    assert requests.loc[0, "wait_hours"] == 2
    assert statuses.loc[0, "status"] == "request_emitted"


def test_partial_observation_never_reads_unclosed_k2_or_calls_wait_expired():
    h, raw, mothers = fixture()
    put_k2(h, 1)
    requests, statuses = run(h, raw, mothers, cutoff=1.5)
    assert requests.empty
    assert statuses.loc[0, "status"] == "waiting_censored"
    assert statuses.loc[0, "terminal_time"] == START + pd.Timedelta(hours=1.5)
    assert pd.isna(statuses.loc[0, "k2_time"])


def test_future_mutation_prefix_and_timezone_invariance():
    h, raw, mothers = fixture()
    put_k2(h, 2)
    expected = run(h, raw, mothers, cutoff=3)
    prefix_h = h.loc[h["open_time"] < START + pd.Timedelta(hours=3)].copy()
    prefix_raw = raw.loc[raw["open_time"] < START + pd.Timedelta(hours=3)].copy()
    for frame, names in ((prefix_h, ["open_time"]), (prefix_raw, ["open_time"]), (mothers, ["signal_time", "decision_time"])):
        for name in names:
            frame[name] = frame[name].dt.tz_convert("Asia/Shanghai")
    actual = run(prefix_h, prefix_raw, mothers, cutoff=3)
    for before, after in zip(expected, actual):
        pd.testing.assert_frame_equal(before, after)


def test_competing_mothers_are_independent_and_never_replace_original_identity():
    h, raw, mothers = fixture()
    put_k2(h, 2)
    second = mothers.iloc[0].to_dict()
    second.update(event_id="mother-b", signal_time=START + pd.Timedelta(hours=1), decision_time=START + pd.Timedelta(hours=2))
    mothers = pd.concat([mothers, pd.DataFrame([second])], ignore_index=True)
    requests, statuses = run(h, raw, mothers)
    assert requests["event_id"].tolist() == ["mother-a", "mother-b"]
    assert requests["wait_hours"].tolist() == [2, 1]
    assert requests["mother_deadline"].tolist() == [START + pd.Timedelta(hours=73), START + pd.Timedelta(hours=74)]
    assert statuses["event_id"].tolist() == mothers["event_id"].tolist()


def test_every_mother_has_terminal_status_empty_outputs_keep_schema_and_inputs_unchanged():
    h, raw, mothers = fixture()
    original = mothers.copy(deep=True)
    requests, statuses = run(h, raw, mothers)
    assert requests.empty and "k2_initial_stop" in requests and "event_id" in requests
    assert len(statuses) == len(mothers)
    assert statuses.loc[0, "status"] == "expired_no_k2"
    pd.testing.assert_frame_equal(mothers, original)
    no_requests, no_statuses = run(h, raw, mothers.iloc[:0])
    assert no_requests.empty and no_statuses.empty
    assert list(no_requests.columns) == list(requests.columns)
    assert list(no_statuses.columns) == list(statuses.columns)


@pytest.mark.parametrize("problem", ["epoch_raw", "epoch_object", "epoch_mother", "duplicate_id", "duplicate_raw", "wrong_clock", "wrong_ma", "gap_zero", "gap_nine"])
def test_invalid_input_contracts_fail_explicitly(problem):
    h, raw, mothers = fixture()
    kwargs = {}
    if problem == "epoch_raw":
        raw["open_time"] = raw["open_time"].astype("int64")
    elif problem == "epoch_object":
        raw["open_time"] = raw["open_time"].astype("int64").astype(object)
    elif problem == "epoch_mother":
        mothers["signal_time"] = mothers["signal_time"].astype("int64")
    elif problem == "duplicate_id":
        mothers = pd.concat([mothers, mothers], ignore_index=True)
    elif problem == "duplicate_raw":
        raw.loc[1, "open_time"] = raw.loc[0, "open_time"]
    elif problem == "wrong_clock":
        mothers["decision_time"] += pd.Timedelta(hours=1)
    elif problem == "wrong_ma":
        h.attrs["ma_kind"] = "EMA"
    elif problem == "gap_zero":
        kwargs["gap_min"] = 0
    else:
        kwargs["gap_max"] = 9
    with pytest.raises(ValueError):
        run(h, raw, mothers, **kwargs)

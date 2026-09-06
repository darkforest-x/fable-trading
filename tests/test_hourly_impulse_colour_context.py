"""Synthetic native-colour state/availability tests; no price files opened."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.data.hourly_impulse import add_features, resample_complete
from yoyo.data.hourly_impulse_colour_context import CONTEXT_COLUMNS, attach_entry_colour_context


START = pd.Timestamp("2024-01-01", tz="UTC")


def fixture():
    times = pd.date_range(START, periods=8, freq="5min")
    raw = pd.DataFrame({"open_time": times, "open": 100.0, "segment_id": 7})
    management = pd.DataFrame({"open_time": times, "ma_side": [1, -1, 1, -1, 1, -1, 1, -1], "segment_id": 99})
    management.attrs["bar_minutes"] = 5
    entries = pd.DataFrame({
        "event_id": ["a", "b", "c", "d"],
        "decision_time": times[[1, 1, 2, 2]], "direction": [1, -1, -1, 1],
        "sentinel": [3, 4, 5, 6],
    })
    return raw, management, entries


def test_exact_close_equality_alignment_and_mirror_use_previous_not_current_bar():
    raw, management, entries = fixture()
    result = attach_entry_colour_context(raw, management, entries)
    assert result.ltf_entry_side.tolist() == [1, 1, -1, -1]
    assert result.ltf_entry_aligned.tolist() == [True, False, True, False]
    assert result.ltf_entry_state.tolist() == ["aligned", "opposite", "aligned", "opposite"]
    assert result.ltf_entry_context_reason.eq("known").all()
    assert result.ltf_entry_available_at.equals(entries.decision_time)
    assert result.ltf_entry_bar_open.equals(entries.decision_time-pd.Timedelta(minutes=5))
    assert str(result.ltf_entry_side.dtype) == "Int64"
    assert str(result.ltf_entry_aligned.dtype) == "boolean"


def test_source_and_management_segment_counters_are_not_compared():
    raw, management, entries = fixture()
    management["segment_id"] = [99, 99, 100, 100, 101, 101, 102, 102]
    result = attach_entry_colour_context(raw, management, entries)
    assert result.ltf_entry_state.ne("unknown").all()


def test_ma_equality_is_precomputed_positive_colour_not_real_body_direction():
    count = 45
    raw = pd.DataFrame({
        "open_time": pd.date_range(START, periods=count, freq="5min"),
        "open": 100.2, "high": 101.0, "low": 99.0, "close": 99.8, "volume": 10.0,
    })
    five = resample_complete(raw, 5)
    management = add_features(five)
    assert management.loc[40, "hl2"] == management.loc[40, "ma"]
    assert management.loc[40, "close"] < management.loc[40, "open"]
    entries = pd.DataFrame({"decision_time": [raw.loc[41, "open_time"]], "direction": [1]})
    result = attach_entry_colour_context(five, management, entries)
    assert result.loc[0, "ltf_entry_state"] == "aligned"
    assert result.loc[0, "ltf_entry_side"] == 1


def test_raw_gap_into_entry_makes_unknown_not_opposite():
    raw, management, entries = fixture()
    raw.loc[1:, "segment_id"] = 8
    result = attach_entry_colour_context(raw, management, entries.iloc[:1])
    assert result.loc[0, "ltf_entry_state"] == "unknown"
    assert pd.isna(result.loc[0, "ltf_entry_side"])
    assert pd.isna(result.loc[0, "ltf_entry_aligned"])
    assert result.loc[0, "ltf_entry_context_reason"] == "raw_source_gap"


@pytest.mark.parametrize("problem,reason", [
    ("empty", "no_completed_management_bar"),
    ("stale", "stale_management_bar"),
    ("missing_entry", "missing_raw_entry_open"),
    ("missing_source", "missing_raw_context_bar"),
    ("bad_open", "invalid_raw_entry_open"),
    ("bad_segment", "invalid_raw_source_segment"),
])
def test_absent_stale_and_invalid_source_states_are_explicit(problem, reason):
    raw, management, entries = fixture()
    entries = entries.iloc[2:3]
    if problem == "empty":
        management = management.iloc[:0]
    elif problem == "stale":
        management = management.drop(index=1)
    elif problem == "missing_entry":
        raw = raw.drop(index=2)
    elif problem == "missing_source":
        raw = raw.drop(index=1)
    elif problem == "bad_open":
        raw.loc[2, "open"] = np.nan
    else:
        raw.loc[2, "segment_id"] = np.nan
    result = attach_entry_colour_context(raw, management, entries)
    assert result.iloc[0].ltf_entry_state == "unknown"
    assert result.iloc[0].ltf_entry_context_reason == reason


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, 0, 2, -2, True, "1"])
def test_unavailable_or_invalid_management_side_remains_nullable_unknown(value):
    raw, management, entries = fixture()
    management["ma_side"] = management["ma_side"].astype(object)
    management.loc[0, "ma_side"] = value
    result = attach_entry_colour_context(raw, management, entries.iloc[:1])
    assert result.iloc[0].ltf_entry_state == "unknown"
    assert result.iloc[0].ltf_entry_context_reason == "invalid_management_side"
    assert pd.isna(result.iloc[0].ltf_entry_aligned)


def test_unsorted_duplicate_entries_and_duplicate_index_preserve_all_original_values():
    raw, management, entries = fixture()
    entries = entries.iloc[[3, 0, 2, 0]].copy()
    entries.index = pd.Index([9, 4, 4, 1], name="owner_index")
    entries.attrs["origin"] = "fixed_mother_requests"
    original = entries.copy(deep=True)
    result = attach_entry_colour_context(raw, management, entries)
    pd.testing.assert_frame_equal(result[entries.columns], entries)
    pd.testing.assert_frame_equal(entries, original)
    assert result.ltf_entry_state.tolist() == ["opposite", "aligned", "aligned", "aligned"]
    assert result.attrs == entries.attrs


def test_no_future_ohlc_or_unclosed_management_colour_can_change_state():
    raw, management, entries = fixture()
    entries = entries.iloc[:1]
    baseline = attach_entry_colour_context(raw, management, entries)
    boundary = entries.decision_time.iloc[0]
    management.loc[management.open_time.ge(boundary), "ma_side"] = np.nan
    for field in ("high", "low", "close"):
        raw[field] = np.nan
        management[field] = np.nan
    raw.loc[raw.open_time.gt(boundary), "open"] = np.inf
    actual = attach_entry_colour_context(raw, management, entries)
    pd.testing.assert_frame_equal(baseline, actual)


def test_prefix_with_entry_open_but_no_postentry_management_bar_is_identical():
    raw, management, entries = fixture()
    entries = entries.iloc[:1]
    baseline = attach_entry_colour_context(raw, management, entries)
    boundary = entries.decision_time.iloc[0]
    prefix = attach_entry_colour_context(
        raw.loc[raw.open_time.le(boundary)],
        management.loc[management.open_time.lt(boundary)], entries,
    )
    pd.testing.assert_frame_equal(baseline, prefix)


def test_timezone_normalization_is_internal_and_original_columns_unchanged():
    raw, management, entries = fixture()
    expected = attach_entry_colour_context(raw, management, entries)
    for frame, name in ((raw, "open_time"), (management, "open_time"), (entries, "decision_time")):
        frame[name] = frame[name].dt.tz_convert("Asia/Shanghai")
    result = attach_entry_colour_context(raw, management, entries)
    pd.testing.assert_frame_equal(result[entries.columns], entries)
    pd.testing.assert_frame_equal(result[CONTEXT_COLUMNS], expected[CONTEXT_COLUMNS])


def test_empty_entries_return_typed_context_schema():
    raw, management, entries = fixture()
    result = attach_entry_colour_context(raw, management, entries.iloc[:0])
    assert result.empty
    assert result.columns.tolist() == entries.columns.tolist() + CONTEXT_COLUMNS
    assert str(result.ltf_entry_side.dtype) == "Int64"
    assert str(result.ltf_entry_aligned.dtype) == "boolean"
    assert str(result.ltf_entry_available_at.dtype) == "datetime64[ns, UTC]"


@pytest.mark.parametrize("direction", [pd.NA, None, np.nan, 0, 2, True, "1"])
def test_invalid_direction_preserves_row_with_unknown_not_false(direction):
    raw, management, entries = fixture()
    entries = entries.iloc[:1].copy()
    entries["direction"] = pd.Series([direction], dtype=object)
    result = attach_entry_colour_context(raw, management, entries)
    assert len(result) == 1
    assert result.iloc[0].ltf_entry_context_reason == "invalid_entry_direction"
    assert pd.isna(result.iloc[0].ltf_entry_aligned)


@pytest.mark.parametrize("problem", ["missing", "unaligned"])
def test_invalid_entry_timestamp_preserves_request_but_cannot_attach_state(problem):
    raw, management, entries = fixture()
    entries = entries.iloc[:1].copy()
    entries.loc[0, "decision_time"] = pd.NaT if problem == "missing" else START+pd.Timedelta(seconds=1)
    result = attach_entry_colour_context(raw, management, entries)
    assert len(result) == 1
    assert result.iloc[0].ltf_entry_state == "unknown"
    assert pd.isna(result.iloc[0].ltf_entry_bar_open)
    assert result.iloc[0].ltf_entry_context_reason == ("invalid_entry_time" if problem == "missing" else "unaligned_entry_time")


@pytest.mark.parametrize("problem", ["duplicate_raw", "duplicate_management", "unordered_source", "unaligned_source", "epoch", "unsupported_minutes", "wrong_frame_minutes", "existing_context"])
def test_invalid_source_contracts_fail_explicitly(problem):
    raw, management, entries = fixture()
    kwargs = {}
    if problem == "duplicate_raw":
        raw.loc[1, "open_time"] = raw.loc[0, "open_time"]
    elif problem == "duplicate_management":
        management.loc[1, "open_time"] = management.loc[0, "open_time"]
    elif problem == "unordered_source":
        raw = raw.iloc[::-1]
    elif problem == "unaligned_source":
        management.loc[0, "open_time"] += pd.Timedelta(seconds=1)
    elif problem == "epoch":
        raw["open_time"] = raw["open_time"].astype("int64").astype(object)
    elif problem == "unsupported_minutes":
        kwargs["management_minutes"] = 15
    elif problem == "wrong_frame_minutes":
        management.attrs["bar_minutes"] = 15
    else:
        entries["ltf_entry_state"] = "aligned"
    with pytest.raises(ValueError):
        attach_entry_colour_context(raw, management, entries, **kwargs)

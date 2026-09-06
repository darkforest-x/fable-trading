"""Synthetic four-hour availability and missing-source-bar contracts."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.data.hourly_impulse import add_features
from yoyo.data.hourly_impulse_context import (
    CONTEXT_COLUMNS, add_prior_4h_context, complete_4h_bars,
)


def raw_bars(count=48 * 60, start="2024-01-01", trend=0.01):
    price = 100.0 + np.arange(count) * trend
    return pd.DataFrame({
        "open_time": pd.date_range(start, periods=count, freq="5min", tz="UTC"),
        "open": price, "high": price + 1, "low": price - 1,
        "close": price + 0.1, "volume": np.full(count, 10.0),
    })


def hours(*offsets):
    return pd.DataFrame({"open_time": [pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=h) for h in offsets]})


def test_complete_48_bar_aggregation_utc_edges_and_gap_segmentation():
    raw = raw_bars(48 * 4, trend=0)
    raw.loc[0, "open"] = 99.5
    raw.loc[47, "close"] = 100.5
    raw.loc[4, "high"] = 107
    raw.loc[20, "low"] = 96
    raw = raw.drop(index=55)
    raw["open_time"] = raw["open_time"].dt.tz_convert("Asia/Shanghai")
    bars = complete_4h_bars(raw)
    assert bars["open_time"].dt.hour.tolist() == [0, 8, 12]
    assert bars["segment_id"].tolist() == [0, 1, 1]
    assert bars["raw_segment_id"].tolist() == [0, 1, 1]
    assert bars.iloc[0][["open", "high", "low", "close", "volume"]].tolist() == [99.5, 107, 96, 100.5, 480]
    assert str(bars["open_time"].dt.tz) == "UTC"
    assert bars.attrs["bar_minutes"] == 240
    edges = complete_4h_bars(raw_bars(48 * 3).iloc[1:-1])
    assert edges["open_time"].dt.hour.tolist() == [4]


def test_exact_boundary_and_inside_four_hour_bar_use_only_prior_context():
    raw = raw_bars()
    joined = add_prior_4h_context(raw, hours(171, 172, 173, 175, 176))
    assert joined["context_valid"].tolist() == [False, True, True, True, True]
    assert joined["context_available"].tolist() == hours(168, 172, 172, 172, 176)["open_time"].tolist()
    assert joined["context_side"].tolist() == [0, 1, 1, 1, 1]
    featured = add_features(complete_4h_bars(raw))
    expected = featured.iloc[42]["ma_slope_atr"]
    assert joined.loc[1:3, "context_slope_atr"].tolist() == pytest.approx([expected] * 3)


def test_context_colour_and_slope_preserve_bearish_direction():
    joined = add_prior_4h_context(raw_bars(trend=-0.01), hours(172, 176))
    assert joined["context_valid"].all()
    assert joined["context_side"].eq(-1).all()
    assert joined["context_slope_atr"].lt(0).all()


def test_future_mutation_including_k1_never_changes_prior_context():
    raw = raw_bars()
    query = hours(172, 173, 174, 175)
    expected = add_prior_4h_context(raw, query)
    changed = raw.copy()
    future = changed["open_time"].ge(query["open_time"].iloc[0])
    changed.loc[future, ["open", "high", "low", "close"]] *= 7
    changed.loc[future, "volume"] *= 999
    actual = add_prior_4h_context(changed, query)
    pd.testing.assert_frame_equal(expected, actual)


@pytest.mark.parametrize("hour", [172, 173, 175, 176])
def test_raw_prefix_invariance_without_any_k1_or_future_bar(hour):
    raw = raw_bars()
    query = hours(hour)
    prefix = raw.loc[raw["open_time"] < query["open_time"].iloc[0]]
    pd.testing.assert_frame_equal(
        add_prior_4h_context(raw, query), add_prior_4h_context(prefix, query),
    )


def test_gap_after_context_close_invalidates_even_when_less_than_four_hours_old():
    raw = raw_bars()
    missing = pd.Timestamp("2024-01-08 04:15", tz="UTC")  # hour 172 + 15m
    raw = raw.loc[raw["open_time"].ne(missing)]
    joined = add_prior_4h_context(raw, hours(172, 173, 174, 175, 176, 180))
    assert joined["context_valid"].tolist() == [True, False, False, False, False, False]
    assert joined.loc[1:4, "context_available"].isna().all()
    assert joined.loc[1:, "context_side"].eq(0).all()
    assert joined.loc[1:, "context_slope_atr"].isna().all()
    # At 180h a new complete bar exists, but its rolling state has restarted.
    assert joined.loc[5, "context_available"] == hours(180).loc[0, "open_time"]


def test_missing_last_five_minute_bar_invalidates_context_before_gap_is_bridged():
    raw = raw_bars()
    missing = hours(173).loc[0, "open_time"] - pd.Timedelta(minutes=5)
    raw = raw.loc[raw["open_time"].ne(missing)]
    joined = add_prior_4h_context(raw, hours(173))
    assert not joined.loc[0, "context_valid"]
    assert pd.isna(joined.loc[0, "context_available"])


def test_missing_four_hour_bucket_resets_all_warmup_and_recovers_after_43_bars():
    raw = raw_bars(48 * 100)
    cutoff = hours(172).loc[0, "open_time"]
    raw = raw.loc[~raw["open_time"].between(cutoff, cutoff + pd.Timedelta(hours=4), inclusive="left")]
    joined = add_prior_4h_context(raw, hours(172, 176, 180, 344, 348))
    assert joined["context_valid"].tolist() == [True, False, False, False, True]
    assert joined.loc[4, "context_side"] == 1


def test_stale_raw_tail_and_no_prior_context_fail_closed():
    raw = raw_bars(48 * 43)
    joined = add_prior_4h_context(raw, hours(0, 172, 173, 176, 180))
    assert joined["context_valid"].tolist() == [False, True, False, False, False]
    assert joined.loc[[0, 2, 3, 4], "context_available"].isna().all()


def test_flat_zero_atr_remains_invalid_after_warmup():
    raw = raw_bars(trend=0)
    raw[["open", "high", "low", "close"]] = 100.0
    joined = add_prior_4h_context(raw, hours(172, 176))
    assert not joined["context_valid"].any()
    assert joined["context_side"].eq(0).all()
    assert joined["context_slope_atr"].isna().all()


def test_empty_inputs_preserve_schema_index_attrs_and_do_not_mutate_input():
    query = hours(172, 173)
    query.index = pd.Index([7, 9], name="original")
    query["sentinel"] = ["a", "b"]
    query.attrs["bar_minutes"] = 60
    original = query.copy(deep=True)
    empty_raw = raw_bars(0)
    joined = add_prior_4h_context(empty_raw, query)
    pd.testing.assert_frame_equal(query, original)
    pd.testing.assert_index_equal(joined.index, query.index)
    assert joined.attrs == query.attrs
    assert joined["sentinel"].tolist() == ["a", "b"]
    assert not joined["context_valid"].any()
    empty_join = add_prior_4h_context(raw_bars(), query.iloc[:0])
    assert list(empty_join.columns) == list(query.columns) + CONTEXT_COLUMNS
    assert empty_join.empty
    assert complete_4h_bars(empty_raw).empty


@pytest.mark.parametrize("problem", ["duplicate", "unordered", "unaligned", "numeric", "bad_ohlc", "nonfinite"])
def test_invalid_raw_is_rejected_without_sorting_or_imputation(problem):
    raw = raw_bars(48)
    if problem == "duplicate":
        raw.loc[1, "open_time"] = raw.loc[0, "open_time"]
    elif problem == "unordered":
        raw = raw.iloc[::-1]
    elif problem == "unaligned":
        raw.loc[0, "open_time"] += pd.Timedelta(seconds=1)
    elif problem == "numeric":
        raw["open_time"] = raw["open_time"].astype("int64")
    elif problem == "bad_ohlc":
        raw.loc[0, "low"] = 1000
    else:
        raw.loc[0, "volume"] = np.inf
    with pytest.raises(ValueError):
        add_prior_4h_context(raw, hours(4))


@pytest.mark.parametrize("problem", ["duplicate", "unordered", "unaligned", "numeric", "missing", "repeated_context", "wrong_timeframe"])
def test_invalid_hourly_join_keys_are_rejected(problem):
    query = hours(172, 173)
    if problem == "duplicate":
        query.loc[1, "open_time"] = query.loc[0, "open_time"]
    elif problem == "unordered":
        query = query.iloc[::-1]
    elif problem == "unaligned":
        query.loc[0, "open_time"] += pd.Timedelta(minutes=15)
    elif problem == "numeric":
        query["open_time"] = query["open_time"].astype("int64")
    elif problem == "missing":
        query = query.drop(columns="open_time")
    elif problem == "repeated_context":
        query["context_valid"] = True
    else:
        query.attrs["bar_minutes"] = 15
    with pytest.raises(ValueError):
        add_prior_4h_context(raw_bars(), query)

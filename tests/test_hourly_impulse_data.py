"""Synthetic causality and candle-semantics contracts for hourly impulse data."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.data.hourly_impulse import add_features, make_entries, resample_complete


def raw_bars(count=600, freq="5min", start="2026-01-01"):
    return pd.DataFrame({
        "open_time": pd.date_range(start, periods=count, freq=freq, tz="UTC"),
        "open": np.full(count, 100.0), "high": np.full(count, 101.0),
        "low": np.full(count, 99.0), "close": np.full(count, 100.0),
        "volume": np.full(count, 10.0),
    })


def hourly_bars(count=100):
    bars = raw_bars(count, freq="60min")
    bars["segment_id"] = 0
    bars.attrs["bar_minutes"] = 60
    return bars


def test_complete_utc_aggregation_and_missing_bucket_segments():
    bars = raw_bars(48)
    bars.loc[0, "open"] = 99.5
    bars.loc[11, "close"] = 100.5
    bars.loc[7, "high"] = 105.0
    bars.loc[9, "low"] = 97.0
    bars = bars.drop(index=13)
    result = resample_complete(bars, 60)
    assert result["open_time"].dt.hour.tolist() == [0, 2, 3]
    assert result["segment_id"].tolist() == [0, 1, 1]
    assert result.iloc[0][["open", "high", "low", "close", "volume"]].tolist() == [99.5, 105.0, 97.0, 100.5, 120.0]
    assert str(result["open_time"].dt.tz) == "UTC"
    assert result.attrs["bar_minutes"] == 60
    partial_edges = resample_complete(raw_bars(7).iloc[1:], 15)
    assert partial_edges["open_time"].tolist() == [pd.Timestamp("2026-01-01 00:15", tz="UTC")]
    passthrough = resample_complete(bars, 5)
    assert len(passthrough) == len(bars)
    assert passthrough["segment_id"].iloc[13] == 1


@pytest.mark.parametrize("problem", ["duplicate", "unordered", "unaligned", "bad_bounds", "nan"])
def test_bad_raw_data_is_rejected_without_sorting_or_filling(problem):
    bars = raw_bars(24)
    if problem == "duplicate":
        bars.loc[1, "open_time"] = bars.loc[0, "open_time"]
    elif problem == "unordered":
        bars = bars.iloc[::-1]
    elif problem == "unaligned":
        bars.loc[0, "open_time"] += pd.Timedelta(seconds=1)
    elif problem == "bad_bounds":
        bars.loc[0, "low"] = 102
    else:
        bars.loc[0, "close"] = np.nan
    with pytest.raises(ValueError):
        resample_complete(bars, 15)


def test_atr_uses_arithmetic_seed_then_wilder_and_restarts_at_gap():
    bars = hourly_bars(30)
    widths = np.arange(1, 31, dtype=float) * 2
    bars["high"] = 100 + widths / 2
    bars["low"] = 100 - widths / 2
    bars.loc[16:, "segment_id"] = 1
    bars.loc[16:, "open_time"] += pd.Timedelta(hours=1)
    result = add_features(bars, "SMA", 3)
    assert result["atr"].iloc[:13].isna().all()
    assert result["atr"].iloc[13] == pytest.approx(np.mean(widths[:14]))
    assert result["atr"].iloc[14] == pytest.approx((15 * 13 + 30) / 14)
    assert result["atr"].iloc[16:29].isna().all()
    assert result["atr"].iloc[29] == pytest.approx(np.mean(widths[16:30]))
    assert result["ma"].iloc[16:18].isna().all()
    assert result["volume_ratio"].iloc[16:].isna().all()
    assert result["ma_side"].iloc[16:18].eq(0).all()


@pytest.mark.parametrize("kind", ["SMA", "EMA"])
def test_future_mutation_never_changes_preceding_features_or_entries(kind):
    bars = hourly_bars(100)
    change = np.sin(np.arange(100) / 3) * 4
    for column in ("open", "high", "low", "close"):
        bars[column] += change
    bars.loc[60, ["open", "high", "low", "close"]] = [95, 110, 94.9, 109.8]
    original = add_features(bars, kind, 20)
    altered = bars.copy()
    altered.loc[70:, ["open", "high", "low", "close"]] *= 5
    altered.loc[70:, "volume"] *= 1000
    mutated = add_features(altered, kind, 20)
    pd.testing.assert_frame_equal(original.iloc[:70], mutated.iloc[:70])
    before = make_entries(original.iloc[:70], {})
    assert len(before) >= 1
    after = make_entries(mutated, {})
    after = after.loc[after["signal_time"] < bars.loc[70, "open_time"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(before, after)


def test_engulfing_uses_real_bodies_even_when_both_indicator_colours_are_bearish():
    bars = hourly_bars(44)
    for column in ("open", "high", "low", "close"):
        bars[column] += 10
    bars.loc[40, ["open", "high", "low", "close"]] = [103, 105, 99, 100]
    bars.loc[41, ["open", "high", "low", "close"]] = [99, 105, 90, 104]
    bars.loc[42, ["open", "high", "low", "close"]] = [104, 106, 98, 99]
    bars.loc[43, ["open", "high", "low", "close"]] = [99, 106, 98, 105]
    featured = add_features(bars)
    assert featured["ma_side"].iloc[40:].tolist() == [-1, -1, -1, -1]
    assert bool(featured.loc[41, "bullish_engulf"])
    # An exactly equal inverse body is not engulfing; one strict edge is required.
    assert not bool(featured.loc[42, "bearish_engulf"])
    assert bool(featured.loc[43, "bullish_engulf"])
    bars.loc[42, "open"] = 104.1
    assert bool(add_features(bars).loc[42, "bearish_engulf"])


def impulse_example(direction=1):
    bars = hourly_bars(42)
    if direction == 1:
        bars.loc[40, ["open", "high", "low", "close"]] = [99, 104, 98.9, 103.8]
        bars.loc[41, ["open", "high", "low", "close"]] = [103, 107, 98.9, 106.8]
    else:
        bars.loc[40, ["open", "high", "low", "close"]] = [101, 101.1, 96, 96.2]
        bars.loc[41, ["open", "high", "low", "close"]] = [97, 101.1, 93, 93.2]
    return add_features(bars)


@pytest.mark.parametrize("direction", [1, -1])
def test_real_body_cross_enters_at_hour_close_but_wick_only_does_not(direction):
    featured = impulse_example(direction)
    entries = make_entries(featured, {})
    assert len(entries) == 1
    entry = entries.iloc[0]
    assert entry["direction"] == direction
    assert entry["signal_time"] == featured.loc[40, "open_time"]
    assert entry["decision_time"] == featured.loc[41, "open_time"]
    assert entry["initial_stop"] == featured.loc[40, "low" if direction == 1 else "high"]
    assert entry["extension_atr"] > 0
    assert entry["signal_time"] < entry["decision_time"]
    assert entry["event_id"].endswith("_L" if direction == 1 else "_S")


def test_optional_entry_filters_apply_only_when_requested_and_unknown_keys_fail():
    featured = impulse_example()
    assert len(make_entries(featured, {})) == 1
    assert make_entries(featured, {"side": "short"}).empty
    assert make_entries(featured, {"min_volume_ratio": 2}).empty
    assert make_entries(featured, {"max_extension_atr": 0.1}).empty
    assert make_entries(featured, {"shape": "engulf_only"}).empty
    assert len(make_entries(featured, {"require_breakout20": True})) == 1
    with pytest.raises(ValueError, match="unknown"):
        make_entries(featured, {"volume_min": 2})


def test_prior_context_excludes_current_bar_and_unknown_ma_is_zero():
    bars = hourly_bars(50)
    bars.loc[40, ["high", "low", "volume"]] = [200, 1, 2000]
    result = add_features(bars)
    assert result.loc[40, "prior_high20"] == 101
    assert result.loc[40, "prior_low20"] == 99
    assert result.loc[40, "prior_range_median20"] == 2
    assert result.loc[40, "volume_ratio"] == 200
    assert result["ma_side"].iloc[:39].eq(0).all()
    assert result.loc[30, "efficiency24"] == 0


def test_cross_count_excludes_current_flip():
    bars = hourly_bars(50)
    wave = np.where(np.arange(50) % 2 == 0, 2.0, -2.0)
    for column in ("open", "high", "low", "close"):
        bars[column] += wave
    featured = add_features(bars, "SMA", 2)
    assert featured.loc[26, "cross_count24"] == 24
    bars.loc[26, ["open", "high", "low", "close"]] -= 20
    altered = add_features(bars, "SMA", 2)
    assert featured.loc[26, "ma_side"] != altered.loc[26, "ma_side"]
    assert featured.loc[26, "cross_count24"] == altered.loc[26, "cross_count24"]

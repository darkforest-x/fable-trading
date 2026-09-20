"""Focused behavior checks for the pure four-hour range evaluator."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.four_hour_range import BAR, prepare, simulate, trace_trade


def frame(start: str, periods: int, price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq="5min", tz="UTC")
    return pd.DataFrame({"open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 1.0}, index=idx)


def set_bar(data: pd.DataFrame, when: str, *, op: float | None = None, high: float | None = None, low: float | None = None, close: float | None = None) -> int:
    i = data.index.get_loc(pd.Timestamp(when, tz="UTC"))
    for key, value in {"open": op, "high": high, "low": low, "close": close}.items():
        if value is not None:
            data.iloc[i, data.columns.get_loc(key)] = value
    return int(i)


def ny_early_data(day: str = "2025-01-06", periods: int = 288) -> pd.DataFrame:
    # NY winter midnight is 05:00 UTC; a full NY day is 288 5m bars.
    return frame(f"{day} 05:00", periods)


def test_signals_start_after_visible_range_and_use_next_open() -> None:
    data = ny_early_data()
    set_bar(data, "2025-01-06 08:55", close=98, low=97)  # still before NY 04:00
    outside = set_bar(data, "2025-01-06 09:00", close=96, low=96)
    returned = set_bar(data, "2025-01-06 09:05", op=100, close=100, low=95, high=101)
    set_bar(data, "2025-01-06 09:10", op=103, high=104, low=100, close=101)
    result = simulate(prepare(data), data.index[0], data.index[-1] + BAR)
    assert len(result["trades"]) == 1
    trade = result["trades"].iloc[0]
    assert trade.side == 1 and trade.signal_i == returned and trade.entry_i == returned + 1
    assert trade.entry_price == 103 and not trade.entry_inside_range and trade.stop_price == 95  # return-bar wick included
    assert outside < returned


def test_short_equality_and_opposite_jump() -> None:
    data = ny_early_data()
    set_bar(data, "2025-01-06 09:00", close=102, high=104)
    set_bar(data, "2025-01-06 09:05", close=101, high=106)  # equality keeps short armed
    returned = set_bar(data, "2025-01-06 09:10", close=100, high=107)
    set_bar(data, "2025-01-06 09:15", op=99, high=100, low=98, close=99)
    result = simulate(prepare(data), data.index[0], data.index[-1] + BAR)
    assert result["trades"].iloc[0].stop_price == 107
    assert result["trades"].iloc[0].side == -1
    # A below-to-above jump replaces the old long instead of emitting it.
    data = ny_early_data()
    set_bar(data, "2025-01-06 09:00", close=98, low=95)
    set_bar(data, "2025-01-06 09:05", close=102, high=106)
    returned = set_bar(data, "2025-01-06 09:10", close=100, high=108)
    set_bar(data, "2025-01-06 09:15", op=99, close=99)
    events = simulate(prepare(data), data.index[0], data.index[-1] + BAR)["events"]
    assert len(events) == 1 and events.iloc[0].side == -1 and events.iloc[0].signal_i == returned


def test_multiple_excursions_and_occupied_filtering() -> None:
    data = ny_early_data()
    set_bar(data, "2025-01-06 09:00", close=98, low=97)
    set_bar(data, "2025-01-06 09:05", close=100)
    set_bar(data, "2025-01-06 09:10", op=100, high=101, low=99, close=100)
    set_bar(data, "2025-01-06 09:15", close=102, high=103)
    set_bar(data, "2025-01-06 09:20", close=100)
    result = simulate(prepare(data), data.index[0], data.index[-1] + BAR)
    assert result["events"].status.tolist() == ["filled", "skipped_occupied"]
    assert len(result["trades"]) == 1


def test_same_bar_exit_allows_next_bar_candidate_entry_and_day_reset() -> None:
    data = ny_early_data(periods=576)
    # First long is filled at 09:10 and its stop is touched at 09:20.
    set_bar(data, "2025-01-06 09:00", close=98, low=98)
    set_bar(data, "2025-01-06 09:05", close=100)
    set_bar(data, "2025-01-06 09:10", op=100, high=101, low=99, close=100)
    # The short's return close coincides with the long's intrabar stop, so it
    # may enter at 09:25.  It proves occupancy is compared with next entry.
    set_bar(data, "2025-01-06 09:15", close=102, high=103)
    set_bar(data, "2025-01-06 09:20", close=100, high=104, low=97)
    set_bar(data, "2025-01-06 09:25", op=100, high=101, low=99, close=100)
    result = simulate(prepare(data), data.index[0], data.index[-1] + BAR)
    assert result["events"].status.tolist()[:2] == ["filled", "filled"]
    assert result["trades"].entry_i.tolist()[:2] == [50, 53]
    # An unreturned end-of-day excursion cannot cross into the next NY day.
    set_bar(data, "2025-01-07 04:55", close=98, low=95)
    set_bar(data, "2025-01-07 09:00", close=100)
    next_day = simulate(prepare(data), data.index[0], data.index[-1] + BAR)
    assert (next_day["events"].signal_time.dt.date != pd.Timestamp("2025-01-07").date()).all()


def test_trace_gap_dual_touch_eod_and_censored_end() -> None:
    data = ny_early_data()
    ctx = prepare(data)
    entry = set_bar(data, "2025-01-06 09:00", op=100, high=111, low=89, close=100)
    ctx = prepare(data)
    dual = trace_trade(ctx, entry, 1, 95)
    assert dual["reason"] == "stop" and dual["dual_touch"] and dual["exit_time"] == data.index[entry] + BAR
    gap_data = ny_early_data()
    gap_entry = set_bar(gap_data, "2025-01-06 09:00", op=100, high=101, low=99, close=100)
    set_bar(gap_data, "2025-01-06 09:05", op=90, high=91, low=89, close=90)
    gap = trace_trade(prepare(gap_data), gap_entry, 1, 95)
    assert gap["reason"] == "stop_gap" and gap["exit_price"] == 90 and gap["exit_time"] == gap_data.index[gap_entry + 1]
    plain = frame("2025-01-06 05:00", 60)
    eod = trace_trade(prepare(ny_early_data()), 60, 1, 90)
    assert eod["reason"] == "daily_end" and not eod["censored"]
    partial = trace_trade(prepare(plain), 50, 1, 90)
    assert partial["reason"] == "data_end" and partial["censored"]


def test_dst_reference_counts_and_invalid_frames() -> None:
    spring = frame("2025-03-09 05:00", 276)
    fall = frame("2025-11-02 04:00", 300)
    assert prepare(spring)["daily_ranges"].iloc[0].reference_bars_expected == 36
    assert prepare(fall)["daily_ranges"].iloc[0].reference_bars_expected == 60
    broken = ny_early_data().drop(ny_early_data().index[5])
    with pytest.raises(ValueError, match="continuous"):
        prepare(broken)
    off_grid = ny_early_data()
    off_grid.index = off_grid.index + pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="grid boundary"):
        prepare(off_grid)
    invalid = ny_early_data()
    invalid.iloc[0, invalid.columns.get_loc("low")] = 102
    with pytest.raises(ValueError, match="invalid OHLC"):
        prepare(invalid)
    partial_reference = frame("2025-01-06 07:00", 264)
    partial_reference.loc[pd.Timestamp("2025-01-06 09:00", tz="UTC"), ["close", "low"]] = [90, 89]
    incomplete = prepare(partial_reference)
    assert not incomplete["range_complete"].any()
    empty = simulate(incomplete, partial_reference.index[0], partial_reference.index[-1] + BAR)
    assert empty["events"].empty and str(empty["events"].signal_time.dtype) == "datetime64[ns, UTC]"


def test_prepare_prefix_is_invariant_to_future_mutation() -> None:
    data = ny_early_data(periods=576)
    first = prepare(data)
    changed = data.copy()
    changed.iloc[300:, changed.columns.get_loc("high")] = 1000
    changed.iloc[300:, changed.columns.get_loc("close")] = 900
    second = prepare(changed)
    np.testing.assert_allclose(first["range_high"][:288], second["range_high"][:288], equal_nan=True)
    np.testing.assert_allclose(first["range_low"][:288], second["range_low"][:288], equal_nan=True)
    np.testing.assert_allclose(first["volatility"][:288], second["volatility"][:288], equal_nan=True)
    np.testing.assert_array_equal(first["vol_bucket"][:288], second["vol_bucket"][:288])

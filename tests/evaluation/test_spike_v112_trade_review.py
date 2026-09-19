"""Chronology tests: a stopped 5m candle's high is not known to precede its low."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_v112_trade_review import stop_bar_bounds, time_x


def bars(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                        index=pd.date_range("2025-01-01", periods=len(rows), freq="5min", tz="UTC"))


def test_touch_candle_high_is_only_an_upper_bound():
    raw = bars([[101, 105, 97, 99], [99, 112, 98, 110]])
    low, high, stamp, ambiguous = stop_bar_bounds(raw, 100, 2, 98, 0)
    assert (low, high, ambiguous) == (.5, 2.5, True)
    assert stamp == str(raw.index[0])


def test_high_before_touch_candle_is_definite():
    raw = bars([[101, 104, 99, 103], [103, 106, 97, 100]])
    low, high, _, ambiguous = stop_bar_bounds(raw, 100, 2, 98, .5)
    assert (low, high, ambiguous) == (2, 3, True)


def test_gap_at_open_excludes_all_later_highs():
    raw = bars([[97, 110, 96, 106]])
    low, high, _, ambiguous = stop_bar_bounds(raw, 100, 2, 98, 1)
    assert (low, high, ambiguous) == (1, 1, False)


def test_touch_candle_open_above_prior_high_is_definite():
    raw = bars([[104, 105, 97, 99]])
    low, high, _, ambiguous = stop_bar_bounds(raw, 100, 2, 98, 1)
    assert (low, high, ambiguous) == (2, 2.5, True)


def test_missing_stop_touch_is_rejected():
    with pytest.raises(AssertionError):
        stop_bar_bounds(bars([[101, 105, 99, 103]]), 100, 2, 98, 0)


def test_chart_anchor_before_loaded_window_is_not_clamped():
    index = pd.date_range("2025-01-02", periods=4, freq="1h", tz="UTC")
    assert time_x(index, "2025-01-01 22:00Z", 60) == -2
    assert time_x(index, "2025-01-02 01:30Z", 60) == 1.5

"""Regression coverage for the bounded clear-background morphology screen."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from yoyo.datasets.ma_morphology_background import (
    DEFAULT_THRESHOLD,
    EMA_SUPPORT_BARS,
    THRESHOLD_PROVENANCE,
    WIDEST_POST_BARS,
    WIDEST_PRE_BARS,
    screen_window,
)


CORE_START = 1211
CORE_END = 1214


def frame(*, rows: int = 1240, mode: str = "separated") -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    if mode == "separated":
        close = 100.0 + index * 1.0
    elif mode == "dense":
        close = 100.0 + 0.02 * np.sin(index / 3.0)
    else:
        raise ValueError(mode)
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=rows, freq="15min", tz="UTC"),
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
        }
    )


def screen(data: pd.DataFrame, **kwargs: object) -> dict:
    return screen_window(
        data,
        core_start_i=CORE_START,
        core_end_i=CORE_END,
        bar_minutes=15,
        **kwargs,
    )


def test_clear_separated_background_accepts_and_records_frozen_threshold() -> None:
    data = frame()
    result = screen(data)
    assert result["accepted"] is True
    assert result["reasons"] == []
    assert result["visible_start_i"] == CORE_START - WIDEST_PRE_BARS
    assert result["visible_end_i"] == CORE_END + WIDEST_POST_BARS
    assert result["metrics"]["threshold"] == DEFAULT_THRESHOLD == 1.6
    assert result["metrics"]["threshold_provenance"] == THRESHOLD_PROVENANCE
    assert result["metrics"]["ema_support_bars_before_visible_start"] == EMA_SUPPORT_BARS
    assert result["metrics"]["uses_profit_or_outcome"] is False
    assert result["metrics"]["uses_direction"] is False
    expected_close = data.open_time.iloc[CORE_END + WIDEST_POST_BARS] + pd.Timedelta(minutes=15)
    assert result["decision_close_utc"] == expected_close.isoformat()
    json.dumps(result, allow_nan=False)


def test_future_mutation_after_visible_right_edge_cannot_change_result() -> None:
    original = frame()
    changed = original.copy()
    changed.loc[CORE_END + WIDEST_POST_BARS + 1 :, ["open", "high", "low", "close"]] = [
        1_000_000.0,
        2_000_000.0,
        1.0,
        1_500_000.0,
    ]
    assert screen(changed) == screen(original)


def test_dense_background_is_review_only() -> None:
    result = screen(frame(mode="dense"))
    assert result["accepted"] is False
    assert "close_six_ma_spread_below_threshold" in result["reasons"]
    assert "hl2_six_ma_spread_below_threshold" in result["reasons"]


def test_full_visible_window_rejects_a_left_or_right_edge_below_threshold() -> None:
    result = screen(frame())
    # The returned audit coordinates establish that all visible positions,
    # including both geometry edges, are compared against one max ATR anchor.
    assert (
        result["metrics"]["close_spread_at_visible_start"]
        >= result["metrics"]["required_spread"]
    )
    assert result["metrics"]["close_spread_at_visible_end"] >= result["metrics"]["required_spread"]

    left_dense = frame()
    left_dense.loc[: CORE_START - WIDEST_PRE_BARS, "close"] = 100.0
    left_dense.loc[:, "open"] = left_dense["close"] - 0.1
    left_dense.loc[:, "high"] = left_dense["close"] + 0.5
    left_dense.loc[:, "low"] = left_dense["close"] - 0.5
    left = screen(left_dense)
    assert left["accepted"] is False
    assert left["visible_start_i"] in left["metrics"]["close_spread_failure_indices"]

    # A one-bar pre-window impulse makes earlier visible MA spreads exceed the
    # override, while the visible right edge alone falls below it.  This proves
    # post-core bars are screened instead of merely recorded as context.
    right_edge = frame()
    right_edge.loc[:, "close"] = 100.0
    right_edge.loc[CORE_START - WIDEST_PRE_BARS - 1, "close"] = 102.31012970008316
    right_edge.loc[
        CORE_START - WIDEST_PRE_BARS : CORE_END + WIDEST_POST_BARS, "close"
    ] = 100.0 + np.arange(20) * 0.01
    right_edge.loc[:, "open"] = right_edge["close"] - 0.1
    right_edge.loc[:, "high"] = right_edge["close"] + 0.5
    right_edge.loc[:, "low"] = right_edge["close"] - 0.5
    right = screen(right_edge, threshold=0.0835)
    assert right["accepted"] is False
    assert right["metrics"]["close_spread_failure_indices"] == [right["visible_end_i"]]
    assert right["metrics"]["hl2_spread_failure_indices"] == [right["visible_end_i"]]


def test_source_gaps_short_support_and_inactive_windows_fail_closed() -> None:
    gap = frame()
    gap.loc[1000:, "open_time"] = gap.loc[1000:, "open_time"] + pd.Timedelta(minutes=15)
    assert "source_gap_or_duplicate_in_support" in screen(gap)["reasons"]

    short = frame(rows=CORE_END + WIDEST_POST_BARS + 1)
    short_result = screen_window(
        short, core_start_i=1200, core_end_i=1203, bar_minutes=15
    )
    assert "insufficient_ema_support" in short_result["reasons"]

    inactive = frame()
    start = CORE_START - WIDEST_PRE_BARS
    stop = CORE_END + WIDEST_POST_BARS
    inactive.loc[start:stop, ["open", "high", "low", "close"]] = 100.0
    inactive_result = screen(inactive)
    assert inactive_result["accepted"] is False
    assert "insufficient_unique_close_activity" in inactive_result["reasons"]
    assert "insufficient_nonzero_range_activity" in inactive_result["reasons"]


def test_invalid_core_and_nondefault_threshold_are_explicit() -> None:
    invalid = screen(frame(), threshold=0.0)
    assert invalid["accepted"] is False
    assert invalid["reasons"] == ["invalid_threshold"]
    five_bar = screen_window(
        frame(), core_start_i=CORE_START, core_end_i=CORE_START + 4, bar_minutes=15, threshold=1.0
    )
    assert five_bar["metrics"]["threshold"] == 1.0
    assert five_bar["metrics"]["default_threshold"] == DEFAULT_THRESHOLD
    assert five_bar["metrics"]["threshold_provenance"] == "caller_supplied_override"
    assert five_bar["metrics"]["threshold_override"] is True

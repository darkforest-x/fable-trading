"""Focused geometry tests for the two-span launch-origin Review50 v2."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets.ma_launch_transition_box_review import (
    CONFIRMATION_BARS,
    CORE_LENGTHS,
    PAD_FRACTION,
    TransitionBoxReviewError,
    transition_box_for_span,
    transition_span,
)
from yoyo.layers.l1_detection.render import make_chart_transform


MA_COLUMNS = ("sma20", "sma60", "sma120", "ema20", "ema60", "ema120")


def frame() -> pd.DataFrame:
    close = np.linspace(100.0, 101.0, 20)
    out = pd.DataFrame(
        {
            "open": close - 0.05,
            "high": close + 0.35,
            "low": close - 0.30,
            "close": close,
        }
    )
    for offset, column in enumerate(MA_COLUMNS):
        out[column] = close - 0.15 + offset * 0.025
    return out


def test_span_uses_core_t_minus_3_and_extends_through_t() -> None:
    anchor_local = 17
    for core_len in CORE_LENGTHS:
        span = transition_span(anchor_local, core_len, 20)
        assert span.core_end_local == anchor_local - 3
        assert span.confirmation_end_local == anchor_local
        assert span.total_box_bars == core_len + CONFIRMATION_BARS
        assert span.total_box_bars in {7, 8, 9, 10}


def test_vertical_bounds_ignore_confirmation_values_with_fixed_transform() -> None:
    original = frame()
    span = transition_span(17, 5, len(original))
    transform = make_chart_transform(original)
    first = transition_box_for_span(transform, original, span)
    mutated = original.copy()
    mutated.loc[span.core_end_local + 1 : span.confirmation_end_local, ["high", "low", *MA_COLUMNS]] *= 50.0
    second = transition_box_for_span(transform, mutated, span)
    for key in ("y0", "y1", "core_price_high_raw", "core_price_low_raw", "box_price_high", "box_price_low"):
        assert first[key] == second[key]
    assert first["confirmation_values_used_for_vertical_bounds"] is False
    assert second["confirmation_values_used_for_vertical_bounds"] is False


def test_vertical_bounds_respond_to_core_wick_and_contain_core_mas() -> None:
    original = frame()
    span = transition_span(17, 5, len(original))
    transform = make_chart_transform(original)
    first = transition_box_for_span(transform, original, span)
    mutated = original.copy()
    mutated.loc[span.core_start_local, "high"] += 1.5
    second = transition_box_for_span(transform, mutated, span)
    assert second["core_price_high_raw"] > first["core_price_high_raw"]
    assert second["box_price_high"] > first["box_price_high"]
    assert second["contains_core_wicks_and_six_mas"] is True
    assert second["pad_fraction"] == PAD_FRACTION


def test_confirmation_is_horizontal_context_not_vertical_union() -> None:
    data = frame()
    span = transition_span(17, 4, len(data))
    data.loc[span.confirmation_end_local, "high"] = 150.0
    box = transition_box_for_span(make_chart_transform(data), data, span)
    assert box["confirmation_extremes_outside_vertical_zone"] >= 1
    assert box["box_price_high"] < 150.0


def test_extreme_core_wick_fails_closed() -> None:
    data = frame()
    span = transition_span(17, 5, len(data))
    data.loc[span.core_start_local, "high"] += 100.0
    with pytest.raises(TransitionBoxReviewError, match="mark IGNORE"):
        transition_box_for_span(make_chart_transform(data), data, span)


def test_invalid_core_length_is_rejected() -> None:
    with pytest.raises(TransitionBoxReviewError, match="unsupported"):
        transition_span(17, 8, 20)

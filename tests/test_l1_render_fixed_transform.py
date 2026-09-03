"""Regression tests for paired MA-source rendering with a frozen chart axis."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l1_detection.data import MA_PERIODS
from yoyo.layers.l1_detection.render import make_chart_transform, render_chart


def _frame(rows: int = 18) -> pd.DataFrame:
    close = pd.Series(np.linspace(100.0, 101.7, rows))
    frame = pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.4,
            "low": close - 0.5,
            "close": close,
        }
    )
    for period in MA_PERIODS:
        frame[f"sma{period}"] = close - period / 1000.0
        frame[f"ema{period}"] = close + period / 1000.0
    return frame


def test_explicit_fixed_transform_is_byte_identical_to_default() -> None:
    frame = _frame()
    expected, transform = render_chart(frame)
    actual, returned = render_chart(frame, fixed_transform=transform)

    assert returned is transform
    assert np.array_equal(actual, expected)


def test_fixed_transform_rejects_canvas_or_bar_count_drift() -> None:
    frame = _frame()
    transform = make_chart_transform(frame)

    with pytest.raises(ValueError, match="fixed transform"):
        render_chart(frame.iloc[:-1], fixed_transform=transform)
    with pytest.raises(ValueError, match="fixed transform"):
        render_chart(frame, width=640, fixed_transform=transform)

"""Causality and column-isolation tests for the HL2 MA-source treatment."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal, assert_series_equal

from yoyo.datasets.ma_launch_owner_grade_a_hl2 import with_hl2_mas
from yoyo.datasets.ma_rope_filter import add_six_mas
from yoyo.layers.l1_detection.data import ALL_MA_COLS, MA_PERIODS


def _ohlc(rows: int = 140) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    close = 100.0 + index * 0.03 + np.sin(index / 5.0) * 0.4
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2025-01-01", periods=rows, freq="15min", tz="UTC"),
            "open": close - 0.1,
            "high": close + 0.5 + (index % 3) * 0.02,
            "low": close - 0.3 - (index % 5) * 0.01,
            "close": close,
            "volume": 1000.0 + index,
        }
    )


def test_hl2_treatment_changes_only_the_six_ma_columns() -> None:
    baseline = add_six_mas(_ohlc())
    treatment = with_hl2_mas(baseline)
    non_ma = [column for column in baseline if column not in ALL_MA_COLS]

    assert_frame_equal(treatment[non_ma], baseline[non_ma])
    hl2 = (baseline["high"] + baseline["low"]) / 2.0
    for period in MA_PERIODS:
        assert_series_equal(
            treatment[f"sma{period}"],
            hl2.rolling(period).mean().rename(f"sma{period}"),
        )
        assert_series_equal(
            treatment[f"ema{period}"],
            hl2.ewm(span=period, adjust=False).mean().rename(f"ema{period}"),
        )


def test_future_ohlc_mutation_cannot_change_prior_hl2_mas() -> None:
    source = _ohlc()
    mutated = source.copy()
    cut = 125
    mutated.loc[cut:, "high"] += 100.0
    mutated.loc[cut:, "low"] -= 100.0

    original = with_hl2_mas(add_six_mas(source))
    changed = with_hl2_mas(add_six_mas(mutated))
    assert_frame_equal(
        original.loc[: cut - 1, list(ALL_MA_COLS)],
        changed.loc[: cut - 1, list(ALL_MA_COLS)],
    )

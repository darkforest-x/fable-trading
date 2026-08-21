"""Causality and side-semantics checks for Pine EMA cross-count factors."""
from __future__ import annotations

import pandas as pd

from yoyo.layers.l2_judgment.pine_cross_features import (
    EMA_COLUMNS,
    SIX_MA_COLUMNS,
    SIX_MA_DIRECTIONAL_PAIRS,
    add_cross_count_features,
    add_six_ma_cross_count_features,
    side_aligned_cross_frame,
    side_aligned_six_ma_cross_frame,
)


def test_adjacent_cross_counts_breadth_and_side_alignment() -> None:
    rows = [
        [1, 2, 3, 4, 5, 6, 7],
        [7, 6, 5, 4, 3, 2, 1],
        [1, 2, 3, 4, 5, 6, 7],
    ]
    frame = pd.DataFrame(rows, columns=EMA_COLUMNS)
    featured = add_cross_count_features(frame, windows=(2,))
    assert featured.loc[1, "ema_cross_up_count_2"] == 6
    assert featured.loc[1, "ema_cross_up_breadth_2"] == 6
    assert featured.loc[1, "ema_up_alignment"] == 6
    assert featured.loc[2, "ema_cross_churn_2"] == 12

    long = side_aligned_cross_frame(featured, window=2, side="long")
    short = side_aligned_cross_frame(featured, window=2, side="short")
    assert long.loc[1, "directional_cross_count"] == 6
    assert long.loc[1, "opposite_cross_count"] == 0
    assert short.loc[1, "directional_cross_count"] == 0
    assert short.loc[1, "opposite_cross_count"] == 6
    assert long.loc[2, "cross_imbalance"] == 0


def test_future_ema_changes_do_not_change_past_cross_features() -> None:
    base = pd.DataFrame(
        {column: [float(index + offset) for index in range(8)] for offset, column in enumerate(EMA_COLUMNS)}
    )
    original = add_cross_count_features(base, windows=(4,))
    perturbed = base.copy()
    perturbed.loc[5:, list(EMA_COLUMNS)] *= -10.0
    changed = add_cross_count_features(perturbed, windows=(4,))
    columns = [column for column in original if column.startswith("ema_")]
    pd.testing.assert_frame_equal(original.loc[:4, columns], changed.loc[:4, columns])


def test_true_six_ma_bundle_counts_twelve_directional_pairs() -> None:
    assert len(SIX_MA_DIRECTIONAL_PAIRS) == 12
    frame = pd.DataFrame(
        [
            [1.0, 1.1, 2.0, 2.1, 3.0, 3.1],
            [6.0, 6.1, 4.0, 4.1, 2.0, 2.1],
            [1.0, 1.1, 2.0, 2.1, 3.0, 3.1],
        ],
        columns=SIX_MA_COLUMNS,
    )
    frame["close"] = 10.0
    featured = add_six_ma_cross_count_features(frame, windows=(2,))
    assert featured.loc[1, "six_ma_cross_up_count_2"] == 12
    assert featured.loc[1, "six_ma_cross_up_breadth_2"] == 12
    assert featured.loc[1, "six_ma_up_alignment"] == 12
    assert featured.loc[2, "six_ma_cross_churn_2"] == 24

    long = side_aligned_six_ma_cross_frame(featured, window=2, side="long")
    short = side_aligned_six_ma_cross_frame(featured, window=2, side="short")
    assert long.loc[1, "directional_cross_count"] == 12
    assert short.loc[1, "opposite_cross_count"] == 12
    assert long.loc[2, "cross_imbalance"] == 0


def test_future_six_ma_changes_do_not_change_past_cross_features() -> None:
    base = pd.DataFrame(
        {
            column: [float(index + offset + 1) for index in range(8)]
            for offset, column in enumerate(SIX_MA_COLUMNS)
        }
    )
    base["close"] = 100.0
    original = add_six_ma_cross_count_features(base, windows=(4,))
    perturbed = base.copy()
    perturbed.loc[5:, list(SIX_MA_COLUMNS)] *= -10.0
    changed = add_six_ma_cross_count_features(perturbed, windows=(4,))
    columns = [column for column in original if column.startswith("six_ma_")]
    pd.testing.assert_frame_equal(original.loc[:4, columns], changed.loc[:4, columns])

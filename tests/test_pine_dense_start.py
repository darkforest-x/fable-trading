"""Causality and gate-semantics tests for the ETH 15m dense-start factor."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l2_judgment.pine_cross_features import SIX_MA_COLUMNS
from yoyo.layers.l2_judgment.pine_dense_start import (
    DenseStartProfile,
    add_six_ma_dense_start_features,
    dense_start_gate_mask,
)


def _profile() -> DenseStartProfile:
    return DenseStartProfile(
        profile_id="test",
        min_pre_pairwise_crosses=2,
        max_pre_bandwidth_atr_mean=2.5,
        min_current_alignment=8,
        min_pre_cross_imbalance=0,
        min_slope_coherence=5.0 / 6.0,
        min_atr_release_ratio=1.05,
    )


def _frame(rows: int = 40) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    data: dict[str, np.ndarray] = {}
    # Oscillating offsets create actual order flips without relying on future
    # rows.  Values need not be literal rolling MAs for this feature unit test.
    for offset, column in enumerate(SIX_MA_COLUMNS):
        data[column] = 100.0 + index * 0.05 + np.sin(index + offset) * (0.4 + offset * 0.03)
    result = pd.DataFrame(data)
    result["close"] = 101.0 + index * 0.06
    result["atr"] = 1.0 + index * 0.002
    return result


def test_formation_features_exclude_the_release_bar() -> None:
    decision = 25
    base = _frame()
    original = add_six_ma_dense_start_features(base)
    perturbed = base.copy()
    # Force a radically different release bar.  Formation values at t must
    # remain identical because their right edge is t-1.
    perturbed.loc[decision, list(SIX_MA_COLUMNS)] = [80, 85, 90, 110, 115, 120]
    perturbed.loc[decision, "close"] = 130.0
    changed = add_six_ma_dense_start_features(perturbed)
    formation = [
        "dense_pre_pairwise_cross_count_12",
        "dense_pre_pairwise_cross_breadth_12",
        "dense_pre_bandwidth_atr_mean_12",
        "dense_pre_bandwidth_atr_max_12",
        "dense_pre_cross_up_count_12",
        "dense_pre_cross_down_count_12",
        "dense_pre_cross_imbalance_long_12",
        "dense_pre_cross_imbalance_short_12",
    ]
    pd.testing.assert_series_equal(
        original.loc[decision, formation],
        changed.loc[decision, formation],
        check_names=False,
    )
    assert original.loc[decision, "dense_breakout_distance_atr_long"] != changed.loc[
        decision, "dense_breakout_distance_atr_long"
    ]


def test_future_rows_do_not_change_any_past_dense_feature() -> None:
    decision = 25
    base = _frame()
    original = add_six_ma_dense_start_features(base)
    perturbed = base.copy()
    perturbed.loc[decision + 1 :, list(SIX_MA_COLUMNS)] *= -20.0
    perturbed.loc[decision + 1 :, ["close", "atr"]] *= 3.0
    changed = add_six_ma_dense_start_features(perturbed)
    columns = [column for column in original if column.startswith("dense_")]
    pd.testing.assert_frame_equal(
        original.loc[:decision, columns],
        changed.loc[:decision, columns],
    )


def test_composite_gate_requires_density_compression_direction_and_release() -> None:
    row = pd.DataFrame(
        {
            "dense_start_ready": [True],
            "dense_pre_pairwise_cross_count_12": [3.0],
            "dense_pre_bandwidth_atr_mean_12": [2.0],
            "dense_current_alignment_long": [9.0],
            "dense_pre_cross_imbalance_long_12": [1.0],
            "dense_breakout_distance_atr_long": [0.2],
            "dense_signed_mean_slope_atr_long_3": [0.05],
            "dense_slope_coherence_long_3": [1.0],
            "dense_atr_release_ratio_8": [1.0],
        }
    )
    assert bool(dense_start_gate_mask(row, _profile(), side="long").iloc[0])
    for column, failing_value in (
        ("dense_pre_pairwise_cross_count_12", 1.0),
        ("dense_pre_bandwidth_atr_mean_12", 2.6),
        ("dense_current_alignment_long", 7.0),
        ("dense_pre_cross_imbalance_long_12", -1.0),
        ("dense_breakout_distance_atr_long", 0.0),
        ("dense_signed_mean_slope_atr_long_3", 0.0),
    ):
        changed = row.copy()
        changed.loc[0, column] = failing_value
        assert not bool(dense_start_gate_mask(changed, _profile(), side="long").iloc[0])


def test_profile_validation_fails_closed() -> None:
    with pytest.raises(ValueError, match="pairwise"):
        DenseStartProfile(
            profile_id="bad",
            min_pre_pairwise_crosses=-1,
            max_pre_bandwidth_atr_mean=2.0,
            min_current_alignment=8,
            min_pre_cross_imbalance=0,
            min_slope_coherence=0.8,
            min_atr_release_ratio=1.0,
        )
    with pytest.raises(ValueError, match="alignment"):
        DenseStartProfile(
            profile_id="bad",
            min_pre_pairwise_crosses=1,
            max_pre_bandwidth_atr_mean=2.0,
            min_current_alignment=13,
            min_pre_cross_imbalance=0,
            min_slope_coherence=0.8,
            min_atr_release_ratio=1.0,
        )

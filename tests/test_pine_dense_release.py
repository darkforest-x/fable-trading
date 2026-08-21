"""Causality and semantics tests for the V14 literal release correction."""
from __future__ import annotations

import pandas as pd

from yoyo.layers.l2_judgment.pine_dense_release import (
    add_dense_release_v2_features,
    dense_release_v2_gate_mask,
)
from yoyo.layers.l2_judgment.pine_dense_start import DenseStartProfile


def _profile() -> DenseStartProfile:
    return DenseStartProfile(
        profile_id="dense_l1",
        min_pre_pairwise_crosses=2,
        max_pre_bandwidth_atr_mean=3.0,
        min_current_alignment=6,
        min_pre_cross_imbalance=-1,
        min_slope_coherence=2.0 / 3.0,
        min_atr_release_ratio=1.0,
    )


def _base_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "high": [101.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.0, 102.0, 103.0],
            "atr": [2.0, 2.1, 2.2],
            "dense_rope_upper": [100.5, 101.0, 102.0],
            "dense_rope_lower": [99.5, 100.0, 101.0],
            "dense_breakout_distance_atr_long": [-0.25, 1.0 / 2.1, 1.0 / 2.2],
            "dense_breakout_distance_atr_short": [-0.25, -2.0 / 2.1, -2.0 / 2.2],
            "dense_start_score_long": [0.5, 0.7, 0.8],
            "dense_start_score_short": [0.5, 0.4, 0.3],
        }
    )


def test_release_features_use_current_true_range_and_prior_atr() -> None:
    featured = add_dense_release_v2_features(_base_frame())
    # Bar 1 TR=max(3, |3|, |0|)=3 and prior ATR is 2.
    assert featured.loc[1, "dense_release_true_range"] == 3.0
    assert featured.loc[1, "dense_release_true_range_atr_ratio"] == 1.5
    # Prior long distance at bar 1: (close[0]-upper[0])/atr[0] = -0.25.
    assert featured.loc[1, "dense_release_prior_distance_atr_long"] == -0.25
    assert featured.loc[1, "dense_release_breakout_expansion_atr_long"] > 0.0


def test_future_change_does_not_change_release_features_at_decision() -> None:
    original = add_dense_release_v2_features(_base_frame())
    changed_source = _base_frame()
    changed_source.loc[2, ["high", "low", "close", "atr"]] = [200.0, 50.0, 150.0, 20.0]
    changed = add_dense_release_v2_features(changed_source)
    columns = [column for column in original if column.startswith("dense_release_")]
    pd.testing.assert_series_equal(original.loc[1, columns], changed.loc[1, columns])


def test_release_gate_requires_true_range_and_distance_expansion() -> None:
    row = pd.DataFrame(
        {
            "dense_start_ready": [True],
            "dense_pre_pairwise_cross_count_12": [3.0],
            "dense_pre_bandwidth_atr_mean_12": [2.0],
            "dense_current_alignment_long": [8.0],
            "dense_pre_cross_imbalance_long_12": [0.0],
            "dense_breakout_distance_atr_long": [0.5],
            "dense_signed_mean_slope_atr_long_3": [0.05],
            "dense_slope_coherence_long_3": [5.0 / 6.0],
            "dense_atr_release_ratio_8": [1.0],
            "dense_release_v2_ready": [True],
            "dense_release_true_range_atr_ratio": [1.2],
            "dense_release_breakout_expansion_atr_long": [0.1],
        }
    )
    assert bool(dense_release_v2_gate_mask(row, _profile(), side="long").iloc[0])
    weak_range = row.copy()
    weak_range.loc[0, "dense_release_true_range_atr_ratio"] = 0.99
    assert not bool(dense_release_v2_gate_mask(weak_range, _profile(), side="long").iloc[0])
    shrinking = row.copy()
    shrinking.loc[0, "dense_release_breakout_expansion_atr_long"] = 0.0
    assert not bool(dense_release_v2_gate_mask(shrinking, _profile(), side="long").iloc[0])

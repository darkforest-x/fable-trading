"""Unit tests for the read-only Local Signal V2 boundary diagnosis."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.diagnose_local_signal_v2_semantic_boundary import (
    add_bh_qvalues,
    cliffs_delta,
    extract_feature_row,
    load_snapshot_prefix,
    wilson95,
)
from yoyo.layers.l1_detection.data import ALL_MA_COLS


def test_cliffs_delta_has_an_explicit_direction() -> None:
    assert cliffs_delta(np.array([3.0, 4.0]), np.array([1.0, 2.0])) == 1.0
    assert cliffs_delta(np.array([1.0, 2.0]), np.array([3.0, 4.0])) == -1.0
    assert cliffs_delta(np.array([1.0]), np.array([1.0])) == 0.0


def test_bh_qvalues_are_monotone_and_bounded() -> None:
    effects = {
        "a": {"median_permutation_p": 0.001},
        "b": {"median_permutation_p": 0.02},
        "c": {"median_permutation_p": 0.5},
    }
    add_bh_qvalues(effects)
    assert 0 <= effects["a"]["bh_q"] <= effects["b"]["bh_q"] <= effects["c"]["bh_q"] <= 1


def test_extract_feature_row_uses_only_declared_causal_bounds() -> None:
    frame = pd.DataFrame(
        {
            "open": [100.0, 100.1, 100.2, 100.0, 99.8, 99.5],
            "high": [100.3, 100.4, 100.5, 100.3, 100.0, 99.8],
            "low": [99.8, 99.9, 100.0, 99.7, 99.4, 99.1],
            "close": [100.1, 100.2, 100.0, 99.8, 99.5, 99.2],
        }
    )
    for offset, column in enumerate(ALL_MA_COLS):
        frame[column] = np.linspace(100.15 + offset * 0.01, 99.55 + offset * 0.01, len(frame))
    review = {
        "review_id": "C001",
        "event_id": "event",
        "symbol": "ETH_USDT_SWAP",
        "source_type": "canary_candidate",
        "source_model": "R2",
        "canary_cohort": "common_retained",
        "owner_verdict": "YES",
        "decision_time": "2026-05-03T00:00:00+00:00",
        "window_start_bar": 10,
        "window_length": 6,
        "box_start_bar": 12,
        "box_end_bar": 13,
        "model_confidence": 0.5,
    }
    original = {
        "x1n": 0.3,
        "x2n": 0.7,
        "y1n": 0.4,
        "y2n": 0.6,
        "raw_detection_count": 4,
        "window_lengths_seen": [12, 13],
    }

    result = extract_feature_row(review, original, frame, paired_r1=None)

    assert result["decision_delay_bars"] == 2
    assert result["core_bars"] == 2
    assert result["pre_bars"] == 2
    assert result["core_center_pct"] == 50.0
    assert result["raw_detection_count"] == 4
    assert result["windows_seen_count"] == 2
    assert result["renderer_floor_active"] is True
    assert result["post_core_return_bps"] < 0


def test_wilson_interval_contains_observed_rate() -> None:
    lower, upper = wilson95(11, 89)
    assert lower < 0.11 < upper


def test_snapshot_prefix_does_not_materialize_later_rows(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.csv"
    pd.DataFrame(
        {
            "open_time": [
                "2026-05-03T00:00:00+00:00",
                "2026-05-03T00:15:00+00:00",
                "not-a-time-after-decision",
            ],
            "open": [100.0, 101.0, "poison"],
            "high": [101.0, 102.0, "poison"],
            "low": [99.0, 100.0, "poison"],
            "close": [100.5, 101.5, "poison"],
            "volume": [1.0, 2.0, "poison"],
        }
    ).to_csv(path, index=False)

    frame, audit = load_snapshot_prefix(path, required_end=1)

    assert len(frame) == 2
    assert audit["rows_materialized"] == 2
    assert audit["holdout_rows_materialized"] == 0

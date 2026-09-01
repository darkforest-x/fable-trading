"""Unit contracts for the causal completed-history semantic proposal gate."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l1_detection.data import ALL_MA_COLS
from yoyo.layers.l1_detection.semantic_gate import (
    SemanticGateError,
    compute_causal_core_semantics,
    evaluate_causal_semantic_gate,
)
from scripts.evaluate_15m_ma_launch_owner_yolo_semantic_gate import (
    DEFAULT_PREREG,
    EXPERIMENT_ID,
    map_prediction_to_core,
    paired_binary_summary,
    verify_preregistration,
    x_only_transform,
)


ROOT = Path(__file__).resolve().parents[1]
AUTOFILL_PREREG = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json"
)


def _gates() -> dict[str, float]:
    return json.loads(AUTOFILL_PREREG.read_text(encoding="utf-8"))["morphology_gate"]


def _long_frame(*, far_from_mas: bool = False, sparse_mas: bool = False) -> pd.DataFrame:
    rows = 10
    ma_center = 100.0 + np.arange(rows) * 0.05
    offsets = np.linspace(-0.10, 0.10, len(ALL_MA_COLS))
    if sparse_mas:
        offsets = np.linspace(-1.50, 1.50, len(ALL_MA_COLS))
    close = ma_center.copy() + (4.0 if far_from_mas else 0.02)
    close[5] = close[4] + 0.20
    close[6] = close[4] + 1.10
    close[7] = close[4] + 1.40
    close[9] = close[4] + 1.90
    frame = pd.DataFrame(
        {
            "open": close - 0.05,
            "high": close + 0.12,
            "low": close - 0.12,
            "close": close,
            "atr": np.ones(rows),
        }
    )
    for column, offset in zip(ALL_MA_COLS, offsets):
        frame[column] = ma_center + offset
    return frame


def test_dense_near_ma_post2_prefix_passes_without_future_reads() -> None:
    frame = _long_frame()
    frame["close"] = frame["close"].astype(object)
    frame.loc[7:, "close"] = "future-not-readable"
    features = compute_causal_core_semantics(
        frame,
        core_start_i=0,
        core_end_i=4,
        observed_end_i=6,
        direction="LONG",
    )
    result = evaluate_causal_semantic_gate(features, _gates())
    assert result.passed is True
    assert "post3" not in result.checks
    assert "post5" not in result.checks
    assert features.post3_progress_atr is None
    assert features.post5_progress_atr is None


def test_sparse_six_ma_bundle_is_rejected() -> None:
    features = compute_causal_core_semantics(
        _long_frame(sparse_mas=True),
        core_start_i=0,
        core_end_i=4,
        observed_end_i=6,
        direction="LONG",
    )
    result = evaluate_causal_semantic_gate(features, _gates())
    assert result.passed is False
    assert {"ma_envelope", "ma_spread_end"}.issubset(result.failed_checks)


def test_candles_far_from_ma_bundle_are_rejected() -> None:
    features = compute_causal_core_semantics(
        _long_frame(far_from_mas=True),
        core_start_i=0,
        core_end_i=4,
        observed_end_i=6,
        direction="LONG",
    )
    result = evaluate_causal_semantic_gate(features, _gates())
    assert result.passed is False
    assert {
        "minimum_close_to_ma",
        "close_to_ma_envelope",
        "body_to_ma_envelope",
    }.issubset(result.failed_checks)


def test_visible_post3_and_post5_are_enforced_conditionally() -> None:
    frame = _long_frame()
    passing = compute_causal_core_semantics(
        frame,
        core_start_i=0,
        core_end_i=4,
        observed_end_i=9,
        direction="LONG",
    )
    assert evaluate_causal_semantic_gate(passing, _gates()).passed is True

    frame.loc[9, "close"] = frame.loc[4, "close"] + 0.2
    failing = compute_causal_core_semantics(
        frame,
        core_start_i=0,
        core_end_i=4,
        observed_end_i=9,
        direction="LONG",
    )
    assert "post5" in evaluate_causal_semantic_gate(failing, _gates()).failed_checks


def test_direction_is_part_of_semantics_not_a_geometry_alias() -> None:
    frame = _long_frame()
    long_features = compute_causal_core_semantics(
        frame,
        core_start_i=0,
        core_end_i=4,
        observed_end_i=6,
        direction="LONG",
    )
    short_features = compute_causal_core_semantics(
        frame,
        core_start_i=0,
        core_end_i=4,
        observed_end_i=6,
        direction="SHORT",
    )
    assert evaluate_causal_semantic_gate(long_features, _gates()).passed is True
    assert {
        "post1",
        "post2",
        "ma_slope",
    }.issubset(evaluate_causal_semantic_gate(short_features, _gates()).failed_checks)


def test_gate_rejects_a_core_without_two_visible_confirmation_bars() -> None:
    with pytest.raises(SemanticGateError, match="at least two confirmation"):
        compute_causal_core_semantics(
            _long_frame(),
            core_start_i=0,
            core_end_i=4,
            observed_end_i=5,
            direction="LONG",
        )


def test_box_mapping_reproduces_four_or_five_core_and_confirmation_contract() -> None:
    transform = x_only_transform(19)
    prediction = {
        "xyxy_norm": [
            transform.x_at(5) / transform.width,
            0.2,
            transform.x_at(9) / transform.width,
            0.4,
        ]
    }
    mapped = map_prediction_to_core(
        prediction,
        {"window_bars": 19, "window_start_i": 100, "window_end_i": 118},
    )
    assert mapped["structural_pass"] is True
    assert mapped["core_start_i"] == 105
    assert mapped["core_end_i"] == 109
    assert mapped["core_length_bars"] == 5
    assert mapped["confirmation_bars"] == 9


def test_paired_summary_keeps_subset_recall_cost_visible() -> None:
    summary = paired_binary_summary(
        [True, True, False, True],
        [True, False, False, False],
        left_name="control",
        right_name="gate",
    )
    assert summary["control_positive"] == 3
    assert summary["gate_positive"] == 1
    assert summary["left_only"] == 2
    assert summary["right_only"] == 0


def test_preregistration_copies_thresholds_without_authorizing_holdout() -> None:
    prereg, gates = verify_preregistration(DEFAULT_PREREG)
    assert prereg["experiment_id"] == EXPERIMENT_ID
    assert gates == _gates()
    assert prereg["treatment"]["raw_box_vertical_coverage_is_gate"] is False
    assert prereg["safety"]["holdout_read"] is False
    assert prereg["safety"]["new_training"] is False
    assert prereg["safety"]["production_eligible"] is False

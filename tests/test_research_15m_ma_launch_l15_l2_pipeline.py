"""Contract tests for the causal L1.5 + side-split L2 experiment."""

from __future__ import annotations

import numpy as np

from scripts.research_15m_ma_launch_l15_l2_pipeline import (
    EXPERIMENT_ID,
    GLOBAL_SHAPE_FEATURE_COLUMNS,
    L2_REDUCED_FEATURES,
    choose_strict_threshold,
    load_preregistration,
)


def test_preregistration_freezes_safety_and_feature_contracts() -> None:
    prereg = load_preregistration()
    assert prereg["experiment_id"] == EXPERIMENT_ID
    assert prereg["owner_authorization"]["p0_p1_training_gate_override_for_this_research_run"] is True
    assert prereg["owner_authorization"]["holdout_read_authorized"] is False
    assert prereg["safety"]["holdout_read"] is False
    assert prereg["safety"]["promote"] is False
    assert prereg["safety"]["deploy"] is False
    assert tuple(prereg["l15"]["feature_columns"]) == GLOBAL_SHAPE_FEATURE_COLUMNS
    assert tuple(prereg["l2"]["feature_columns"]) == L2_REDUCED_FEATURES
    assert prereg["outcome"]["changed"] is False


def test_strict_threshold_respects_fpr_cap_and_is_deterministic() -> None:
    labels = np.array([1] * 30 + [0] * 60)
    scores = np.concatenate(
        [np.linspace(0.95, 0.50, 30), np.linspace(0.70, 0.05, 60)]
    )
    first = choose_strict_threshold(
        labels,
        scores,
        max_false_positive_rate=0.10,
        minimum_true_positives=20,
    )
    second = choose_strict_threshold(
        labels,
        scores,
        max_false_positive_rate=0.10,
        minimum_true_positives=20,
    )
    assert first == second
    assert first["false_positive_rate"] <= 0.10
    assert first["tp"] >= 20


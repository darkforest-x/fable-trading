from __future__ import annotations

import pandas as pd
import pytest

from src.detection.evaluate_direction_classifier import (
    DirectionEvaluationError,
    candidate_side_predictions,
    classification_metrics,
    ordered_model_names,
    path_batches,
    profit_gate_result,
)


def test_candidate_side_predictions_are_deterministic_for_overlap() -> None:
    manifest = pd.DataFrame(
        {
            "long_candidate": [True, False, True, False],
            "short_candidate": [False, True, True, False],
        }
    )

    assert candidate_side_predictions(manifest) == ["long", "short", "no_trade", "no_trade"]


def test_classification_metrics_reconcile_confusion_and_balanced_accuracy() -> None:
    result = classification_metrics(
        ["long", "long", "short", "short", "no_trade", "no_trade"],
        ["long", "short", "short", "no_trade", "no_trade", "no_trade"],
    )

    assert result.accuracy == pytest.approx(4 / 6)
    assert result.balanced_accuracy == pytest.approx((0.5 + 0.5 + 1.0) / 3)
    assert result.confusion == ((1, 1, 0), (0, 1, 1), (0, 0, 2))
    assert sum(item.support for item in result.per_class) == 6


def test_ordered_model_names_requires_exact_canonical_classes() -> None:
    assert ordered_model_names({0: "long", 1: "no_trade", 2: "short"}) == (
        "long",
        "no_trade",
        "short",
    )

    with pytest.raises(DirectionEvaluationError, match="classes"):
        ordered_model_names({0: "up", 1: "flat", 2: "down"})


def test_path_batches_bounds_inference_memory_without_reordering() -> None:
    paths = [f"image-{index}.png" for index in range(5)]

    assert path_batches(paths, batch_size=2) == [
        ["image-0.png", "image-1.png"],
        ["image-2.png", "image-3.png"],
        ["image-4.png"],
    ]


def test_profit_gate_reports_requirements_and_observations_separately() -> None:
    result = profit_gate_result(
        round_trip_cost=0.002,
        net_mean_per_trade=-0.0015,
        profit_factor=0.75,
        n_trades=4_850,
    )

    assert result.requires_net_mean_per_trade_gt == 0.0
    assert result.requires_profit_factor_gte == 1.3
    assert result.requires_n_trades_gte == 100
    assert result.observed_net_mean_per_trade == -0.0015
    assert result.observed_profit_factor == 0.75
    assert result.observed_n_trades == 4_850
    assert result.passed is False

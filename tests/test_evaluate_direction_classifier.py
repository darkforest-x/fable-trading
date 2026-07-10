from __future__ import annotations

import pandas as pd
import pytest

from src.detection.evaluate_direction_classifier import (
    DirectionEvaluationError,
    candidate_side_predictions,
    classification_metrics,
    ordered_model_names,
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

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.judgment.direction_economics import (
    DirectionPredictionError,
    evaluate_direction_predictions,
    train_numeric_direction_baseline,
)
from src.judgment.features import FEATURE_COLUMNS


def test_evaluate_direction_predictions_uses_selected_side_and_excludes_no_trade() -> None:
    manifest = pd.DataFrame(
        {
            "long_realized_ret": [0.01, -0.03, 0.50, -0.01],
            "short_realized_ret": [-0.02, 0.02, 0.50, 0.03],
        }
    )

    result = evaluate_direction_predictions(
        manifest,
        ["long", "short", "no_trade", "long"],
        costs=(0.002,),
    )

    assert result.n_candidates == 4
    assert result.n_trades == 3
    assert result.trade_coverage == pytest.approx(0.75)
    assert result.gross_mean_per_trade == pytest.approx(0.0066666667)
    assert result.gross_win_rate == pytest.approx(2 / 3)
    assert len(result.cost_metrics) == 1
    cost = result.cost_metrics[0]
    assert cost.round_trip_cost == 0.002
    assert cost.net_mean_per_trade == pytest.approx(0.0046666667)
    assert cost.profit_factor == pytest.approx(0.026 / 0.012)
    assert cost.max_drawdown_pct == pytest.approx(0.012)


def test_evaluate_direction_predictions_handles_all_no_trade() -> None:
    manifest = pd.DataFrame(
        {
            "long_realized_ret": [0.01, -0.01],
            "short_realized_ret": [-0.01, 0.01],
        }
    )

    result = evaluate_direction_predictions(manifest, ["no_trade", "no_trade"])

    assert result.n_trades == 0
    assert result.trade_coverage == 0.0
    assert result.gross_mean_per_trade is None
    assert all(metric.profit_factor is None for metric in result.cost_metrics)


def test_evaluate_direction_predictions_rejects_invalid_or_nonfinite_inputs() -> None:
    manifest = pd.DataFrame(
        {
            "long_realized_ret": [np.nan],
            "short_realized_ret": [0.01],
        }
    )

    with pytest.raises(DirectionPredictionError, match="unsupported"):
        evaluate_direction_predictions(manifest, ["flat"])
    with pytest.raises(DirectionPredictionError, match="finite"):
        evaluate_direction_predictions(manifest, ["long"])
    with pytest.raises(DirectionPredictionError, match="length"):
        evaluate_direction_predictions(manifest, [])


def test_numeric_direction_baseline_is_deterministic_and_uses_fixed_classes() -> None:
    rows = []
    labels = []
    classes = ("long", "short", "no_trade")
    for class_i, label in enumerate(classes):
        for row_i in range(40):
            rows.append(
                {
                    feature: float(class_i * 10 + row_i / 1000 + feature_i / 10000)
                    for feature_i, feature in enumerate(FEATURE_COLUMNS)
                }
            )
            labels.append(label)
    train = pd.DataFrame(rows)
    train["direction_class"] = labels
    val = train.iloc[[0, 1, 40, 41, 80, 81]].copy().reset_index(drop=True)

    first = train_numeric_direction_baseline(train, val)
    second = train_numeric_direction_baseline(train, val)

    assert first.class_names == classes
    assert first.probabilities.shape == (6, 3)
    assert first.predictions == second.predictions
    assert np.allclose(first.probabilities, second.probabilities)
    assert first.predictions == ["long", "long", "short", "short", "no_trade", "no_trade"]


def test_numeric_direction_baseline_rejects_unknown_training_label() -> None:
    train = pd.DataFrame([{feature: 0.0 for feature in FEATURE_COLUMNS}])
    train["direction_class"] = ["up"]

    with pytest.raises(DirectionPredictionError, match="unsupported"):
        train_numeric_direction_baseline(train, train)

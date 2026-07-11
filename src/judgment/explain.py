"""Native LightGBM contribution summaries for frozen judgment scores.

The contribution vector is produced by ``Booster.predict(pred_contrib=True)``.
Its final value is the model expected value; preceding values are per-feature
contributions in raw-score space. No future market data or outcome labels are
used by this module.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


class ContributionShapeError(ValueError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"expected {expected} values, got {actual}")
        self.expected = expected
        self.actual = actual


class ContributionScoreError(ValueError):
    def __init__(self, predicted: float, reconstructed: float) -> None:
        super().__init__(
            f"contributions reconstruct {reconstructed:.12f}, "
            f"but model predicted {predicted:.12f}"
        )
        self.predicted = predicted
        self.reconstructed = reconstructed


@dataclass(frozen=True)
class FeatureContribution:
    __slots__ = ("feature", "contribution")

    feature: str
    contribution: float


@dataclass(frozen=True)
class ContributionExplanation:
    __slots__ = (
        "contributions",
        "expected_value",
        "predicted_probability",
        "reconstructed_probability",
        "top_positive",
        "top_negative",
    )

    contributions: tuple[FeatureContribution, ...]
    expected_value: float
    predicted_probability: float
    reconstructed_probability: float
    top_positive: FeatureContribution
    top_negative: FeatureContribution


def summarize_contributions(
    feature_names: Sequence[str],
    contribution_vector: np.ndarray,
    predicted_probability: float,
    *,
    tolerance: float = 1e-9,
) -> ContributionExplanation:
    """Validate and summarize one binary LightGBM contribution vector."""
    values = np.asarray(contribution_vector, dtype=float).reshape(-1)
    expected_width = len(feature_names) + 1
    if len(values) != expected_width:
        raise ContributionShapeError(expected_width, len(values))

    raw_score = float(values.sum())
    reconstructed = _sigmoid(raw_score)
    if not math.isclose(reconstructed, predicted_probability, rel_tol=tolerance, abs_tol=tolerance):
        raise ContributionScoreError(predicted_probability, reconstructed)

    contributions = tuple(
        FeatureContribution(feature=name, contribution=float(value))
        for name, value in zip(feature_names, values[:-1])
    )
    return ContributionExplanation(
        contributions=contributions,
        expected_value=float(values[-1]),
        predicted_probability=float(predicted_probability),
        reconstructed_probability=reconstructed,
        top_positive=max(contributions, key=lambda item: item.contribution),
        top_negative=min(contributions, key=lambda item: item.contribution),
    )


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)

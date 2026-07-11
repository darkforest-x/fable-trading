from __future__ import annotations

import math

import numpy as np
import pytest

from src.judgment.explain import ContributionShapeError, summarize_contributions


def test_summarize_contributions_reconstructs_binary_probability() -> None:
    feature_names = ("spread", "momentum", "volume")
    contributions = np.array([0.6, -0.2, 0.1, -0.4])
    probability = 1.0 / (1.0 + math.exp(-float(contributions.sum())))

    explanation = summarize_contributions(feature_names, contributions, probability)

    assert explanation.reconstructed_probability == pytest.approx(probability)
    assert explanation.top_positive.feature == "spread"
    assert explanation.top_positive.contribution == pytest.approx(0.6)
    assert explanation.top_negative.feature == "momentum"
    assert explanation.top_negative.contribution == pytest.approx(-0.2)
    assert explanation.expected_value == pytest.approx(-0.4)


def test_summarize_contributions_rejects_wrong_vector_width() -> None:
    with pytest.raises(ContributionShapeError, match="expected 3 values"):
        summarize_contributions(("spread", "momentum"), np.array([0.2, -0.1]), 0.5)

"""Unit tests for the no-training judgment signal audit."""
from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.analyze_pine_eth_15m_judgment_signal import (
    empirical_score,
    exact_shift_null,
    holm_adjust,
)


def test_empirical_score_uses_only_train_distribution_and_direction() -> None:
    train = np.asarray([1.0, 2.0, 3.0, 4.0])
    validation = np.asarray([0.0, 2.0, 5.0])
    assert empirical_score(train, validation, 1).tolist() == [0.0, 0.5, 1.0]
    assert empirical_score(train, validation, -1).tolist() == [1.0, 0.5, 0.0]


def test_holm_adjustment_is_familywise_and_monotone_by_sorted_p() -> None:
    adjusted = holm_adjust([0.01, 0.04, 0.03, 0.20])
    assert np.allclose(adjusted, [0.04, 0.09, 0.09, 0.20])
    assert (adjusted >= np.asarray([0.01, 0.04, 0.03, 0.20])).all()


def test_exact_shift_null_enumerates_cartesian_fold_shifts() -> None:
    parts = []
    for offset in (0.0, 0.1, 0.2):
        parts.append(
            pd.DataFrame(
                {
                    "score": [0.1, 0.4, 0.8, 0.9],
                    "project_net_return": [-0.02, -0.01, 0.01, 0.03 + offset],
                    "net_positive": [False, False, True, True],
                }
            )
        )
    result = exact_shift_null(parts)
    assert result["validation_rows"] == 12
    assert result["exact_shift_combinations"] == 4**3
    assert result["fold_shift_counts"] == [4, 4, 4]
    assert result["auc"] == 1.0
    assert result["top_decile_rows"] == 3
    assert 0.0 < result["auc_exact_circular_shift_p"] <= 1.0

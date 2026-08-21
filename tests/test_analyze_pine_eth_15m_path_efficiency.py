"""Tests for the preregistered Pine path-efficiency diagnostic."""
from __future__ import annotations

import pandas as pd
import pytest

from scripts.analyze_pine_eth_15m_path_efficiency import (
    FEATURE_COLUMN,
    attach_path_efficiency,
    rank_diagnostic,
)


def test_attach_path_efficiency_preserves_identity_and_excludes_signal_bar() -> None:
    featured = pd.DataFrame({"close": list(range(40))})
    surface = pd.DataFrame(
        {
            "candidate_id": ["a"],
            "side": ["long"],
            "signal_i": [35],
            "signal_time": ["2024-01-01T00:00:00Z"],
            "features_available_at": ["2024-01-01T00:15:00Z"],
            "earliest_entry_time": ["2024-01-01T00:15:00Z"],
            "candidate_policy": ["test"],
        }
    )
    original = attach_path_efficiency(surface, featured)
    changed = featured.copy()
    changed.loc[35:, "close"] = -1000
    perturbed = attach_path_efficiency(surface, changed)
    assert original.loc[0, FEATURE_COLUMN] == pytest.approx(1.0)
    assert perturbed.loc[0, FEATURE_COLUMN] == original.loc[0, FEATURE_COLUMN]
    assert not bool(original.loc[0, "training_eligible"])


def test_rank_diagnostic_uses_fixed_high_feature_orientation() -> None:
    rows = pd.DataFrame(
        {
            FEATURE_COLUMN: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
            "project_net_return": [-0.01] * 8 + [0.02, 0.05],
            "net_positive": [False] * 8 + [True, True],
        }
    )
    result = rank_diagnostic(rows, permutations=199, seed=7)
    assert result["top_decile_rows"] == 1
    assert result["top_decile_net_bp_per_trade"] == pytest.approx(500.0)
    assert result["auc_net_positive"] == pytest.approx(1.0)
    assert result["spearman_return"] > 0.0
    assert result["top_decile_permutation_p_one_sided"] <= 0.25


def test_rank_diagnostic_rejects_invalid_inputs() -> None:
    rows = pd.DataFrame(
        {
            FEATURE_COLUMN: [0.1, 0.2],
            "project_net_return": [0.0, 0.1],
            "net_positive": [False, True],
        }
    )
    with pytest.raises(ValueError, match="positive integer"):
        rank_diagnostic(rows, permutations=0)
    with pytest.raises(ValueError, match="both positive"):
        rank_diagnostic(rows.assign(net_positive=False), permutations=10)

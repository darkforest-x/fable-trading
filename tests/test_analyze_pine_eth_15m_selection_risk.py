"""Tests for the development search-budget audit."""
from __future__ import annotations

import pandas as pd

from scripts.analyze_pine_eth_15m_selection_risk import (
    collapse_identical_paths,
    exact_common_max_stat,
    pbo_rank_reversal,
)


def test_identical_paths_are_collapsed_with_aliases() -> None:
    rows = []
    for configuration, values in (
        ("a", [1.0, 2.0, 3.0, 4.0]),
        ("b", [1.0, 2.0, 3.0, 4.0]),
        ("c", [-1.0, -2.0, -3.0, -4.0]),
    ):
        for period, value in zip(("2023H1", "2023H2", "2024H1", "2024H2"), values):
            rows.append(
                {
                    "configuration": configuration,
                    "period": period,
                    "project_net_bp_per_trade": value,
                    "trades": 10,
                }
            )
    returns, trades, aliases = collapse_identical_paths(pd.DataFrame(rows))
    assert len(returns) == len(trades) == 2
    assert aliases["a"] == ["a", "b"]


def test_common_max_stat_uses_all_sign_patterns() -> None:
    returns = pd.DataFrame(
        [[1.0, 2.0, 3.0, 4.0], [-1.0, 5.0, -2.0, 1.0]],
        index=["a", "b"],
        columns=["2023H1", "2023H2", "2024H1", "2024H2"],
    )
    result = exact_common_max_stat(returns)
    assert result["selected_configuration"] == "a"
    assert result["exact_common_sign_patterns"] == 16
    assert result["minimum_attainable_p_value"] == 1 / 16
    assert 0.0 < result["selection_adjusted_p_value"] <= 1.0


def test_two_by_two_rank_reversal_has_six_directed_partitions() -> None:
    returns = pd.DataFrame(
        [[1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0], [2.0, 2.0, 2.0, 2.0]],
        index=["a", "b", "c"],
        columns=["2023H1", "2023H2", "2024H1", "2024H2"],
    )
    result = pbo_rank_reversal(returns)
    assert result["partition_count"] == 6
    assert result["formal_pbo_claimed"] is False
    assert 0.0 <= result["descriptive_pbo_fraction"] <= 1.0

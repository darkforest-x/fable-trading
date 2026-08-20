"""Contracts for ETH 15m block-bootstrap path-risk diagnostics."""
import numpy as np
import pandas as pd
import pytest

from scripts.analyze_pine_eth_15m_path_risk import (
    circular_block_bootstrap,
    longest_losing_streak,
)


def test_zero_week_returns_have_zero_terminal_and_drawdown() -> None:
    result = circular_block_bootstrap(
        np.zeros(8),
        n_resamples=100,
        block_weeks=4,
        seed=1,
    )
    assert np.array_equal(result["terminal_return"], np.zeros(100))
    assert np.array_equal(result["maximum_drawdown"], np.zeros(100))


def test_block_bootstrap_is_seed_deterministic() -> None:
    values = np.array([0.01, -0.02, 0.03, -0.01, 0.04, -0.03])
    first = circular_block_bootstrap(values, n_resamples=20, block_weeks=3, seed=7)
    second = circular_block_bootstrap(values, n_resamples=20, block_weeks=3, seed=7)
    assert np.array_equal(first["terminal_return"], second["terminal_return"])
    assert np.array_equal(first["maximum_drawdown"], second["maximum_drawdown"])


def test_block_bootstrap_refuses_bankruptcy_return() -> None:
    with pytest.raises(ValueError, match="-100%"):
        circular_block_bootstrap(
            np.array([0.1, -1.0, 0.2, 0.0]),
            n_resamples=10,
            block_weeks=4,
            seed=1,
        )


def test_longest_losing_streak_counts_nonpositive_runs() -> None:
    trades = pd.DataFrame(
        {"project_net_return": [0.1, -0.1, 0.0, -0.2, 0.3, -0.1, -0.1]}
    )
    assert longest_losing_streak(trades) == 3

"""Causality and numerical tests for pre-cross path efficiency."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l2_judgment.pine_path_efficiency import (
    add_pre_cross_path_efficiency,
    path_efficiency_column,
)


def test_monotone_and_retracing_paths_have_expected_efficiency() -> None:
    lookback = 4
    column = path_efficiency_column(lookback)
    monotone = pd.DataFrame({"close": [0, 1, 2, 3, 4, 5, 6]})
    retracing = pd.DataFrame({"close": [0, 1, 0, 1, 0, 1, 0]})

    monotone_result = add_pre_cross_path_efficiency(monotone, lookback=lookback)
    retracing_result = add_pre_cross_path_efficiency(retracing, lookback=lookback)

    assert monotone_result.loc[5, column] == pytest.approx(1.0)
    assert retracing_result.loc[5, column] == pytest.approx(0.0)


def test_signal_bar_and_future_rows_cannot_change_decision_value() -> None:
    lookback = 4
    decision = 7
    column = path_efficiency_column(lookback)
    base = pd.DataFrame({"close": np.arange(12, dtype=float)})
    original = add_pre_cross_path_efficiency(base, lookback=lookback)

    changed = base.copy()
    changed.loc[decision:, "close"] = [-500.0, 900.0, -800.0, 700.0, -600.0]
    perturbed = add_pre_cross_path_efficiency(changed, lookback=lookback)

    pd.testing.assert_series_equal(
        original.loc[:decision, column],
        perturbed.loc[:decision, column],
    )


def test_prior_right_edge_is_t_minus_one() -> None:
    lookback = 4
    decision = 7
    column = path_efficiency_column(lookback)
    base = pd.DataFrame({"close": np.arange(12, dtype=float)})
    original = add_pre_cross_path_efficiency(base, lookback=lookback)

    changed = base.copy()
    changed.loc[decision - 1, "close"] = -100.0
    perturbed = add_pre_cross_path_efficiency(changed, lookback=lookback)

    assert original.loc[decision, column] != perturbed.loc[decision, column]


def test_invalid_contract_and_flat_path_fail_closed() -> None:
    with pytest.raises(ValueError, match="greater than one"):
        path_efficiency_column(1)
    with pytest.raises(ValueError, match="missing"):
        add_pre_cross_path_efficiency(pd.DataFrame({"open": [1.0, 2.0]}))

    column = path_efficiency_column(4)
    flat = add_pre_cross_path_efficiency(
        pd.DataFrame({"close": np.ones(8)}),
        lookback=4,
    )
    assert flat[column].isna().all()

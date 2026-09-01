"""Causality and direction tests for the 128-bar global-shape feature contract."""

from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.layers.l2_judgment.global_shape import (
    GLOBAL_CONTEXT_BARS,
    GLOBAL_SHAPE_FEATURE_COLUMNS,
    add_global_shape_indicators,
    extract_global_shape_features,
)


def _frame(rows: int = 400) -> pd.DataFrame:
    x = np.arange(rows, dtype=float)
    close = 100.0 + x * 0.02 + np.sin(x / 9.0) * 0.3
    return pd.DataFrame(
        {
            "open": close - 0.03,
            "high": close + 0.20,
            "low": close - 0.20,
            "close": close,
            "volume": 1_000.0 + (x % 17) * 10.0,
        }
    )


def test_feature_contract_is_finite_and_fixed_width() -> None:
    enriched = add_global_shape_indicators(_frame())
    values = extract_global_shape_features(
        enriched,
        decision_i=300,
        core_end_i=296,
        side="long",
        confirmation_bars=4,
    )
    assert tuple(values) == GLOBAL_SHAPE_FEATURE_COLUMNS
    assert len(values) == 35
    assert all(np.isfinite(value) for value in values.values())
    assert GLOBAL_CONTEXT_BARS == 128


def test_extreme_future_mutation_cannot_change_feature_row() -> None:
    decision_i = 300
    original = _frame()
    mutated = original.copy()
    future = mutated.index > decision_i
    mutated.loc[future, ["open", "high", "low", "close"]] *= 50.0
    mutated.loc[future, "volume"] *= 1_000.0
    before = extract_global_shape_features(
        add_global_shape_indicators(original),
        decision_i=decision_i,
        core_end_i=296,
        side="short",
        confirmation_bars=4,
    )
    after = extract_global_shape_features(
        add_global_shape_indicators(mutated),
        decision_i=decision_i,
        core_end_i=296,
        side="short",
        confirmation_bars=4,
    )
    assert before == after


def test_directional_returns_and_slopes_mirror_between_sides() -> None:
    enriched = add_global_shape_indicators(_frame())
    long = extract_global_shape_features(
        enriched, decision_i=300, core_end_i=296, side="long", confirmation_bars=4
    )
    short = extract_global_shape_features(
        enriched, decision_i=300, core_end_i=296, side="short", confirmation_bars=4
    )
    directional = [
        column
        for column in GLOBAL_SHAPE_FEATURE_COLUMNS
        if column.startswith("aligned_") and column != "aligned_ma_order_score"
    ]
    for column in directional:
        assert np.isclose(long[column], -short[column])


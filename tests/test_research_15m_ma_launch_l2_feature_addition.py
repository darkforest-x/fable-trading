from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.owner_side_rich_features import add_rich_features, rich_feature_columns
from scripts.research_15m_ma_launch_l2_feature_addition import (
    ADDITION_GROUPS,
    BASE_COLUMNS,
    DUPLICATE_RICH_COLUMNS,
    EXTRA_COLUMNS,
    MODEL_ARMS,
    SELECTION_ARMS,
    load_preregistration,
)
from yoyo.data.indicators import add_indicators
from yoyo.layers.l2_judgment.features import (
    FEATURE_COLUMNS,
    add_features,
    extract_feature_rows_for_side,
)


def synthetic_ohlcv(rows: int = 420) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    close = 100.0 + 0.02 * index + np.sin(index / 11.0)
    open_ = close + 0.05 * np.cos(index / 7.0)
    high = np.maximum(open_, close) + 0.2 + 0.03 * np.sin(index / 5.0)
    low = np.minimum(open_, close) - 0.2 - 0.03 * np.cos(index / 6.0)
    volume = 1000.0 + 40.0 * np.sin(index / 13.0) + index
    return pd.DataFrame(
        {
            "open_time": pd.date_range(
                "2025-12-01", periods=rows, freq="15min", tz="UTC"
            ),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_addition_groups_are_unique_and_exclude_semantic_duplicates() -> None:
    flattened = [column for columns in ADDITION_GROUPS.values() for column in columns]
    assert tuple(flattened) == EXTRA_COLUMNS
    assert len(flattened) == len(set(flattened)) == 82
    assert set(flattened).isdisjoint(FEATURE_COLUMNS)
    assert set(flattened).isdisjoint(DUPLICATE_RICH_COLUMNS)
    rich = set(rich_feature_columns())
    assert rich == set(FEATURE_COLUMNS) | set(flattened) | set(DUPLICATE_RICH_COLUMNS)


def test_preregistered_arms_match_code_and_expected_sizes() -> None:
    prereg = load_preregistration()
    assert prereg["selection"]["final_validation_used_for_selection"] is False
    assert {name: len(columns) for name, columns in SELECTION_ARMS.items()} == {
        "baseline_28": 28,
        "plus_ma_family": 54,
        "plus_dense_dynamics": 36,
        "plus_ma_dense": 62,
        "plus_momentum_structure": 50,
        "plus_candle_volatility": 39,
        "plus_volume_flow": 33,
        "plus_market_structure": 33,
        "plus_time_context": 33,
        "full_110": 110,
    }
    assert len(MODEL_ARMS) == 11
    assert len(MODEL_ARMS["ma_spread_only"]) == 1


def test_rich_builder_preserves_legacy_28_for_both_side_extractors() -> None:
    frame = synthetic_ohlcv()
    rich = add_rich_features(frame)
    base = add_features(add_indicators(frame))
    indices = [300, 350, 400]
    for side in ("long", "short"):
        observed = extract_feature_rows_for_side(rich, indices, side)
        expected = extract_feature_rows_for_side(base, indices, side)
        np.testing.assert_allclose(
            observed[list(BASE_COLUMNS)].to_numpy(),
            expected[list(BASE_COLUMNS)].to_numpy(),
            rtol=0.0,
            atol=1e-14,
        )


def test_additions_are_causal_under_future_mutation() -> None:
    frame = synthetic_ohlcv()
    cut = 320
    first = add_rich_features(frame).iloc[cut][list(EXTRA_COLUMNS)]
    mutated = frame.copy()
    mutated.loc[cut + 1 :, ["open", "high", "low", "close", "volume"]] *= 7.0
    second = add_rich_features(mutated).iloc[cut][list(EXTRA_COLUMNS)]
    np.testing.assert_allclose(
        first.to_numpy(dtype=float),
        second.to_numpy(dtype=float),
        rtol=0.0,
        atol=0.0,
    )


def test_all_additions_are_finite_after_declared_warmup() -> None:
    rich = add_rich_features(synthetic_ohlcv())
    values = rich.iloc[300:][list(EXTRA_COLUMNS)].to_numpy(dtype=float)
    assert np.isfinite(values).all()

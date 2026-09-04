from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.research_btcusdtp_k1k2_causal_failure_map import (
    attach_causal_features,
    familywise_permutation_p,
)


def _frame() -> pd.DataFrame:
    n = 220
    index = np.arange(n, dtype=float)
    close = 100.0 + 0.02 * index + np.sin(index / 7.0)
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2023-01-01", periods=n, freq="5min", tz="UTC"),
            "open": close - 0.03,
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close,
            "atr": np.full(n, 0.8),
            "sma40_hl2": pd.Series(close).rolling(40, min_periods=1).mean(),
            "volume_ratio_20": 1.0 + 0.01 * np.cos(index),
            "atr_release_24": np.full(n, 1.0),
            "ma_shift_osc": np.sin(index / 20.0),
            "ma_shift_osc_delta": np.cos(index / 20.0) / 20.0,
            "market_break_state": np.where(index % 3 == 0, 1, -1),
            "ma_shift_candle_side": np.where(close >= pd.Series(close).rolling(40, min_periods=1).mean(), 1, -1),
            "native_candle_side": np.ones(n, dtype=int),
            "range_atr": np.ones(n, dtype=float),
        }
    )


def test_features_do_not_read_after_entry_open() -> None:
    candidates = pd.DataFrame(
        {
            "k1_i": [120, 140],
            "k2_i": [126, 148],
            "direction": [1, -1],
        }
    )
    original = _frame()
    changed = original.copy()
    changed.loc[150:, ["open", "high", "low", "close"]] = 999999.0
    left = attach_causal_features(candidates, original)
    right = attach_causal_features(candidates, changed)
    pd.testing.assert_frame_equal(left, right, check_exact=False, rtol=0.0, atol=0.0)


def test_familywise_permutation_is_deterministic() -> None:
    y = np.array([-3.0, 1.0, 2.0, -1.0, 4.0, -2.0, 3.0, 0.0])
    labels = np.array(["2023H1"] * 4 + ["2023H2"] * 4)
    masks = [
        np.array([True, True, False, False, True, True, False, False]),
        np.array([False, True, True, False, False, True, True, False]),
    ]
    first = familywise_permutation_p(y, labels, masks, 200, 17)
    second = familywise_permutation_p(y, labels, masks, 200, 17)
    assert first == second
    assert 0.0 < first["familywise_permutation_p"] <= 1.0


def test_familywise_permutation_matches_direct_observed_maximum() -> None:
    y = np.array([-3.0, 1.0, 2.0, -1.0, 4.0, -2.0, 3.0, 0.0])
    labels = np.array(["2023H1"] * 4 + ["2023H2"] * 4)
    masks = [
        np.array([True, True, False, False, True, True, False, False]),
        np.array([False, True, True, False, False, True, True, False]),
    ]
    expected = max(
        min(
            y[mask & (labels == half)].mean() - y[labels == half].mean()
            for half in ("2023H1", "2023H2")
        )
        for mask in masks
    )
    actual = familywise_permutation_p(y, labels, masks, 20, 17)
    assert actual["observed_max_stable_improvement_bp"] == expected

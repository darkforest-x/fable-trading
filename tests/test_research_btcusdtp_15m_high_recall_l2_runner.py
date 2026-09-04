from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.research_btcusdtp_15m_high_recall_l2_runner import (
    add_context_features,
    matrix,
    score_permutation_p,
)


def frame(rows: int = 420) -> pd.DataFrame:
    time = pd.date_range("2023-01-01", periods=rows, freq="15min", tz="UTC")
    close = 100.0 + np.sin(np.arange(rows) / 11.0) + np.arange(rows) * 0.002
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "open_time": time,
            "open": open_,
            "high": np.maximum(open_, close) + 0.3,
            "low": np.minimum(open_, close) - 0.3,
            "close": close,
            "volume": 1000.0 + np.arange(rows),
            "atr": np.full(rows, 1.0),
            "segment_id": np.ones(rows, dtype=int),
        }
    )


def events(source: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for signal_i, direction, family in (
        (300, 1, "direct+rejection"),
        (330, -1, "coil"),
    ):
        rows.append(
            {
                "signal_i": signal_i,
                "direction": direction,
                "signal_family": family,
                "signal_score": 0.7,
                "signed_close_atr": 0.4,
                "signed_slope_atr_per_bar": 0.02,
                "signed_body_atr": 0.8,
                "breakout_atr": 0.2,
                "prior_near_ma_share": 0.75,
                "prior_range_atr": 2.0,
                "risk_fraction": 0.02,
                "entry_time": source.loc[signal_i + 1, "open_time"],
            }
        )
    return pd.DataFrame(rows)


def test_context_features_do_not_change_when_future_bars_change() -> None:
    base = frame()
    first = add_context_features(base, events(base), cost=0.002)
    changed = base.copy()
    changed.loc[301:, ["open", "high", "low", "close", "volume"]] *= 4.0
    second = add_context_features(changed, events(changed), cost=0.002)
    feature_columns = [
        "atr_pct",
        "volume_ratio20",
        "atr_ratio96",
        "range_ratio20",
        "eff12",
        "eff24",
        "adx14",
        "signed_di_balance",
        "ema20_sma60_spread_atr",
        "ema30_sma60_spread_atr",
        "sma60_sma120_spread_atr",
        "sma60_sma160_spread_atr",
        "sma120_sma240_spread_atr",
        "signed_return8_atr",
        "signed_return24_atr",
        "prior24_range_atr",
    ]
    np.testing.assert_allclose(
        first.loc[0, feature_columns].to_numpy(dtype=float),
        second.loc[0, feature_columns].to_numpy(dtype=float),
        equal_nan=True,
    )


def test_matrix_uses_training_medians_for_later_rows() -> None:
    train = pd.DataFrame({"a": [1.0, np.nan, 3.0], "b": [np.nan, np.nan, np.nan]})
    _, medians = matrix(train, ["a", "b"])
    later = pd.DataFrame({"a": [np.nan], "b": [np.nan]})
    filled, reused = matrix(later, ["a", "b"], medians)
    assert medians == {"a": 2.0, "b": 0.0}
    assert reused == medians
    assert filled.iloc[0].to_dict() == medians


def test_score_permutation_is_deterministic_and_one_sided() -> None:
    pool = pd.DataFrame({"net_return": np.linspace(-0.02, 0.02, 100)})
    selected = pool.tail(5)
    first = score_permutation_p(pool, selected, resamples=2_000, seed=7)
    second = score_permutation_p(pool, selected, resamples=2_000, seed=7)
    assert first == second
    assert first < 0.01

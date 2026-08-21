"""Causality and formula tests for the ETH 15m V15 trend ensemble."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l2_judgment.pine_trend_ensemble import (
    DEFAULT_DONCHIAN_WINDOWS,
    DEFAULT_EWMAC_SPEED_PAIRS,
    TrendEnsembleProfile,
    add_trend_ensemble_features,
    trend_ensemble_gate_mask,
)


def _frame(rows: int = 260) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    close = 100.0 + index * 0.08 + np.sin(index / 5.0) * 0.7
    return pd.DataFrame(
        {
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "atr": np.full(rows, 1.2),
            "dense_start_ready": np.ones(rows, dtype=bool),
            "dense_start_score_long": np.full(rows, 0.6),
            "dense_start_score_short": np.full(rows, 0.4),
        }
    )


def test_default_contract_has_three_ewmac_and_three_prior_channels() -> None:
    featured = add_trend_ensemble_features(_frame())
    assert DEFAULT_EWMAC_SPEED_PAIRS == ((8, 32), (16, 64), (32, 128))
    assert DEFAULT_DONCHIAN_WINDOWS == (24, 48, 96)
    assert all(
        column in featured
        for column in (
            "trend_ewmac_8_32",
            "trend_ewmac_16_64",
            "trend_ewmac_32_128",
            "trend_donchian_24",
            "trend_donchian_48",
            "trend_donchian_96",
        )
    )
    ready = featured.loc[featured["trend_ensemble_ready"]]
    assert not ready.empty
    assert ready["trend_ensemble_forecast"].between(-1.0, 1.0).all()
    assert ready["trend_quality_long"].between(0.0, 1.0).all()
    assert ready["trend_quality_short"].between(0.0, 1.0).all()


def test_donchian_channel_excludes_decision_bar_high_and_low() -> None:
    decision = 180
    base = _frame()
    original = add_trend_ensemble_features(base)
    perturbed = base.copy()
    perturbed.loc[decision, "high"] = 10_000.0
    perturbed.loc[decision, "low"] = 0.01
    changed = add_trend_ensemble_features(perturbed)
    for window in DEFAULT_DONCHIAN_WINDOWS:
        assert original.loc[decision, f"trend_donchian_upper_{window}"] == changed.loc[
            decision, f"trend_donchian_upper_{window}"
        ]
        assert original.loc[decision, f"trend_donchian_lower_{window}"] == changed.loc[
            decision, f"trend_donchian_lower_{window}"
        ]
        assert original.loc[decision, f"trend_donchian_{window}"] == changed.loc[
            decision, f"trend_donchian_{window}"
        ]


def test_future_rows_do_not_change_past_ensemble_features() -> None:
    decision = 180
    base = _frame()
    original = add_trend_ensemble_features(base)
    perturbed = base.copy()
    perturbed.loc[decision + 1 :, ["high", "low", "close", "atr"]] *= 7.0
    perturbed.loc[decision + 1 :, "dense_start_score_long"] = 0.0
    perturbed.loc[decision + 1 :, "dense_start_score_short"] = 1.0
    changed = add_trend_ensemble_features(perturbed)
    columns = [column for column in original if column.startswith("trend_")]
    pd.testing.assert_frame_equal(
        original.loc[:decision, columns],
        changed.loc[:decision, columns],
    )


def test_quality_is_exact_80_percent_trend_and_20_percent_dense() -> None:
    featured = add_trend_ensemble_features(_frame())
    row = featured.loc[featured["trend_ensemble_ready"]].iloc[-1]
    assert row["trend_quality_long"] == pytest.approx(
        0.8 * row["trend_support_long"] + 0.2 * row["dense_start_score_long"]
    )
    assert row["trend_quality_short"] == pytest.approx(
        0.8 * row["trend_support_short"] + 0.2 * row["dense_start_score_short"]
    )
    assert row["trend_support_long"] + row["trend_support_short"] == pytest.approx(1.0)


def test_gate_is_only_ready_plus_quality_threshold() -> None:
    profile = TrendEnsembleProfile("test", minimum_quality=0.55)
    frame = pd.DataFrame(
        {
            "trend_ensemble_ready": [True, True, False],
            "trend_quality_long": [0.55, 0.5499, 0.99],
        }
    )
    assert trend_ensemble_gate_mask(frame, profile, side="long").tolist() == [
        True,
        False,
        False,
    ]


def test_profile_and_contract_validation_fail_closed() -> None:
    with pytest.raises(ValueError, match="minimum_quality"):
        TrendEnsembleProfile("bad", minimum_quality=1.1)
    with pytest.raises(ValueError, match="fast < slow"):
        add_trend_ensemble_features(_frame(), ewmac_speed_pairs=((32, 8),))
    with pytest.raises(ValueError, match="sum to one"):
        add_trend_ensemble_features(_frame(), trend_weight=0.7, dense_weight=0.2)

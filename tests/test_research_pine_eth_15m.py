"""Causality and inference checks for the frozen ETH 15m Pine builder."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.research_pine_eth_15m import (
    FEATURE_COLUMNS,
    block_signflip,
    build_feature_frame,
    concentration_diagnostics,
    control_outcome,
    load_config,
)
from yoyo.layers.l2_judgment.features import add_features
from yoyo.data.indicators import add_indicators as add_project_indicators
from yoyo.layers.l3_backtest.pine_allin_v7 import SignalParameters


def _bars(rows: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(20260821)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.003, rows)))
    open_ = np.r_[close[0], close[:-1]]
    spread = rng.uniform(0.001, 0.006, rows) * close
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2023-01-01", periods=rows, freq="15min", tz="UTC"),
            "open": open_,
            "high": np.maximum(open_, close) + spread,
            "low": np.minimum(open_, close) - spread,
            "close": close,
            "volume": rng.uniform(10.0, 100.0, rows),
        }
    )


def test_v9_signal_prefix_is_invariant_to_future_bars() -> None:
    raw = _bars()
    original = build_feature_frame(raw)
    changed = raw.copy()
    changed.loc[450:, ["open", "high", "low", "close"]] *= 2.0
    replay = build_feature_frame(changed)
    for column in ("v8_long", "v8_short", "v9_long", "v9_short", "slow_slope_12"):
        pd.testing.assert_series_equal(
            original.loc[:449, column], replay.loc[:449, column], check_names=False
        )


def test_config_freezes_15m_and_blocks_training() -> None:
    config = load_config()
    assert config["instrument"]["bar_minutes"] == 15
    assert config["eligibility"]["holdout_consumed"] is False
    assert config["eligibility"]["training_eligible"] is False
    assert config["signal_contract"]["locked_oscillator_threshold"] == pytest.approx(0.1)


def test_control_outcome_uses_signal_close_tick_stop_and_two_cost_views() -> None:
    frame = _bars(10)
    frame[["open", "high", "low", "close"]] = 100.0
    frame["atr"] = 0.251
    frame.loc[1, "open"] = 101.0
    frame.loc[1, "low"] = 99.0
    outcome = control_outcome(
        frame,
        signal_i=0,
        direction=1,
        holding_bars=2,
        params=SignalParameters(),
    )
    assert outcome["control_exit_reason"] == "stop"
    assert outcome["control_exit_price"] == pytest.approx(100.0)
    gross = 100.0 / 101.0 - 1.0
    assert outcome["control_project_net_return"] == pytest.approx(gross - 0.002)
    assert outcome["control_pine_net_return"] == pytest.approx(
        gross - 0.001 * (1.0 + 100.0 / 101.0)
    )


def test_week_signflip_is_clustered_and_deterministic() -> None:
    pairs = pd.DataFrame(
        {
            "candidate_entry_time": pd.date_range(
                "2025-01-01", periods=12, freq="7D", tz="UTC"
            ),
            "excess_return": np.full(12, 0.01),
        }
    )
    first = block_signflip(pairs, n_resamples=10_000, seed=7)
    second = block_signflip(pairs, n_resamples=10_000, seed=7)
    assert first == second
    assert first["n_blocks"] == 12
    assert first["statistic_mean_excess_bp"] == pytest.approx(100.0)
    assert first["p_value"] < 0.01


def test_project_feature_export_has_exact_28_column_contract() -> None:
    featured = add_features(add_project_indicators(_bars()))
    assert len(FEATURE_COLUMNS) == 28
    assert set(FEATURE_COLUMNS).issubset(featured.columns)


def test_profit_concentration_exposes_top_trade_dependency() -> None:
    trades = pd.DataFrame(
        {
            "project_net_return": [0.10, -0.02, -0.02],
            "entry_time": pd.to_datetime(
                ["2025-01-01", "2025-01-08", "2025-02-01"], utc=True
            ),
            "exit_reason": ["reverse", "stop", "stop"],
        }
    )
    result = concentration_diagnostics(trades)
    assert result["positive_trades"] == 1
    assert result["top1_share_of_net"] > 1.0
    assert result["mean_without_top1_bp"] == pytest.approx(-200.0)

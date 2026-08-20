"""Causality and safety tests for the dependency-light V9 replay."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from scripts.replay_pine_eth_15m_offline import (
    CONFIG,
    build_v9_frame,
    replay_and_reconcile,
)


def _synthetic_bars(rows: int = 720) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    close = 100.0 + index * 0.003 + 2.4 * np.sin(index / 17.0)
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=rows, freq="15min", tz="UTC"),
            "open": close + 0.05 * np.sin(index / 3.0),
            "high": close + 0.7,
            "low": close - 0.7,
            "close": close,
            "volume": 100.0 + 10.0 * np.cos(index / 11.0),
        }
    )


def test_v9_features_do_not_change_when_only_future_bars_change() -> None:
    bars = _synthetic_bars()
    baseline = build_v9_frame(bars)
    changed = bars.copy()
    cutoff = 520
    changed.loc[cutoff + 1 :, ["open", "high", "low", "close"]] *= 1.8
    replayed = build_v9_frame(changed)

    columns = [
        "fast_ma",
        "slow_ma",
        "regime_ma",
        "atr",
        "osc",
        "v9_long",
        "v9_short",
        "v9_score",
        "entry_allowed",
    ]
    pd.testing.assert_frame_equal(
        baseline.loc[:cutoff, columns],
        replayed.loc[:cutoff, columns],
        check_exact=True,
    )


def test_offline_replay_refuses_consumed_holdout_flag(tmp_path) -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["eligibility"]["holdout_consumed"] = True
    changed = tmp_path / "config.json"
    changed.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(RuntimeError, match="holdout flag"):
        replay_and_reconcile(config_path=changed)

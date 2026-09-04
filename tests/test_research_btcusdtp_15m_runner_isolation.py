"""Causality and execution contracts for the BTC 15m runner-isolation study."""
from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.research_btcusdtp_15m_runner_isolation import (
    delayed_confirmation_entries,
    sequence_tensor,
    simulate_progress_stop,
    strict_k1k2_score,
)


def _market(rows: int = 8) -> pd.DataFrame:
    close = np.arange(100.0, 100.0 + rows)
    return pd.DataFrame(
        {
            "open": close - 0.25,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.linspace(100.0, 170.0, rows),
            "atr": np.ones(rows),
            "ema30": close - 0.5,
            "sma60": np.full(rows, 99.0),
            "trend_ma": np.full(rows, 99.0),
            "volume_median20_prior": np.full(rows, 100.0),
            "segment_id": np.zeros(rows, dtype=int),
        }
    )


def test_strict_k1k2_gate_requires_every_registered_geometry_clause() -> None:
    events = pd.DataFrame(
        {
            "k1_found": [1.0, 1.0, 1.0],
            "k1_gap_bars": [2.0, 9.0, 4.0],
            "between_wrong_side_share": [0.0, 0.0, 0.0],
            "k2_touch_depth_atr": [0.0, 0.0, -0.01],
            "k2_body_clearance_atr": [0.0, 0.0, 0.0],
            "signal_score": [0.2, 0.9, 0.8],
        }
    )

    scores = strict_k1k2_score(events)

    assert scores[0] > 0.5
    assert scores[1] < 0.5
    assert scores[2] < 0.5


def test_sequence_tensor_ends_at_signal_and_zero_pads_across_source_gap() -> None:
    market = _market()
    market.loc[4:, "segment_id"] = 1
    events = pd.DataFrame(
        {
            "signal_i": [5, 5],
            "direction": [1, -1],
        }
    )

    original = sequence_tensor(market, events, window=4)
    changed_future = market.copy()
    changed_future.loc[6:, ["open", "high", "low", "close", "volume"]] = 1e9
    replay = sequence_tensor(changed_future, events, window=4)

    np.testing.assert_allclose(original, replay)
    np.testing.assert_allclose(original[0, :4], -original[1, :4])
    np.testing.assert_allclose(original[0, :, :2], 0.0)
    np.testing.assert_allclose(original[0, 4:], original[1, 4:])


def test_delayed_confirmation_can_only_fill_on_bar_after_arm_close() -> None:
    market = _market(7)
    event = pd.DataFrame(
        {
            "setup_id": ["long-1"],
            "direction": [1],
            "signal_time": [pd.Timestamp("2025-01-01", tz="UTC")],
            "entry_i": [0],
            "entry_price": [99.75],
            "signal_atr": [1.0],
            "runner_armed": [True],
            "runner_arm_i": [2],
        }
    )

    result = delayed_confirmation_entries(market, event, cost=0.002)

    assert len(result) == 1
    assert result.iloc[0]["entry_i"] == 3
    assert result.iloc[0]["entry_price"] == market.iloc[3]["open"]


def test_progress_failure_decided_at_close_exits_only_at_next_open() -> None:
    market = _market(7)
    market.loc[:, "close"] = [100.0, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6]
    market.loc[:, "open"] = [100.0, 100.05, 100.25, 100.35, 100.45, 100.55, 100.65]
    market.loc[:, "high"] = market["close"] + 0.2
    market.loc[:, "low"] = market["close"] - 0.2
    event = pd.DataFrame(
        {
            "setup_id": ["slow-long"],
            "direction": [1],
            "signal_time": [pd.Timestamp("2025-01-01", tz="UTC")],
            "entry_i": [0],
            "entry_price": [100.0],
            "signal_atr": [1.0],
        }
    )

    result = simulate_progress_stop(
        market,
        event,
        deadline_bars=2,
        required_close_atr=0.5,
        cost=0.002,
    )

    row = result.iloc[0]
    assert row["outcome"] == "progress_exit_next_open"
    assert row["exit_i"] == 2
    assert row["exit_price"] == market.iloc[2]["open"]
    assert np.isclose(row["net_return"], 0.0005)

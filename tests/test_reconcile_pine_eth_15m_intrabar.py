"""Focused contracts for the ETH V9 ordered 3-minute execution audit."""
from __future__ import annotations

import pandas as pd

from scripts.reconcile_pine_eth_15m_intrabar import (
    aggregate_three_to_fifteen,
    replay_trade,
)


def _subbars(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["open_time", "open", "high", "low", "close"],
    ).assign(
        open_time=lambda frame: pd.to_datetime(frame["open_time"], utc=True),
        volume=1.0,
    )


def _trade(*, reason: str, exit_time: str, exit_price: float, stop: float = 98.0) -> pd.Series:
    direction = 1.0
    gross = direction * (exit_price / 100.0 - 1.0)
    net = gross - 0.001 * (1.0 + exit_price / 100.0)
    return pd.Series(
        {
            "trade_id": "synthetic-long",
            "direction": "long",
            "entry_time": "2025-01-01T00:00:00Z",
            "exit_time": exit_time,
            "entry_price": 100.0,
            "exit_price": exit_price,
            "initial_stop_price": stop,
            "exit_reason": reason,
            "net_return": net,
        }
    )


def test_three_minute_aggregation_preserves_ordered_parent_ohlc() -> None:
    frame = _subbars(
        [
            ("2025-01-01T00:00:00Z", 100.0, 101.0, 99.0, 100.5),
            ("2025-01-01T00:03:00Z", 100.5, 103.0, 100.0, 102.0),
            ("2025-01-01T00:06:00Z", 102.0, 102.5, 98.0, 99.0),
            ("2025-01-01T00:09:00Z", 99.0, 100.0, 97.0, 98.0),
            ("2025-01-01T00:12:00Z", 98.0, 104.0, 97.5, 103.0),
        ]
    )
    parent = aggregate_three_to_fifteen(frame).iloc[0]
    assert parent["open"] == 100.0
    assert parent["high"] == 104.0
    assert parent["low"] == 97.0
    assert parent["close"] == 103.0
    assert parent["subbar_count"] == 5


def test_break_even_seen_inside_parent_activates_only_next_parent() -> None:
    frame = _subbars(
        [
            ("2025-01-01T00:00:00Z", 100.0, 100.5, 99.0, 100.0),
            ("2025-01-01T00:03:00Z", 100.0, 102.0, 100.0, 101.5),
            ("2025-01-01T00:06:00Z", 101.5, 101.6, 100.5, 101.0),
            ("2025-01-01T00:09:00Z", 101.0, 101.0, 99.0, 99.5),
            ("2025-01-01T00:12:00Z", 99.5, 100.0, 99.0, 99.8),
            ("2025-01-01T00:15:00Z", 100.3, 100.4, 99.0, 99.5),
            ("2025-01-01T00:18:00Z", 99.5, 99.7, 99.2, 99.4),
            ("2025-01-01T00:21:00Z", 99.4, 99.5, 99.0, 99.2),
            ("2025-01-01T00:24:00Z", 99.2, 99.4, 99.0, 99.1),
            ("2025-01-01T00:27:00Z", 99.1, 99.3, 99.0, 99.2),
        ]
    )
    parents = aggregate_three_to_fifteen(frame)
    result = replay_trade(
        _trade(reason="stop", exit_time="2025-01-01T00:15:00Z", exit_price=100.1),
        subbars=frame,
        parents=parents,
    )
    assert result["replay_exit_time"] == "2025-01-01T00:15:00+00:00"
    assert result["replay_exit_price"] == 100.1
    assert result["break_even_armed_at"] == "2025-01-01T00:15:00+00:00"


def test_subbar_gap_beyond_stop_fills_at_subbar_open() -> None:
    frame = _subbars(
        [
            ("2025-01-01T00:00:00Z", 100.0, 100.5, 99.0, 99.5),
            ("2025-01-01T00:03:00Z", 97.0, 97.5, 96.0, 96.5),
            ("2025-01-01T00:06:00Z", 96.5, 97.0, 96.0, 96.8),
            ("2025-01-01T00:09:00Z", 96.8, 97.0, 96.5, 96.7),
            ("2025-01-01T00:12:00Z", 96.7, 97.0, 96.5, 96.8),
        ]
    )
    result = replay_trade(
        _trade(reason="stop", exit_time="2025-01-01T00:00:00Z", exit_price=97.0),
        subbars=frame,
        parents=aggregate_three_to_fifteen(frame),
    )
    assert result["replay_exit_time"] == "2025-01-01T00:03:00+00:00"
    assert result["replay_exit_price"] == 97.0


def test_reversal_fills_before_new_parent_intrabar_path() -> None:
    frame = _subbars(
        [
            ("2025-01-01T00:00:00Z", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01T00:03:00Z", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01T00:06:00Z", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01T00:09:00Z", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01T00:12:00Z", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01T00:15:00Z", 105.0, 106.0, 90.0, 95.0),
            ("2025-01-01T00:18:00Z", 95.0, 96.0, 94.0, 95.0),
            ("2025-01-01T00:21:00Z", 95.0, 96.0, 94.0, 95.0),
            ("2025-01-01T00:24:00Z", 95.0, 96.0, 94.0, 95.0),
            ("2025-01-01T00:27:00Z", 95.0, 96.0, 94.0, 95.0),
        ]
    )
    result = replay_trade(
        _trade(reason="reverse", exit_time="2025-01-01T00:15:00Z", exit_price=105.0),
        subbars=frame,
        parents=aggregate_three_to_fifteen(frame),
    )
    assert result["replay_exit_reason"] == "reverse"
    assert result["replay_exit_price"] == 105.0
    assert result["trigger_subbar"] is None

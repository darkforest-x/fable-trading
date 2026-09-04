"""Contracts for ETHUSDT.P 15m progressive profit banking."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.research_ethusdtp_15m_progressive_scaleout import (
    CONFIG_PATH,
    resolve_progressive,
)


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=len(rows), freq="15min", tz="UTC"),
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "atr": [1.0] * len(rows),
            "trend_ma": [90.0] * len(rows),
            "segment_id": [0] * len(rows),
        }
    )


def _event() -> dict:
    return {
        "setup_id": "test",
        "signal_i": 0,
        "entry_i": 0,
        "entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
    }


def test_three_tranches_are_banked_before_residual_floor_exit() -> None:
    frame = _frame(
        [
            (100.0, 102.1, 99.0, 102.1),
            (102.1, 104.1, 101.0, 104.0),
            (104.0, 106.1, 102.0, 106.0),
            (101.0, 101.2, 100.5, 100.7),
        ]
        + [(100.7, 100.8, 100.6, 100.7)] * 92
    )
    result = resolve_progressive(
        frame,
        _event(),
        _config(),
        bank_total_fraction=0.75,
        step_atr=2.0,
    )

    assert result["partial_hits"] == 3
    assert result["remaining_fraction"] == pytest.approx(0.25)
    assert result["banked_gross_return"] == pytest.approx(0.03)
    assert result["exit_price"] == pytest.approx(100.8)
    assert result["net_return"] == pytest.approx(0.03)
    assert result["outcome"] == "banked_profit_floor_stop"


def test_stop_has_priority_when_stop_and_first_target_share_a_bar() -> None:
    frame = _frame([(100.0, 103.0, 97.0, 101.0)] + [(101.0, 101.0, 100.0, 100.5)] * 95)
    result = resolve_progressive(
        frame,
        _event(),
        _config(),
        bank_total_fraction=0.75,
        step_atr=2.0,
    )

    assert result["outcome"] == "hard_stop"
    assert result["partial_hits"] == 0
    assert result["gross_return"] == pytest.approx(-0.02)
    assert result["net_return"] == pytest.approx(-0.022)


def test_gap_through_profit_floor_is_reported_not_hidden() -> None:
    frame = _frame(
        [(100.0, 102.1, 99.0, 102.0), (99.0, 99.5, 98.5, 99.0)]
        + [(99.0, 99.0, 98.8, 99.0)] * 94
    )
    result = resolve_progressive(
        frame,
        _event(),
        _config(),
        bank_total_fraction=0.6,
        step_atr=2.0,
    )

    assert result["partial_hits"] == 1
    assert result["profit_floor_gap_breach"] is True
    assert result["exit_price"] == pytest.approx(99.0)


def test_short_side_mirrors_long_profit_floor() -> None:
    frame = _frame(
        [
            (100.0, 101.0, 97.9, 97.9),
            (97.9, 99.0, 95.9, 96.0),
            (96.0, 98.0, 93.9, 94.0),
            (99.0, 99.5, 98.5, 99.2),
        ]
        + [(99.2, 99.3, 99.1, 99.2)] * 92
    )
    frame["trend_ma"] = 110.0
    event = {**_event(), "direction": -1}
    result = resolve_progressive(
        frame,
        event,
        _config(),
        bank_total_fraction=0.75,
        step_atr=2.0,
    )

    assert result["partial_hits"] == 3
    assert result["exit_price"] == pytest.approx(99.2)
    assert result["net_return"] == pytest.approx(0.03)

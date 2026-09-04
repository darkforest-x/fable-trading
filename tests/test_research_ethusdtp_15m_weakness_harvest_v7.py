"""Contracts for causal EMA-weakness partial profit harvesting."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.research_ethusdtp_15m_weakness_harvest_v7 import (
    CONFIG_PATH,
    resolve_weakness_harvest,
)


def _event() -> dict[str, object]:
    return {
        "setup_id": "weakness-harvest",
        "signal_i": 0,
        "entry_i": 0,
        "entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
    }


def test_strong_trend_earns_slots_without_selling() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    rows = [(100.0, 112.1, 99.0, 112.0)] + [(112.0, 112.0, 100.0, 111.0)] * 95
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range(
                "2024-01-01", periods=96, freq="15min", tz="UTC"
            ),
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "atr": [1.0] * 96,
            "reference_ma": [105.0] * 96,
            "trend_ma": [90.0] * 96,
            "segment_id": [0] * 96,
        }
    )

    result = resolve_weakness_harvest(frame, _event(), config, fraction_per_stage=0.125)

    assert result["earned_slots"] == 4
    assert result["partial_hits"] == 0
    assert result["remaining_fraction"] == pytest.approx(1.0)
    assert result["final_active_stop"] == pytest.approx(98.0)


def test_weak_completed_closes_release_one_stage_per_next_open() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    rows = [
        (100.0, 108.1, 99.0, 108.0),
        (107.0, 107.0, 105.0, 106.0),
        (105.5, 106.0, 104.0, 105.0),
        (104.5, 105.0, 103.0, 104.0),
        (103.5, 104.0, 102.0, 103.0),
    ] + [(103.0, 103.0, 100.0, 101.0)] * 91
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range(
                "2024-01-01", periods=96, freq="15min", tz="UTC"
            ),
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "atr": [1.0] * 96,
            "reference_ma": [105.5] * 96,
            "trend_ma": [90.0] * 96,
            "segment_id": [0] * 96,
        }
    )

    result = resolve_weakness_harvest(frame, _event(), config, fraction_per_stage=0.125)

    assert result["earned_slots"] == 3
    assert result["partial_hits"] == 3
    assert result["remaining_fraction"] == pytest.approx(0.625)
    assert json.loads(result["release_prices_json"]) == pytest.approx(
        [104.5, 103.5, 103.0]
    )
    assert pd.isna(result["final_profit_floor"])
    assert result["final_active_stop"] == pytest.approx(98.0)

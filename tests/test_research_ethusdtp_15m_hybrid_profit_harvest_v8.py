"""Contracts for hybrid immediate/deferred profit harvesting."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.research_ethusdtp_15m_hybrid_profit_harvest_v8 import (
    CONFIG_PATH,
    resolve_hybrid_harvest,
)


def _event() -> dict[str, object]:
    return {
        "setup_id": "hybrid-harvest",
        "signal_i": 0,
        "entry_i": 0,
        "entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
    }


def test_strong_trend_banks_only_two_early_stages() -> None:
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

    result = resolve_hybrid_harvest(frame, _event(), config, fraction_per_stage=0.125)

    assert result["earned_slots"] == 4
    assert result["immediate_hits"] == 2
    assert result["weakness_hits"] == 0
    assert result["partial_hits"] == 2
    assert result["remaining_fraction"] == pytest.approx(0.75)
    assert result["final_active_stop"] == pytest.approx(98.0)


def test_late_slots_release_one_per_weak_close_without_moving_stop() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    rows = [
        (100.0, 112.1, 99.0, 112.0),
        (111.0, 111.0, 107.0, 108.0),
        (107.0, 108.0, 105.0, 106.0),
        (105.0, 106.0, 103.0, 104.0),
    ] + [(104.0, 104.0, 100.0, 101.0)] * 92
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
            "reference_ma": [109.0] * 96,
            "trend_ma": [90.0] * 96,
            "segment_id": [0] * 96,
        }
    )

    result = resolve_hybrid_harvest(frame, _event(), config, fraction_per_stage=0.125)

    assert result["earned_slots"] == 4
    assert result["immediate_hits"] == 2
    assert result["weakness_hits"] == 2
    assert result["partial_hits"] == 4
    assert result["remaining_fraction"] == pytest.approx(0.5)
    assert json.loads(result["release_prices_json"]) == pytest.approx(
        [102.0, 104.0, 107.0, 105.0]
    )
    assert result["banked_gross_return"] == pytest.approx(0.0225)
    assert pd.isna(result["final_profit_floor"])
    assert result["final_active_stop"] == pytest.approx(98.0)

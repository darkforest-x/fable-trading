"""Contracts for causal adverse-color streak harvesting."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.research_ethusdtp_15m_decay_color_harvest_v10 import (
    resolve_decay_color_harvest,
)
from scripts.research_ethusdtp_15m_streak_harvest_v11 import (
    CONFIG_PATH,
    streak_exit_frames,
)


def test_two_bar_streak_ignores_single_pullback_then_releases() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    rows = [
        (100.0, 108.1, 99.0, 108.0),
        (108.0, 109.0, 106.0, 107.0),
        (107.0, 109.0, 106.5, 108.0),
        (108.0, 108.5, 106.0, 107.0),
        (107.0, 107.5, 104.0, 105.0),
        (104.5, 106.0, 103.0, 105.5),
    ] + [(104.5, 106.0, 100.0, 105.0)] * 90
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
            "reference_ma": [50.0] * 96,
            "trend_ma": [90.0] * 96,
            "segment_id": [0] * 96,
        }
    )
    frames = streak_exit_frames(frame, 2)
    event = {
        "setup_id": "streak",
        "signal_i": 0,
        "entry_i": 0,
        "entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
    }

    result = resolve_decay_color_harvest(
        frames[1], event, config, bank_total_fraction=0.4
    )

    assert frames[1]["adverse_color_run"].iloc[:6].tolist() == [0, 1, 0, 1, 2, 0]
    assert result["partial_hits"] == 1
    assert json.loads(result["release_prices_json"]) == pytest.approx([104.5])
    assert result["remaining_fraction"] == pytest.approx(0.8)
    assert pd.isna(result["final_profit_floor"])
    assert result["final_active_stop"] == pytest.approx(98.0)

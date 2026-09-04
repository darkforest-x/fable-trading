"""Contracts for decaying adverse-color profit releases."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.research_ethusdtp_15m_color_harvest_v9 import color_exit_frame
from scripts.research_ethusdtp_15m_decay_color_harvest_v10 import (
    CONFIG_PATH,
    resolve_decay_color_harvest,
    stage_fractions,
)


def test_decay_weights_prioritize_first_release_and_sum_to_budget() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert stage_fractions(config, 0.4) == pytest.approx([0.2, 0.1, 0.05, 0.05])


def test_release_reweighting_does_not_change_stop_path() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    rows = [
        (100.0, 108.1, 99.0, 108.0),
        (108.0, 109.0, 107.0, 108.5),
        (108.5, 109.0, 106.0, 107.0),
        (106.5, 107.0, 104.0, 105.0),
        (104.5, 105.0, 102.0, 103.0),
    ] + [(103.0, 104.0, 100.0, 102.0)] * 91
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
    exit_frame = color_exit_frame(frame)
    event = {
        "setup_id": "decay-color",
        "signal_i": 0,
        "entry_i": 0,
        "entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
    }

    result = resolve_decay_color_harvest(
        exit_frame, event, config, bank_total_fraction=0.4
    )

    assert result["partial_hits"] == 3
    assert json.loads(result["release_prices_json"]) == pytest.approx(
        [106.5, 104.5, 103.0]
    )
    assert result["banked_gross_return"] == pytest.approx(0.019)
    assert result["remaining_fraction"] == pytest.approx(0.65)
    assert pd.isna(result["final_profit_floor"])
    assert result["final_active_stop"] == pytest.approx(98.0)

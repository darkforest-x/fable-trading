"""Contracts for convex partial exits without profit-triggered stop updates."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.research_ethusdtp_15m_convex_profit_ladder_v5 import (
    CONFIG_PATH,
    convex_stage_fractions,
    resolve_convex_ladder,
)


def test_convex_weights_are_backloaded_and_sum_to_fixed_bank_budget() -> None:
    fractions = convex_stage_fractions([2.0, 4.0, 6.0, 8.0], 0.4, 1.0)

    assert fractions == pytest.approx([0.04, 0.08, 0.12, 0.16])
    assert sum(fractions) == pytest.approx(0.4)


def test_profit_fills_reduce_size_without_changing_stop() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    rows = [(100.0, 108.1, 99.0, 108.0)] + [(108.0, 108.0, 100.0, 101.0)] * 95
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
            "trend_ma": [90.0] * 96,
            "segment_id": [0] * 96,
        }
    )
    event = {
        "setup_id": "convex",
        "signal_i": 0,
        "entry_i": 0,
        "entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
    }

    result = resolve_convex_ladder(frame, event, config, weight_power=1.0)

    assert result["partial_hits"] == 4
    assert result["remaining_fraction"] == pytest.approx(0.6)
    assert result["banked_gross_return"] == pytest.approx(0.024)
    assert pd.isna(result["final_profit_floor"])
    assert result["final_active_stop"] == pytest.approx(98.0)
    assert config["profit_ladder"]["stop_change_after_bank"] == "none"

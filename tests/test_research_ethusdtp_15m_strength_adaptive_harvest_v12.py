"""Contracts for causal strength-adaptive profit release sizing."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.research_ethusdtp_15m_streak_harvest_v11 import streak_exit_frames
from scripts.research_ethusdtp_15m_strength_adaptive_harvest_v12 import (
    CONFIG_PATH,
    resolve_strength_adaptive_harvest,
)


def _frame(high_at_start: float) -> pd.DataFrame:
    peak = high_at_start - 0.1
    rows = [
        (100.0, high_at_start, 99.0, peak),
        (peak, peak + 0.2, peak - 1.0, peak - 0.8),
        (peak - 0.8, peak - 0.6, peak - 1.8, peak - 1.6),
        (peak - 1.8, peak - 1.4, peak - 2.2, peak - 1.5),
    ] + [(peak - 1.6, peak - 1.2, 100.0, peak - 1.5)] * 92
    return pd.DataFrame(
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


def _event() -> dict[str, object]:
    return {
        "setup_id": "adaptive",
        "signal_i": 0,
        "entry_i": 0,
        "entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
    }


def test_observed_supertrend_discounts_release_without_moving_stop() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    frame = _frame(108.1)
    trigger = streak_exit_frames(frame, 2)[1]

    result = resolve_strength_adaptive_harvest(
        trigger, _event(), config, strong_multiplier=0.25
    )

    assert result["earned_slots"] == 3
    assert result["partial_hits"] == 1
    assert json.loads(result["release_fractions_json"]) == pytest.approx([0.05])
    assert json.loads(result["release_prices_json"]) == pytest.approx([106.2])
    assert result["remaining_fraction"] == pytest.approx(0.95)
    assert result["discounted_releases"] == 1
    assert pd.isna(result["final_profit_floor"])
    assert result["final_active_stop"] == pytest.approx(98.0)


def test_two_slot_move_keeps_normal_first_release() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    frame = _frame(104.1)
    trigger = streak_exit_frames(frame, 2)[1]

    result = resolve_strength_adaptive_harvest(
        trigger, _event(), config, strong_multiplier=0.25
    )

    assert result["earned_slots"] == 2
    assert json.loads(result["release_fractions_json"]) == pytest.approx([0.2])
    assert result["remaining_fraction"] == pytest.approx(0.8)
    assert result["discounted_releases"] == 0

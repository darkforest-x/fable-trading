"""Corrected path-metric and four-stage contracts for ETH scale-out V2."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.research_ethusdtp_15m_progressive_scaleout import resolve_progressive
from scripts.research_ethusdtp_15m_progressive_scaleout_v2 import (
    CONFIG_PATH,
    corrected_metrics,
)


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_four_stages_bank_sixty_percent_and_leave_forty_percent_runner() -> None:
    rows = [
        (100.0, 102.1, 99.0, 102.1),
        (102.0, 104.1, 101.0, 104.0),
        (104.0, 106.1, 103.0, 106.0),
        (106.0, 108.1, 105.0, 108.0),
        (101.0, 101.2, 100.4, 100.7),
    ] + [(100.7, 100.8, 100.6, 100.7)] * 91
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=96, freq="15min", tz="UTC"),
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
        "setup_id": "four-stage",
        "signal_i": 0,
        "entry_i": 0,
        "entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
    }

    result = resolve_progressive(
        frame,
        event,
        _config(),
        bank_total_fraction=0.6,
        step_atr=2.0,
    )

    assert result["partial_hits"] == 4
    assert result["remaining_fraction"] == pytest.approx(0.4)
    assert result["banked_gross_return"] == pytest.approx(0.03)
    assert result["net_return"] == pytest.approx(0.03)


def test_corrected_giveback_does_not_use_post_exit_horizon_mfe() -> None:
    events = pd.DataFrame(
        {
            "net_return": [-0.01],
            "gross_return": [-0.008],
            "net_return_r": [-0.5],
            "hold_bars": [1],
            "horizon_mfe_atr": [5.0],
            "mfe_at_exit_atr": [0.2],
            "capture_of_horizon_mfe": [-0.1],
            "gave_back_atr": [5.8],
            "partial_hits": [0],
            "runner_armed": [False],
            "banked_gross_return": [0.0],
            "profit_floor_gap_breach": [False],
        }
    )

    result = corrected_metrics(events)

    assert result["actual_mfe_2atr_events"] == 0
    assert result["runner_armed_events"] == 0
    assert result["banked_events"] == 0

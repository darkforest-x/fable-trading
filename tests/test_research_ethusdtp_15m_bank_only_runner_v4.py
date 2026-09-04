"""Contracts for banking profit without a profit-triggered stop update."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.research_ethusdtp_15m_bank_only_runner_v4 import (
    CONFIG_PATH,
    resolve_bank_only,
)


def test_partial_fills_do_not_change_the_baseline_runner_stop() -> None:
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
        "setup_id": "bank-only",
        "signal_i": 0,
        "entry_i": 0,
        "entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
    }

    result = resolve_bank_only(frame, event, config, bank_total_fraction=0.4)

    assert result["partial_hits"] == 4
    assert result["remaining_fraction"] == pytest.approx(0.6)
    assert result["final_profit_floor"] != result["final_profit_floor"]
    assert result["final_active_stop"] == pytest.approx(98.0)
    assert config["bank_only_scaleout"]["stop_change_after_bank"] == "none"

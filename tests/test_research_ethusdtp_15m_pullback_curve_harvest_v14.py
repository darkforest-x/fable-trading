"""Contracts for causal pullback-curve profit harvesting."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.research_ethusdtp_15m_pullback_curve_harvest_v14 import (
    CONFIG_PATH,
    resolve_pullback_curve_harvest,
)


def _frame() -> pd.DataFrame:
    rows = [
        (100.0, 109.0, 100.0, 109.0),
        (109.0, 109.0, 107.0, 107.0),
        (107.0, 108.0, 106.0, 106.0),
        (106.0, 107.0, 105.0, 105.0),
        (105.0, 106.0, 104.0, 104.0),
        (104.0, 105.0, 103.0, 103.0),
    ]
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame["open_time"] = pd.date_range("2024-01-01", periods=len(frame), freq="15min", tz="UTC")
    frame["atr"] = 1.0
    frame["trend_ma"] = 90.0
    frame["segment_id"] = 0
    return frame


def _config() -> dict[str, object]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config["frozen_execution"]["horizon_bars"] = 6
    return config


def _event() -> dict[str, object]:
    return {
        "setup_id": "synthetic-long",
        "signal_i": -1,
        "entry_i": 0,
        "entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
    }


def test_supertrend_banks_no_more_than_ten_percent() -> None:
    result = resolve_pullback_curve_harvest(
        _frame(), _event(), _config(), pullback_step_atr=1.0
    )

    assert result["strong_observed"] is True
    assert result["banked_fraction"] == pytest.approx(0.10)
    assert result["remaining_fraction"] == pytest.approx(0.90)
    assert json.loads(result["release_fractions_json"]) == pytest.approx([0.05, 0.05])


def test_partial_fill_uses_next_open_and_never_changes_stop() -> None:
    result = resolve_pullback_curve_harvest(
        _frame(), _event(), _config(), pullback_step_atr=1.0
    )

    assert json.loads(result["release_prices_json"])[0] == pytest.approx(107.0)
    assert result["stop_changed_by_harvest"] is False
    assert result["final_active_stop"] == pytest.approx(98.0)


def test_contract_physically_excludes_repository_holdout() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert config["source_contract"]["safe_end_exclusive"] < config["source_contract"]["holdout_start"]
    assert config["pullback_harvest"]["stop_change_after_bank"] == "none"

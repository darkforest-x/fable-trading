"""Contract tests for the repaired 5-minute MA-launch diagnostic."""
from __future__ import annotations

import pandas as pd
import pytest

from yoyo.datasets.ma_launch_5m_causal import (
    CONTRACT_VERSION,
    assert_manifest_timing,
    atr_series,
    resolve_causal_trade,
    split_from_decision_at,
    timing_from_core_end,
)


def _frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=180, freq="5min", tz="UTC"),
            "open": [100.0] * 180,
            "high": [100.5] * 180,
            "low": [99.5] * 180,
            "close": [100.0] * 180,
        }
    )
    frame["atr14"] = atr_series(frame)
    return frame


def test_timing_has_one_visible_decision_bar_and_future_starts_after_it() -> None:
    timing = timing_from_core_end(100)
    assert timing.decision_i == timing.visible_end_i == 102
    assert timing.outcome_start_i == 103


def test_manifest_timing_rejects_visible_future() -> None:
    row = {
        "core_end_i": 100,
        "decision_i": 102,
        "visible_end_i": 103,
        "outcome_start_i": 103,
        "window_end_i": 103,
        "entry_price_source": "decision_close",
        "outcome_contract": CONTRACT_VERSION,
    }
    with pytest.raises(ValueError, match="causal timing mismatch"):
        assert_manifest_timing(row)


def test_shared_trade_contract_ignores_decision_bar_extremes() -> None:
    frame = _frame()
    frame.loc[20, "high"] = 110.0
    frame.loc[21, "low"] = 97.0

    result = resolve_causal_trade(frame, decision_i=20, side="LONG", horizon_bars=144)

    assert result.outcome == "sl"
    assert result.exit_offset == 1


def test_time_split_has_a_symmetric_450_bar_purge_band() -> None:
    assert split_from_decision_at("2025-11-29T10:25:00Z") == "train"
    assert split_from_decision_at("2025-11-30T00:00:00Z") is None
    assert split_from_decision_at("2025-12-01T00:00:00Z") is None
    assert split_from_decision_at("2025-12-02T13:35:00Z") == "val"

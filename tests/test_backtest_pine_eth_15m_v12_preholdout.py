"""The V12 replay must stay isolated, 15-minute and holdout-safe."""
from __future__ import annotations

import pandas as pd

from scripts.backtest_pine_eth_15m_v12_preholdout import (
    HOLDOUT_START,
    PERIODS,
    REQUESTED_RECENT_END,
    REQUESTED_RECENT_START,
    SAFE_END,
    backtest_arms,
    build_v12_feature_frame,
)


def test_all_materialized_periods_end_before_holdout() -> None:
    assert REQUESTED_RECENT_START.isoformat() == "2026-02-21T00:00:00+00:00"
    assert REQUESTED_RECENT_END.isoformat() == "2026-08-21T00:00:00+00:00"
    assert SAFE_END < HOLDOUT_START
    assert all(period.end <= SAFE_END for period in PERIODS)


def test_optimized_arms_are_separate_change_contracts() -> None:
    arms = {arm.name: arm for arm in backtest_arms()}
    assert set(arms) == {
        "v9_frozen_baseline",
        "v12f_ma6_w8_full_gate",
        "v12e_ma6_w8_entry_only",
        "v12t_tbsl_signal_close_ticks",
    }

    full = arms["v12f_ma6_w8_full_gate"]
    assert full.signal_columns[:2] == ("v12f_long", "v12f_short")
    assert full.entry_gate_columns is None
    assert full.execution.take_profit_percent is None
    assert full.strict_single_variable

    entry = arms["v12e_ma6_w8_entry_only"]
    assert entry.signal_columns[:2] == ("v9_long", "v9_short")
    assert entry.entry_gate_columns == (
        "ma6_w8_long_pass",
        "ma6_w8_short_pass",
    )
    assert entry.execution.take_profit_percent is None
    assert entry.strict_single_variable

    tbsl = arms["v12t_tbsl_signal_close_ticks"]
    assert tbsl.signal_columns[:2] == ("v9_long", "v9_short")
    assert tbsl.entry_gate_columns is None
    assert tbsl.execution.take_profit_percent == 30.0
    assert tbsl.execution.take_profit_distance_basis == "signal_close"
    assert tbsl.params.atr_mult == 3.0
    assert tbsl.params.max_sl_percent == 3.0
    assert not tbsl.strict_single_variable


def test_python_w8_ready_gate_matches_the_slowest_sma_warmup() -> None:
    rows = 260
    raw = pd.DataFrame(
        {
            "open_time": pd.date_range("2023-01-01", periods=rows, freq="15min", tz="UTC"),
            "open": [100.0] * rows,
            "high": [100.1] * rows,
            "low": [99.9] * rows,
            "close": [100.0] * rows,
            "volume": [1.0] * rows,
        }
    )
    frame = build_v12_feature_frame(raw)
    assert not frame.loc[118, "ma6_w8_ready"]
    assert not frame.loc[118, "ma6_w8_long_pass"]
    assert not frame.loc[118, "ma6_w8_short_pass"]
    assert frame.loc[119, "ma6_w8_ready"]
    # Threshold zero deliberately passes a ready window with zero crossings.
    assert frame.loc[119, "ma6_w8_long_pass"]
    assert frame.loc[119, "ma6_w8_short_pass"]


def test_every_pine_arm_is_paper_or_frozen_and_exists() -> None:
    for arm in backtest_arms():
        assert arm.pine_path.is_file()
        pine = arm.pine_path.read_text(encoding="utf-8")
        assert "timeframe.in_seconds() != 900" in pine
        if arm.name != "v9_frozen_baseline":
            assert "PAPER ONLY" in pine

"""Tests for the ETH/XAU asset-specific K1->K2 report builder."""

from __future__ import annotations

import pandas as pd

from scripts.build_asset_specific_k1k2_15m_report import (
    SAFE_END,
    _fixed_barrier_returns,
    build_phase_summary,
)


def test_report_builder_never_crosses_repository_holdout() -> None:
    assert SAFE_END == pd.Timestamp("2026-05-01T00:00:00Z")
    assert SAFE_END < pd.Timestamp("2026-05-04T00:00:00Z")


def test_frozen_phase_summary_contains_both_assets_and_all_phases() -> None:
    summary = build_phase_summary()
    assert set(summary["asset"]) == {"ETH", "XAU"}
    assert set(summary["phase"]) == {"selection", "audit", "confirmation"}
    assert set(summary["policy"]) == {"baseline", "candidate"}
    assert len(summary) == 12
    assert summary["registered_result"].all()


def test_fixed_barrier_uses_stop_first_when_both_touch_same_bar() -> None:
    frame = pd.DataFrame(
        {
            "open": [100.0],
            "high": [103.0],
            "low": [97.0],
            "close": [101.0],
        }
    )
    setups = pd.DataFrame(
        {
            "entry_i": [0],
            "direction": [1],
            "entry_price": [100.0],
            "signal_atr": [1.0],
        }
    )
    result = _fixed_barrier_returns(
        frame,
        setups,
        stop_atr=2.0,
        target_atr=2.0,
        horizon_bars=1,
        cost_fraction=0.002,
    )
    assert len(result) == 1
    assert abs(float(result.iloc[0]) - (-0.022)) < 1e-12

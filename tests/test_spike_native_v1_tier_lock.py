"""Synthetic causal ordering contracts for native V1 tier-lock replay."""
from __future__ import annotations

import pandas as pd
import pytest

from yoyo.evaluation.spike_native_v1_tier_lock import _lock_price, _raise_stage, _tier_exit


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=len(rows), freq="1h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index).assign(atr=1.0)


def test_same_bar_stop_precedes_both_tier_triggers() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 116, 89, 100), (100, 100, 99, 100)])
    outcome = _tier_exit(bars, bars.index[0], 100, 10, 90, .01)
    assert outcome["exit_reason"] == "protective_stop"
    assert outcome["be05_trigger_count"] == outcome["tier15_trigger_count"] == 0


def test_both_tiers_crossed_together_apply_on_next_bar_and_keep_fees() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 116, 96, 110), (104, 104.5, 103, 104)])
    outcome = _tier_exit(bars, bars.index[0], 100, 10, 90, .01)
    assert outcome["be05_trigger_bar_open"] == outcome["tier15_trigger_bar_open"] == bars.index[1]
    assert (outcome["be05_trigger_count"], outcome["tier15_trigger_count"]) == (1, 1)
    assert outcome["exit_price"] == pytest.approx(104)  # next-bar gap through 105 tier protection
    assert outcome["net_return"] == pytest.approx(.038)


def test_tighter_native_protection_is_not_relaxed_by_stage() -> None:
    assert _raise_stage(108, 100, 10, .01, 2) == pytest.approx(108)
    assert _raise_stage(90, 100, 10, .01, 2) == pytest.approx(105)


def test_long_lock_rounds_down_on_the_tick_grid() -> None:
    assert _lock_price(.005751, .001, .000001, 2) == pytest.approx(.006251)

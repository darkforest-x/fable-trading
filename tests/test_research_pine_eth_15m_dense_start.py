"""The V13 dense-start selection runner must remain 15m and holdout-safe."""
from __future__ import annotations

from scripts.research_pine_eth_15m import exact_execution
from scripts.research_pine_eth_15m_dense_start import (
    HOLDOUT_START,
    LOCKED_PERIODS,
    ROUND_TRIP_COST,
    SAFE_END,
    SELECTION_PERIODS,
    load_preregistration,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import SignalParameters


def test_selection_and_locked_periods_never_reach_holdout() -> None:
    assert SAFE_END < HOLDOUT_START
    assert [period.name for period in SELECTION_PERIODS] == ["2023H1", "2023H2"]
    assert all(period.end <= SAFE_END for period in LOCKED_PERIODS)
    assert all(period.end.year == 2023 or period.start.year == 2023 for period in SELECTION_PERIODS)


def test_preregistered_profiles_are_ordered_and_fixed_to_15m() -> None:
    payload, profiles = load_preregistration()
    assert payload["scope"]["bar_minutes"] == 15
    assert payload["scope"]["holdout_rows_allowed"] == 0
    assert [profile.profile_id for profile in profiles] == [
        "dense_l0",
        "dense_l1",
        "dense_l2",
        "dense_l3",
    ]


def test_barriers_cost_and_execution_remain_frozen() -> None:
    params = SignalParameters(osc_threshold=0.1)
    execution = exact_execution(equity_frequency=None)
    assert ROUND_TRIP_COST == 0.002
    assert params.atr_mult == 4.0
    assert params.max_sl_percent == 3.0
    assert params.break_even_trigger_percent == 1.5
    assert params.break_even_offset_percent == 0.1
    assert execution.take_profit_percent is None
    assert execution.stop_distance_basis == "signal_close"
    assert execution.signal_bar_duration.total_seconds() == 900

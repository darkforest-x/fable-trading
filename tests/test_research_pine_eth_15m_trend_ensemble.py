"""The V15 trend-ensemble runner must stay 15m, causal and holdout-safe."""
from __future__ import annotations

import pandas as pd

from scripts.research_pine_eth_15m import exact_execution
from scripts.research_pine_eth_15m_dense_start import (
    HOLDOUT_START,
    LOCKED_PERIODS,
    ROUND_TRIP_COST,
    SAFE_END,
    SELECTION_PERIODS,
)
from scripts.research_pine_eth_15m_trend_ensemble import (
    add_profile_columns,
    build_path_differences,
    load_preregistration,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import SignalParameters


def test_selection_and_locked_periods_never_reach_holdout() -> None:
    assert SAFE_END < HOLDOUT_START
    assert [period.name for period in SELECTION_PERIODS] == ["2023H1", "2023H2"]
    assert all(period.end <= SAFE_END for period in LOCKED_PERIODS)


def test_profiles_and_formula_are_fixed_before_outcomes() -> None:
    payload, profiles = load_preregistration()
    assert payload["scope"]["bar_minutes"] == 15
    assert payload["scope"]["holdout_rows_allowed"] == 0
    assert payload["scope"]["training_allowed"] is False
    assert payload["feature_contract"]["ewmac_speed_pairs"] == [
        [8, 32],
        [16, 64],
        [32, 128],
    ]
    assert payload["feature_contract"]["donchian_windows"] == [24, 48, 96]
    assert [profile.profile_id for profile in profiles] == [
        "soft_l0",
        "soft_l1",
        "soft_l2",
        "soft_l3",
    ]
    assert [profile.minimum_quality for profile in profiles] == [0.45, 0.50, 0.55, 0.60]


def test_full_state_integration_filters_only_guarded_candidates() -> None:
    _, profiles = load_preregistration()
    profile = next(profile for profile in profiles if profile.profile_id == "soft_l2")
    frame = pd.DataFrame(
        {
            "entry_allowed": [True, False, True],
            "v9_long": [True, True, False],
            "v9_short": [False, False, True],
            "ma6_w8_long_pass": [True, True, False],
            "ma6_w8_short_pass": [False, False, True],
            "trend_ensemble_ready": [True, True, True],
            "trend_quality_long": [0.54, 0.10, 0.90],
            "trend_quality_short": [0.90, 0.90, 0.56],
        }
    )
    result = add_profile_columns(frame, [profile])
    # Guarded long is rejected by quality; the same raw long outside guards
    # remains present so cooldown/state behavior is frozen.
    assert result["v15e_soft_l2_long"].tolist() == [False, True, False]
    assert result["v15e_soft_l2_short"].tolist() == [False, False, True]
    assert result["v15e_soft_l2_long_pass"].tolist() == [False, False, False]
    assert result["v15e_soft_l2_short_pass"].tolist() == [False, False, True]


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


def test_path_difference_audit_uses_dynamic_signal_identity() -> None:
    rows = []
    for variant, signals in (
        ("v12f_ma6_w8_full_gate", [(10, "long"), (20, "short")]),
        ("v15e_soft_l2", [(10, "long"), (30, "short")]),
    ):
        for signal_i, direction in signals:
            rows.append(
                {
                    "variant": variant,
                    "period": "discovery_2023",
                    "signal_i": signal_i,
                    "signal_time": pd.Timestamp("2023-01-01", tz="UTC")
                    + pd.Timedelta(minutes=15 * signal_i),
                    "direction": direction,
                    "entry_time": pd.Timestamp("2023-01-01", tz="UTC"),
                    "exit_time": pd.Timestamp("2023-01-02", tz="UTC"),
                    "exit_reason": "stop",
                    "holding_bars": 2,
                    "gross_return": -0.01,
                    "project_net_return": -0.012,
                    "trend_quality": 0.5,
                    "trend_only_score": 0.5,
                    "ewmac_only_score": 0.5,
                    "donchian_only_score": 0.5,
                    "dense_only_score": 0.5,
                }
            )
    result = build_path_differences(pd.DataFrame(rows), "v15e_soft_l2")
    assert set(result["signal_i"]) == {20, 30}
    assert set(result["path_membership"]) == {"v12f_only", "v15_only"}

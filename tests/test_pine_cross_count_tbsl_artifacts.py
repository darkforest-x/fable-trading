"""Artifact invariants for the ETH 15m six-MA and staged TP/SL research run."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest


PROJECT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT / "experiments/active/exp-pine-eth-15m-v1/results"


def _payload() -> dict:
    return json.loads((RESULTS / "cross_count_tbsl_optimization.json").read_text())


def test_optimizer_never_crosses_the_development_boundary() -> None:
    payload = _payload()
    assert payload["candidate_rows"] == 335
    assert payload["data_quality"]["last_bar"] == "2024-12-31T23:45:00+00:00"
    assert payload["consumed_final_rows_used"] == 0
    assert payload["holdout_rows_read"] == 0
    assert payload["model_trained_or_scored"] is False
    assert payload["training_eligible"] is False
    assert payload["forward_eligible"] is False
    assert payload["production_eligible"] is False

    trades = pd.read_csv(RESULTS / "cross_count_tbsl_final_trades.csv")
    assert pd.to_datetime(trades["signal_time"], utc=True).max() < pd.Timestamp(
        "2025-01-01", tz="UTC"
    )


def test_true_six_ma_gate_and_runner_up_are_kept_distinct() -> None:
    selection = _payload()["selection"]["cross_gates"]["ma6_directional_pairs"]
    assert selection["best_active"] == {
        "gate_id": "ma6_directional_pairs_cross_imbalance_w16_gte_0",
        "family": "ma6_directional_pairs",
        "window": 16,
        "threshold": 0,
    }
    assert selection["runner_up_active"] == {
        "gate_id": "ma6_directional_pairs_cross_imbalance_w8_gte_0",
        "family": "ma6_directional_pairs",
        "window": 8,
        "threshold": 0,
    }
    assert selection["best_active_calibration"]["worst_2023_half_return_percent"] > (
        selection["runner_up_active_calibration"]["worst_2023_half_return_percent"]
    )


def test_six_ma_runner_up_improves_return_and_drawdown_but_is_not_production_proof() -> None:
    results = pd.DataFrame(_payload()["final_results"]).set_index(["variant", "period"])
    for period in ("2023", "2024"):
        baseline = results.loc[("baseline_v9", period)]
        runner = results.loc[("ma6_runner_up_gate_only", period)]
        assert runner["return_percent"] > baseline["return_percent"]
        assert runner["max_drawdown_15m_percent"] < baseline["max_drawdown_15m_percent"]
        assert runner["without_largest_trade_net_bp"] > 0.0
    assert results.loc[("ma6_runner_up_gate_only", "2023"), "week_block_signflip_p_one_sided"] > 0.01
    assert results.loc[("ma6_runner_up_gate_only", "2024"), "week_block_signflip_p_one_sided"] < 0.01


def test_staged_barrier_choice_preserves_long_tail_and_exposes_drawdown_tradeoff() -> None:
    payload = _payload()
    selection = payload["selection"]
    assert selection["take_profit"]["selected_percent"] == pytest.approx(30.0)
    assert selection["atr_mult"]["selected"] == pytest.approx(3.0)
    assert selection["max_sl_percent"]["selected"] == pytest.approx(3.0)

    results = pd.DataFrame(payload["final_results"]).set_index(["variant", "period"])
    for period in ("2023", "2024"):
        baseline = results.loc[("baseline_v9", period)]
        tbsl = results.loc[("tbsl_only", period)]
        assert tbsl["return_percent"] > baseline["return_percent"]
        assert tbsl["max_drawdown_15m_percent"] > baseline["max_drawdown_15m_percent"]
        assert tbsl["exit_reasons"]["take_profit"] == 2
        assert tbsl["intrabar_barrier_collisions"] == 0

    stages = pd.read_csv(RESULTS / "trend_tbsl_staged_search.csv")
    assert set(stages.loc[stages["stage"].eq("take_profit"), "config_id"]) == {
        "tp_none", "tp_2.5", "tp_5", "tp_8", "tp_12", "tp_20", "tp_30", "tp_40", "tp_50"
    }
    caps = stages.loc[
        stages["stage"].eq("max_sl_percent")
        & stages["period"].isin(["2023H1", "2023H2"])
        & stages["config_id"].isin(["cap_3", "cap_4", "cap_5", "cap_6"])
    ]
    pivot = caps.pivot(index="period", columns="config_id", values="return_percent")
    for period in pivot.index:
        assert pivot.loc[period].max() - pivot.loc[period].min() < 1e-12


def test_matched_controls_have_complete_seed_and_trade_coverage() -> None:
    payload = _payload()
    sensitivity = pd.read_csv(RESULTS / "cross_count_tbsl_control_sensitivity.csv")
    assert len(payload["final_results"]) == 10
    assert sensitivity.groupby(["arm", "period"])["control_seed"].nunique().eq(32).all()
    assert len(sensitivity) == 10 * 32

    trades = pd.read_csv(RESULTS / "cross_count_tbsl_final_trades.csv")
    controls = pd.read_csv(RESULTS / "cross_count_tbsl_final_controls.csv")
    for (arm, period), group in trades.groupby(["final_arm", "period"]):
        local = controls.loc[controls["arm"].eq(arm) & controls["period"].eq(period)]
        assert len(local) == len(group) * 3
        assert local.groupby("trade_id")["control_signal_i"].size().eq(3).all()

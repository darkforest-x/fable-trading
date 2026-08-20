#!/usr/bin/env python3
"""Read-only, dependency-light smoke audit for ETH 15m research artifacts.

This checker intentionally uses only the Python standard library, pandas, and
NumPy.  It can therefore run inside an already-local, network-disabled Docker
image when Docker Hub is unavailable.  It validates bounded artifact arithmetic
and visible failure gates; it does not read market data, rerun a backtest, or
claim that the experiment's pinned Docker recipe built successfully.
"""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"


def load_json(name: str) -> dict[str, Any]:
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def run_checks(runtime_label: str) -> dict[str, Any]:
    config = load_json("../config.json")
    summary = load_json("summary.json")
    statistics = load_json("statistical_tests.json")
    framework = load_json("backtesting_reconciliation.json")
    intrabar = load_json("intrabar_3m_reconciliation.json")
    robustness = load_json("robustness_checks.json")
    v11 = load_json("v11_long_only_summary.json")
    control_sensitivity = load_json("control_seed_sensitivity.json")
    path_risk = load_json("path_risk_bootstrap.json")
    paper_manifest = json.loads(
        (EXPERIMENT / "pine/paper_variants_manifest.json").read_text(encoding="utf-8")
    )
    judgment = load_json("pine_judgment_development_manifest.json")
    feed_sensitivity = load_json("feed_sensitivity.json")
    funding_coverage = load_json("funding_coverage_incident.json")
    exit_anatomy = load_json("exit_anatomy.json")
    backcast = load_json("backcast_2022.json")
    paper_protocol = load_json("paper_forward_protocol.json")
    actual_timeframe = load_json("actual_10m_vs_15m.json")
    regime_stability = load_json("regime_stability.json")
    judgment_feasibility = load_json("judgment_feasibility.json")
    judgment_signal = load_json("judgment_signal_audit.json")
    stateful_gate = load_json("stateful_gate_static_vs_dynamic.json")
    pine_static = load_json("pine_static_contract.json")
    docker_replay = load_json("docker_offline_replay.json")
    validation = load_json("validation.json")
    trades = pd.read_csv(RESULTS / "trades.csv")
    controls = pd.read_csv(RESULTS / "matched_controls.csv")

    final = trades.loc[
        trades["variant"].eq("v9_locked")
        & trades["split"].eq("final_preholdout_2025_202602")
    ].copy()
    gross = np.where(
        final["direction"].eq("long"),
        final["exit_price"] / final["entry_price"] - 1.0,
        1.0 - final["exit_price"] / final["entry_price"],
    )
    project_net = gross - 0.002
    control_counts = controls.groupby("trade_id")["control_signal_i"].size()
    checks = {
        "contract_eth_15m": bool(
            config["instrument"]["research_symbol"] == "ETH-USDT-SWAP"
            and config["instrument"]["bar_minutes"] == 15
        ),
        "final_trade_count_110": len(final) == 110,
        "gross_return_recomputes": bool(
            np.allclose(final["gross_return"].to_numpy(dtype=float), gross, atol=1e-12)
        ),
        "project_cost_recomputes_at_20bp": bool(
            np.allclose(
                final["project_net_return"].to_numpy(dtype=float),
                project_net,
                atol=1e-12,
            )
        ),
        "summary_expectancy_recomputes": bool(
            np.isclose(
                project_net.mean() * 10_000.0,
                summary["v9_final_preholdout"]["project_net_bp_per_trade"],
                atol=1e-10,
            )
        ),
        "controls_three_each": bool(len(control_counts) == 110 and control_counts.eq(3).all()),
        "control_starts_unique": bool(controls["control_signal_i"].is_unique),
        "framework_ledger_passed": bool(
            framework["independent_framework_reconciliation_passed"]
            and framework["exit_time_matches"] == 110
        ),
        "intrabar_exit_ledger_passed": bool(
            intrabar["same_15m_exit_parent_count"] == 110
            and intrabar["exact_exit_price_count"] == 110
        ),
        "bounded_loaders_used_zero_holdout_rows": bool(
            summary["research_data"]["holdout_rows_read"] == 0
            and intrabar["data_quality"]["holdout_rows_read"] == 0
            and robustness["holdout_rows_read"] == 0
        ),
        "statistical_failure_visible": bool(
            statistics["week_block_signflip"]["p_value"] >= 0.01
            and statistics["week_bootstrap_absolute"]["ci95_low_bp"] < 0.0
        ),
        "tail_failure_visible": statistics["profit_concentration"]["mean_without_top1_bp"] < 0.0,
        "selection_adjustment_failure_visible": (
            robustness["selection_adjusted_feature_test"]["selection_adjusted_p_value"] >= 0.01
        ),
        "v11_postselection_failure_visible": bool(
            v11["final_preholdout_was_already_consumed"] is True
            and v11["week_block_signflip"]["p_value"] >= 0.01
            and v11["profit_concentration"]["mean_without_top1_bp"] < 0.0
        ),
        "control_seed_uncertainty_visible": bool(
            control_sensitivity["assignment_seeds"] == 64
            and control_sensitivity["all_control_sets_exact"] is True
            and all(
                row["fraction_assignment_seeds_with_p_below_0p01"] == 0.0
                for row in control_sensitivity["variants"]
            )
        ),
        "path_risk_sizing_limit_visible": bool(
            path_risk["holdout_rows_read"] == 0
            and path_risk["arms"][0]["drawdown_q95_percent"]
            < path_risk["arms"][2]["drawdown_q95_percent"]
            and path_risk["arms"][0]["probability_negative_terminal"] > 0.20
        ),
        "paper_variants_remain_uncombined_and_ineligible": bool(
            paper_manifest["combined_v10_v11_generated"] is False
            and paper_manifest["tradingview_parity_passed"] is False
            and paper_manifest["production_eligible"] is False
        ),
        "judgment_bridge_prepared_without_training": bool(
            judgment["rows"] == 166
            and judgment["feature_count"] == 28
            and judgment["missing_feature_cells"] == 0
            and judgment["training_eligible"] is False
            and judgment["lr_fitted"] is False
            and judgment["lightgbm_fitted"] is False
        ),
        "feed_sensitivity_bounded_and_v10_warning_visible": bool(
            feed_sensitivity["holdout_rows_read"] == 0
            and feed_sensitivity["executed_entry_comparisons"]["V9"]["jaccard"] > 0.95
            and feed_sensitivity["executed_entry_comparisons"]["V10"]["jaccard"]
            < feed_sensitivity["executed_entry_comparisons"]["V9"]["jaccard"]
            and feed_sensitivity["tradingview_parity_passed"] is False
        ),
        "funding_unavailable_and_preview_incident_visible": bool(
            funding_coverage["overlapping_funding_records"] == 0
            and funding_coverage["funding_cost_assumed_zero"] is False
            and funding_coverage["post_holdout_rows_used_in_any_calculation"] == 0
            and funding_coverage["operational_incident"]["rows_displayed"] == 8
        ),
        "cost_underwater_break_even_visible_without_tuning": bool(
            exit_anatomy["trades"] == 110
            and exit_anatomy["barrier_parameters_changed"] is False
            and exit_anatomy["break_even_cost_semantics"]["locked_stop_project_net_bp"]
            == -10.0
        ),
        "backcast_positive_but_not_oos_or_significant": bool(
            backcast["is_out_of_sample"] is False
            and backcast["may_change_locked_candidate"] is False
            and backcast["variants"]["V9"]["summary"]["project_net_bp_per_trade"] > 0.0
            and backcast["variants"]["V9"]["week_signflip"]["p_value"] >= 0.01
        ),
        "paper_protocol_blocked_and_nonexecuting": bool(
            paper_protocol["formal_collection_started"] is False
            and paper_protocol["forward_log_written"] is False
            and paper_protocol["live_or_paper_order_sent"] is False
            and paper_protocol["blocked"] is True
        ),
        "actual_10m_warning_visible": bool(
            actual_timeframe["ten_minute_quality"]["parents_not_exactly_two_5m_bars"] == 0
            and actual_timeframe["selection_or_promotion_allowed"] is False
            and actual_timeframe["variants"]["V8_10m"]["summary"]
            ["project_net_bp_per_trade"]
            > 0.0
            and actual_timeframe["variants"]["V9_15m"]["summary"]
            ["project_net_bp_per_trade"]
            < 0.0
        ),
        "regime_stability_keeps_exact_p_failure": bool(
            regime_stability["matched_controls_exact"] is True
            and regime_stability["absolute_net_equal_block_test"]["positive_blocks"] == 7
            and regime_stability["absolute_net_equal_block_test"]["one_sided_p_value"]
            >= 0.01
        ),
        "judgment_capacity_blocks_full_model": bool(
            judgment_feasibility["overall_positive_events_per_feature"] < 1.0
            and judgment_feasibility["training_or_scoring_performed"] is False
            and judgment_feasibility["training_eligible"] is False
        ),
        "judgment_signal_audit_blocks_flexible_model": bool(
            judgment_signal["source_rows"] == 166
            and judgment_signal["holdout_rows_read"] == 0
            and judgment_signal["training_or_model_scoring_performed"] is False
            and all(
                row["top_decile_holm_p_across_four_displayed_priors"] >= 0.01
                for row in judgment_signal["fixed_prior_diagnostics"]
            )
            and judgment_signal["prequential_28_feature_selector"]["pooled_auc"] < 0.5
            and judgment_signal["prequential_28_feature_selector"]
            ["passes_directional_sanity"]
            is False
        ),
        "stateful_gate_bias_visible": bool(
            stateful_gate["static_top_decile_filtering_valid_for_l2"] is False
            and stateful_gate["final_summary"]["vol_ratio_mean8_ge1"]["entry_jaccard"]
            < 0.90
        ),
        "pine_static_contract_passes_without_compiler_claim": bool(
            pine_static["status"] == "pass"
            and pine_static["check_count"] == 25
            and pine_static["official_pine_compiler_run"] is False
            and pine_static["tradingview_parity_passed"] is False
        ),
        "offline_market_replay_exact_without_tv_claim": bool(
            docker_replay["status"] == "pass"
            and docker_replay["data_contract"]["holdout_rows_read"] == 0
            and docker_replay["ledger"]["canonical_trade_count"] == 110
            and docker_replay["ledger"]["replayed_trade_count"] == 110
            and all(docker_replay["ledger"]["exact_matches"].values())
            and all(docker_replay["ledger"]["time_matches"].values())
            and max(docker_replay["ledger"]["numeric_max_abs_error"].values()) <= 1e-10
            and docker_replay["model_training_or_scoring_performed"] is False
            and docker_replay["tradingview_parity_passed"] is False
        ),
        "tradingview_parity_false": summary["tradingview_parity_passed"] is False,
        "training_and_production_false": bool(
            config["eligibility"]["training_eligible"] is False
            and config["eligibility"]["production_eligible"] is False
        ),
        "canonical_validator_passed": validation["status"] == "pass",
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {
        "status": "pass" if not failed else "fail",
        "scope": "generated-artifact arithmetic only; no market-data or backtest rerun",
        "runtime_label": runtime_label,
        "runtime": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "generated_from_commit": summary["generated_from_commit"],
        "checks": checks,
        "failed": failed,
        "count": len(checks),
        "pinned_docker_recipe_built": False,
        "tradingview_parity_passed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-label", default="host")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run_checks(args.runtime_label)
    rendered = json.dumps(payload, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

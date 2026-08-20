#!/usr/bin/env python3
"""Independent integrity checks for the ETH perpetual 15m Pine experiment.

The validator reads only bounded generated evidence.  It recomputes ledger and
cost arithmetic, verifies exact matched-control coverage and the independent
Backtesting.py reconciliation, and checks that statistical failures remain
visible.  It never reads market rows, trains a model or touches production.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")


def close(left: pd.Series, right: pd.Series, tolerance: float = 1e-10) -> bool:
    delta = np.abs(left.to_numpy(dtype=float) - right.to_numpy(dtype=float))
    return bool(np.nanmax(delta) <= tolerance)


def _is_ancestor(commit: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=PROJECT,
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def main() -> int:
    config = json.loads((EXPERIMENT / "config.json").read_text(encoding="utf-8"))
    quality = json.loads((RESULTS / "data_quality.json").read_text(encoding="utf-8"))
    payload = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    statistics = json.loads((RESULTS / "statistical_tests.json").read_text(encoding="utf-8"))
    feature_contract = json.loads((RESULTS / "feature_contract.json").read_text(encoding="utf-8"))
    framework = json.loads((RESULTS / "backtesting_reconciliation.json").read_text(encoding="utf-8"))
    intrabar = json.loads((RESULTS / "intrabar_3m_reconciliation.json").read_text(encoding="utf-8"))
    robustness = json.loads((RESULTS / "robustness_checks.json").read_text(encoding="utf-8"))
    trades = pd.read_csv(
        RESULTS / "trades.csv",
        parse_dates=["signal_time", "entry_time", "exit_time"],
    )
    controls = pd.read_csv(
        RESULTS / "matched_controls.csv",
        parse_dates=["candidate_entry_time", "control_signal_time"],
    )
    v10_controls = pd.read_csv(
        RESULTS / "v10_matched_controls.csv",
        parse_dates=["candidate_entry_time", "control_signal_time"],
    )
    split_summary = pd.read_csv(RESULTS / "split_summary.csv")
    timeframe = pd.read_csv(RESULTS / "timeframe_rescale_ablation.csv")
    cost = pd.read_csv(RESULTS / "cost_sensitivity.csv")

    checks: dict[str, bool] = {}
    checks["contract_is_eth_15m"] = bool(
        config["instrument"]["research_symbol"] == "ETH-USDT-SWAP"
        and config["instrument"]["bar_minutes"] == 15
    )
    checks["holdout_flags_false"] = bool(
        config["eligibility"]["holdout_consumed"] is False
        and payload["holdout_consumed"] is False
        and framework["holdout_consumed"] is False
    )
    checks["safe_end_precedes_holdout"] = SAFE_END < HOLDOUT_START
    checks["loader_read_zero_holdout_rows"] = quality["holdout_rows_read"] == 0
    checks["data_is_gapless_and_valid"] = all(
        quality[key] == 0
        for key in (
            "duplicate_timestamps",
            "null_ohlcv_cells",
            "non_15m_gaps",
            "ohlc_body_violations",
        )
    )
    checks["artifact_builder_commit_is_ancestor"] = _is_ancestor(payload["generated_from_commit"])
    checks["all_trade_times_bounded"] = bool(
        trades["signal_time"].max() < SAFE_END
        and trades["entry_time"].max() < SAFE_END
        and trades["exit_time"].max() < SAFE_END
    )
    checks["entry_is_next_bar"] = bool((trades["entry_i"] == trades["signal_i"] + 1).all())
    checks["holding_bars_recompute"] = bool(
        (trades["holding_bars"] == trades["exit_i"] - trades["entry_i"]).all()
    )
    recomputed_gross = np.where(
        trades["direction"].eq("long"),
        trades["exit_price"] / trades["entry_price"] - 1.0,
        1.0 - trades["exit_price"] / trades["entry_price"],
    )
    recomputed_commission = 0.001 * (1.0 + trades["exit_price"] / trades["entry_price"])
    checks["trade_return_and_cost_recompute"] = bool(
        close(trades["gross_return"], pd.Series(recomputed_gross))
        and close(trades["project_net_return"], pd.Series(recomputed_gross - 0.002))
        and close(trades["net_return"], pd.Series(recomputed_gross - recomputed_commission))
    )

    final_v9 = trades.loc[
        (trades["variant"] == "v9_locked")
        & (trades["split"] == "final_preholdout_2025_202602")
    ]
    final_v9_summary = split_summary.loc[
        (split_summary["variant"] == "v9_locked")
        & (split_summary["period"] == "final_preholdout_2025_202602")
    ].iloc[0]
    checks["v9_summary_recomputes"] = bool(
        len(final_v9) == int(final_v9_summary["trades"]) == 110
        and np.isclose(
            final_v9["project_net_return"].mean() * 10_000.0,
            final_v9_summary["project_net_bp_per_trade"],
        )
    )

    def controls_are_exact(frame: pd.DataFrame, expected_trades: int) -> bool:
        counts = frame.groupby("trade_id")["control_signal_i"].size()
        return bool(
            len(counts) == expected_trades
            and counts.eq(3).all()
            and frame["control_signal_i"].is_unique
            and frame["control_exit_i"].lt(quality["rows_read"]).all()
            and frame["control_rank"].between(0, 2).all()
            and frame["stratum_hk_6h"].between(0, 3).all()
            and frame["stratum_atr_quintile"].between(0, 4).all()
        )

    checks["v9_controls_exact_unique_and_bounded"] = controls_are_exact(controls, 110)
    checks["v10_controls_exact_unique_and_bounded"] = controls_are_exact(v10_controls, 77)
    checks["independent_framework_ledger_passed"] = bool(
        framework["independent_framework_reconciliation_passed"]
        and framework["entry_time_intersection"] == 110
        and framework["exit_time_matches"] == 110
        and framework["max_unit_return_error_bp"] < 0.01
    )
    checks["tradingview_parity_still_false"] = framework["tradingview_parity_passed"] is False
    checks["intrabar_3m_prefix_is_bounded_and_gapless"] = bool(
        intrabar["data_quality"]["holdout_rows_read"] == 0
        and intrabar["data_quality"]["non_3m_gaps"] == 0
        and intrabar["data_quality"]["duplicate_timestamps"] == 0
        and intrabar["data_quality"]["full_file_hash_intentionally_omitted"] is True
    )
    parent = intrabar["parent_bar_reconstruction"]
    checks["intrabar_3m_exactly_reconstructs_15m_ohlc"] = bool(
        parent["joined_15m_bars"] == parent["expected_15m_bars"] == 40_704
        and parent["parents_with_exactly_five_subbars"] == 40_704
        and not any(parent["ohlc_mismatch_count"].values())
    )
    checks["intrabar_3m_exit_reconciliation_passed"] = bool(
        intrabar["canonical_trade_count"] == 110
        and intrabar["same_15m_exit_parent_count"] == 110
        and intrabar["exact_exit_price_count"] == 110
        and intrabar["maximum_absolute_net_return_delta_bp"] < 0.01
        and intrabar["tradingview_bar_magnifier_parity_passed"] is False
    )
    checks["accidental_holdout_preview_is_disclosed_but_not_used"] = bool(
        intrabar["operational_incident"]["post_holdout_rows_used_in_any_calculation"] == 0
        and "two post-holdout raw 3m rows" in intrabar["operational_incident"]["note"]
    )
    core = robustness["core_component_aggregate"]
    checks["core_ablation_is_development_only_and_nested"] = bool(
        robustness["final_preholdout_rows_read"] == 0
        and robustness["holdout_rows_read"] == 0
        and [row["component_order"] for row in core] == list(range(5))
        and core[0]["positive_blocks"] == 0
        and core[-1]["positive_blocks"] == 4
        and core[-1]["minimum_block_net_bp"] > 0.0
    )
    prequential = robustness["prequential_feature_replay"]
    adjusted = robustness["selection_adjusted_feature_test"]
    checks["feature_gate_prequential_result_keeps_failure_visible"] = bool(
        prequential["test_blocks"] == 3
        and prequential["same_feature_selected_every_step"] is True
        and prequential["positive_increment_blocks"] == 3
        and prequential["increment_exact_signflip"]["p_value"] >= 0.01
        and adjusted["candidate_gate_count_including_none"] == 18
        and adjusted["selection_adjusted_p_value"] >= 0.01
    )
    checks["l2_export_exact_and_training_blocked"] = bool(
        len(feature_contract["project_l2_features"]) == 28
        and feature_contract["training_eligible"] is False
        and feature_contract["existing_frozen_model_scored"] is False
    )
    checks["v9_statistical_failure_visible"] = bool(
        statistics["week_block_signflip"]["p_value"] >= 0.01
        and statistics["week_bootstrap_absolute"]["ci95_low_bp"] < 0.0
        and statistics["profit_concentration"]["mean_without_top1_bp"] < 0.0
    )
    checks["v10_post_selection_failure_visible"] = bool(
        statistics["v10_post_selection_hypothesis"]["week_block_signflip"]["p_value"] >= 0.01
        and "post-final-selection" in statistics["v10_post_selection_hypothesis"]["status"]
    )
    checks["wallclock_rescale_rejection_visible"] = bool(
        len(timeframe) == 4 and (timeframe["project_net_bp_per_trade"] < 0.0).sum() >= 2
    )
    official_costs = cost.loc[cost["official_cost_row"].astype(bool)]
    checks["official_cost_frozen_at_20bp"] = bool(
        len(official_costs) == 2 and official_costs["round_trip_cost_bp"].eq(20.0).all()
    )
    pine = (EXPERIMENT / "pine/allin_eth_15m_v9_research.pine").read_text(encoding="utf-8")
    checks["pine_runtime_guard_and_no_magnifier"] = bool(
        'timeframe.in_seconds() != 900' in pine
        and "use_bar_magnifier = false" in pine
        and "commission_value = 0.10" in pine
    )
    checks["not_production_or_training_eligible"] = bool(
        config["eligibility"]["training_eligible"] is False
        and config["eligibility"]["production_eligible"] is False
        and config["eligibility"]["forward_eligible"] is False
    )

    failed = sorted(name for name, passed in checks.items() if not passed)
    validation = {
        "status": "pass" if not failed else "fail",
        "checks": checks,
        "failed": failed,
        "counts": {
            "checks": len(checks),
            "trades_all_variants": int(len(trades)),
            "v9_final_trades": int(len(final_v9)),
            "v9_controls": int(len(controls)),
            "v10_controls": int(len(v10_controls)),
            "intrabar_3m_rows": int(intrabar["data_quality"]["rows"]),
        },
        "assessment": "research_candidate_with_material_statistical_caveats" if not failed else "needs_revision",
        "holdout_consumed": False,
    }
    (RESULTS / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

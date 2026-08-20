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
    docker_smoke = json.loads((RESULTS / "docker_offline_smoke.json").read_text(encoding="utf-8"))
    docker_replay = json.loads((RESULTS / "docker_offline_replay.json").read_text(encoding="utf-8"))
    v11 = json.loads((RESULTS / "v11_long_only_summary.json").read_text(encoding="utf-8"))
    control_sensitivity = json.loads(
        (RESULTS / "control_seed_sensitivity.json").read_text(encoding="utf-8")
    )
    path_risk = json.loads((RESULTS / "path_risk_bootstrap.json").read_text(encoding="utf-8"))
    paper_manifest = json.loads(
        (EXPERIMENT / "pine/paper_variants_manifest.json").read_text(encoding="utf-8")
    )
    judgment_research = json.loads(
        (RESULTS / "pine_judgment_development_manifest.json").read_text(encoding="utf-8")
    )
    feed_sensitivity = json.loads(
        (RESULTS / "feed_sensitivity.json").read_text(encoding="utf-8")
    )
    funding_coverage = json.loads(
        (RESULTS / "funding_coverage_incident.json").read_text(encoding="utf-8")
    )
    exit_anatomy = json.loads((RESULTS / "exit_anatomy.json").read_text(encoding="utf-8"))
    backcast = json.loads((RESULTS / "backcast_2022.json").read_text(encoding="utf-8"))
    paper_protocol = json.loads(
        (RESULTS / "paper_forward_protocol.json").read_text(encoding="utf-8")
    )
    actual_timeframe = json.loads(
        (RESULTS / "actual_10m_vs_15m.json").read_text(encoding="utf-8")
    )
    regime_stability = json.loads(
        (RESULTS / "regime_stability.json").read_text(encoding="utf-8")
    )
    judgment_feasibility = json.loads(
        (RESULTS / "judgment_feasibility.json").read_text(encoding="utf-8")
    )
    judgment_signal = json.loads(
        (RESULTS / "judgment_signal_audit.json").read_text(encoding="utf-8")
    )
    stateful_gate = json.loads(
        (RESULTS / "stateful_gate_static_vs_dynamic.json").read_text(encoding="utf-8")
    )
    selection_risk = json.loads(
        (RESULTS / "selection_risk_audit.json").read_text(encoding="utf-8")
    )
    density_overlap = json.loads(
        (RESULTS / "density_overlap_audit.json").read_text(encoding="utf-8")
    )
    migration_audit = json.loads(
        (RESULTS / "migration_audit.json").read_text(encoding="utf-8")
    )
    pine_static = json.loads(
        (RESULTS / "pine_static_contract.json").read_text(encoding="utf-8")
    )
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
    side_test = robustness["side_selection_test"]
    checks["long_only_side_hypothesis_is_development_selected_but_unproven"] = bool(
        side_test["selected_policy"] == "v9_long_only"
        and side_test["selected_unadjusted"]["p_value"] >= 0.01
        and side_test["selection_adjusted_p_value"] >= 0.01
        and v11["final_preholdout_was_already_consumed"] is True
        and v11["holdout_rows_read"] == 0
        and v11["final_preholdout"]["trades"] == 56
        and v11["week_block_signflip"]["p_value"] >= 0.01
        and v11["profit_concentration"]["mean_without_top1_bp"] < 0.0
        and v11["eligibility"]["paper_ab_only"] is True
    )
    control_variants = control_sensitivity["variants"]
    checks["matched_control_assignment_seed_uncertainty_is_visible"] = bool(
        control_sensitivity["assignment_seeds"] == 64
        and control_sensitivity["all_control_sets_exact"] is True
        and len(control_variants) == 3
        and all(row["assignment_seeds"] == 64 for row in control_variants)
        and all(row["fraction_assignment_seeds_with_p_below_0p01"] == 0.0 for row in control_variants)
        and next(row for row in control_variants if row["variant"] == "v9_locked")[
            "fraction_assignment_seeds_with_positive_excess"
        ] < 1.0
    )
    path_arms = {row["label"]: row for row in path_risk["arms"]}
    checks["path_risk_bootstrap_shows_sizing_reduces_dd_not_alpha_risk"] = bool(
        path_risk["resamples"] == 20_000
        and path_risk["block_weeks"] == 4
        and path_risk["holdout_rows_read"] == 0
        and len(path_arms) == 5
        and path_arms["V9 risk 0.50%"]["drawdown_q95_percent"] < 20.0
        and path_arms["V9 risk 1.00%"]["drawdown_q95_percent"] > 30.0
        and path_arms["V9 risk 0.50%"]["probability_negative_terminal"] > 0.20
    )
    v10_pine = (EXPERIMENT / "pine/allin_eth_15m_v10_volume_paper.pine").read_text(
        encoding="utf-8"
    )
    v11_pine = (EXPERIMENT / "pine/allin_eth_15m_v11_long_only_paper.pine").read_text(
        encoding="utf-8"
    )
    checks["paper_pine_variants_are_single_variable_and_fail_closed"] = bool(
        "volRatioMean8 >= VOLUME_RATIO_THRESHOLD" in v10_pine
        and v10_pine.count("volumeExpansion and") == 2
        and 'strategy.entry("Short"' not in v11_pine
        and 'strategy.close("Long", comment = "V11 short signal exits long"' in v11_pine
        and paper_manifest["combined_v10_v11_generated"] is False
        and paper_manifest["tradingview_parity_passed"] is False
        and paper_manifest["production_eligible"] is False
    )
    checks["pine_judgment_interface_is_causal_but_training_blocked"] = bool(
        judgment_research["rows"] == 166
        and judgment_research["feature_count"] == 28
        and judgment_research["missing_feature_cells"] == 0
        and judgment_research["features_available_exactly_at_entry_open"] is True
        and judgment_research["data_quality"]["consumed_final_rows_read"] == 0
        and judgment_research["data_quality"]["holdout_rows_read"] == 0
        and judgment_research["training_eligible"] is False
        and judgment_research["existing_frozen_model_scored"] is False
        and judgment_research["lr_fitted"] is False
        and judgment_research["lightgbm_fitted"] is False
    )
    feed_entries = feed_sensitivity["executed_entry_comparisons"]
    checks["nearby_feed_sensitivity_keeps_v9_but_warns_on_v10_volume"] = bool(
        feed_sensitivity["common_bars"] == 23_328
        and feed_sensitivity["holdout_rows_read"] == 0
        and feed_sensitivity["tradingview_parity_passed"] is False
        and feed_sensitivity["spot_is_perpetual_substitute"] is False
        and feed_entries["V9"]["jaccard"] > 0.95
        and feed_entries["V11"]["jaccard"] > 0.95
        and feed_entries["V10"]["jaccard"] < feed_entries["V9"]["jaccard"]
    )
    checks["funding_is_unavailable_not_silently_zero_and_preview_disclosed"] = bool(
        funding_coverage["overlapping_funding_records"] == 0
        and funding_coverage["funding_cost_assumed_zero"] is False
        and funding_coverage["funding_cost_computed"] is False
        and funding_coverage["post_holdout_rows_used_in_any_calculation"] == 0
        and funding_coverage["operational_incident"]["rows_displayed"] == 8
    )
    exit_semantics = exit_anatomy["break_even_cost_semantics"]
    checks["exit_anatomy_exposes_cost_underwater_break_even_without_tuning"] = bool(
        exit_anatomy["trades"] == 110
        and exit_anatomy["holdout_rows_read"] == 0
        and exit_anatomy["barrier_parameters_changed"] is False
        and exit_anatomy["barrier_search_performed"] is False
        and exit_semantics["configured_lock_bp"] == 10.0
        and exit_semantics["frozen_round_trip_cost_bp"] == 20.0
        and exit_semantics["locked_stop_project_net_bp"] == -10.0
        and exit_anatomy["by_exit_subtype"]["reverse"]["positive_trades"] == 11
    )
    checks["backcast_is_qualified_matched_and_cannot_promote"] = bool(
        backcast["holdout_rows_read"] == 0
        and backcast["discovery_rows_read"] == 0
        and backcast["is_out_of_sample"] is False
        and backcast["may_change_locked_candidate"] is False
        and backcast["variants"]["V9"]["summary"]["project_net_bp_per_trade"] > 0.0
        and backcast["variants"]["V9"]["week_signflip"]["p_value"] >= 0.01
        and all(
            row["matched_control"]["controls_per_trade_min"] == 3
            for row in backcast["variants"].values()
        )
    )
    checks["paper_forward_protocol_is_hashed_blocked_and_nonexecuting"] = bool(
        paper_protocol["formal_collection_started"] is False
        and paper_protocol["forward_log_written"] is False
        and paper_protocol["live_or_paper_order_sent"] is False
        and paper_protocol["blocked"] is True
        and paper_protocol["tradingview_parity_passed"] is False
        and paper_protocol["combined_v10_v11_arm_allowed"] is False
        and all(
            arm["minimum_fresh_trades_for_formal_read"] == 100
            for arm in paper_protocol["arms"].values()
        )
    )
    actual_variants = actual_timeframe["variants"]
    checks["actual_10m_short_window_warning_is_exact_and_visible"] = bool(
        actual_timeframe["ten_minute_quality"]["parents_not_exactly_two_5m_bars"] == 0
        and actual_timeframe["holdout_rows_read"] == 0
        and actual_timeframe["selection_or_promotion_allowed"] is False
        and actual_variants["V8_10m"]["summary"]["project_net_bp_per_trade"] > 0.0
        and actual_variants["V8_15m"]["summary"]["project_net_bp_per_trade"] < 0.0
        and actual_variants["V9_10m"]["summary"]["project_net_bp_per_trade"] < 0.0
        and actual_variants["V9_15m"]["summary"]["project_net_bp_per_trade"] < 0.0
        and all(row["week_signflip"]["p_value"] >= 0.01 for row in actual_variants.values())
    )
    checks["chronological_regime_dependence_and_exact_p_failure_visible"] = bool(
        regime_stability["blocks"] == 9
        and regime_stability["holdout_rows_read"] == 0
        and regime_stability["matched_controls_exact"] is True
        and regime_stability["absolute_net_equal_block_test"]["positive_blocks"] == 7
        and regime_stability["absolute_net_equal_block_test"]["one_sided_p_value"] >= 0.01
        and regime_stability["matched_excess_equal_block_test"]["one_sided_p_value"] >= 0.01
        and {row["period"] for row in regime_stability["recent_failures"]}
        == {"2025H1", "2026M1M2"}
    )
    checks["judgment_capacity_blocks_28_feature_overfit"] = bool(
        judgment_feasibility["rows"] == 166
        and judgment_feasibility["positive_events"] == 27
        and judgment_feasibility["candidate_features"] == 28
        and judgment_feasibility["overall_positive_events_per_feature"] < 1.0
        and judgment_feasibility["training_or_scoring_performed"] is False
        and judgment_feasibility["training_eligible"] is False
        and judgment_feasibility["stateful_replay_required"] is True
    )
    volume_prior = next(
        row
        for row in judgment_signal["fixed_prior_diagnostics"]
        if row["feature"] == "vol_ratio_mean8"
    )
    checks["judgment_signal_audit_rejects_flexible_model_and_keeps_volume_unproven"] = bool(
        judgment_signal["source_rows"] == 166
        and judgment_signal["consumed_final_rows_read"] == 0
        and judgment_signal["holdout_rows_read"] == 0
        and judgment_signal["training_or_model_scoring_performed"] is False
        and judgment_signal["threshold_selected_for_execution"] is False
        and len(judgment_signal["fixed_prior_diagnostics"]) == 4
        and all(
            row["auc_holm_p_across_four_displayed_priors"] >= 0.01
            and row["top_decile_holm_p_across_four_displayed_priors"] >= 0.01
            for row in judgment_signal["fixed_prior_diagnostics"]
        )
        and volume_prior["top_decile_exact_circular_shift_p"] >= 0.01
        and volume_prior["top_decile_holm_p_across_four_displayed_priors"] >= 0.01
        and judgment_signal["prequential_28_feature_selector"]["pooled_auc"] < 0.5
        and judgment_signal["prequential_28_feature_selector"]
        ["pooled_top_decile_positive_rows"]
        == 0
        and judgment_signal["prequential_28_feature_selector"]
        ["passes_directional_sanity"]
        is False
        and judgment_signal["training_eligible"] is False
        and judgment_signal["production_eligible"] is False
    )
    checks["static_gate_bias_requires_dynamic_judgment_replay"] = bool(
        stateful_gate["holdout_rows_read"] == 0
        and stateful_gate["training_or_scoring_performed"] is False
        and stateful_gate["static_top_decile_filtering_valid_for_l2"] is False
        and stateful_gate["final_summary"]["vol_ratio_mean8_ge1"]["entry_jaccard"] < 0.90
        and stateful_gate["final_summary"]["vol_ratio_mean8_ge1"]
        ["static_minus_dynamic_net_bp"]
        > 0.0
    )
    checks["known_search_budget_is_selection_adjusted_and_blocks_more_mining"] = bool(
        selection_risk["consumed_final_rows_read"] == 0
        and selection_risk["holdout_rows_read"] == 0
        and selection_risk["new_parameter_combinations_run"] == 0
        and selection_risk["barrier_or_cost_changed"] is False
        and selection_risk["raw_known_configurations"] == 65
        and selection_risk["unique_four_block_performance_paths"] == 60
        and selection_risk["exact_global_max_stat"]["selection_adjusted_p_value"]
        >= 0.01
        and selection_risk["two_by_two_rank_reversal"]["formal_pbo_claimed"] is False
        and selection_risk["training_eligible"] is False
        and selection_risk["production_eligible"] is False
    )
    checks["density_overlap_disproves_semantic_equivalence_without_new_gate"] = bool(
        density_overlap["data_quality"]["holdout_rows_read"] == 0
        and density_overlap["overall"]["trades"] == 276
        and density_overlap["overall"]["strict_overlap"] == 4
        and density_overlap["overall"]["expanded_overlap"] == 29
        and density_overlap["splits"]["final_preholdout_2025_202602"]
        ["strict_overlap"]
        == 1
        and all(
            row["circular_shift_null"]["strict"]
            ["exact_circular_shift_p_enrichment"]
            >= 0.01
            for row in density_overlap["splits"].values()
        )
        and density_overlap["training_eligible"] is False
        and density_overlap["production_eligible"] is False
    )
    checks["original_to_v9_migration_is_hashed_and_execution_safe"] = bool(
        migration_audit["status"] == "pass"
        and migration_audit["check_count"] == 16
        and not migration_audit["failed"]
        and migration_audit["source_attachment_sha256"]
        == config["source_attachment_sha256"]
        and migration_audit["market_rows_read"] == 0
        and migration_audit["holdout_rows_read"] == 0
        and migration_audit["barrier_parameters_changed"] is False
        and migration_audit["tradingview_parity_passed"] is False
        and migration_audit["production_eligible"] is False
    )
    tv_template = EXPERIMENT / "tradingview/trades_normalized.template.csv"
    checks["pine_static_contract_passes_and_tv_parity_harness_is_pending"] = bool(
        pine_static["status"] == "pass"
        and pine_static["check_count"] == 25
        and not pine_static["failed"]
        and pine_static["official_pine_compiler_run"] is False
        and pine_static["tradingview_parity_passed"] is False
        and tv_template.is_file()
        and tv_template.read_text(encoding="utf-8").startswith(
            "direction,entry_time,exit_time,entry_price,exit_price"
        )
    )
    checks["offline_docker_artifact_smoke_passed_without_overclaim"] = bool(
        docker_smoke["status"] == "pass"
        and docker_smoke["count"] == 36
        and docker_smoke["runtime_label"] == "offline-local-label-studio-image"
        and docker_smoke["pinned_docker_recipe_built"] is False
        and docker_smoke["tradingview_parity_passed"] is False
    )
    replay_errors = docker_replay["ledger"]["numeric_max_abs_error"]
    checks["offline_docker_market_replay_is_exact_without_tv_claim"] = bool(
        docker_replay["status"] == "pass"
        and docker_replay["data_contract"]["bounded_rows_read"] == 145_666
        and docker_replay["data_contract"]["holdout_rows_read"] == 0
        and docker_replay["data_contract"]["full_file_hash_intentionally_omitted"] is True
        and docker_replay["ledger"]["canonical_trade_count"] == 110
        and docker_replay["ledger"]["replayed_trade_count"] == 110
        and all(docker_replay["ledger"]["exact_matches"].values())
        and all(docker_replay["ledger"]["time_matches"].values())
        and max(replay_errors.values()) <= 1e-10
        and docker_replay["model_training_or_scoring_performed"] is False
        and docker_replay["tradingview_parity_passed"] is False
        and docker_replay["production_eligible"] is False
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

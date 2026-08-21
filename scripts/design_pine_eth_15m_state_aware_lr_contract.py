#!/usr/bin/env python3
"""Design a blocked state-aware LR interface for the ETH 15-minute Pine path.

The builder reads only compact, already committed development manifests.  It
does not read market bars, final-preholdout or repository holdout, materialize
future labels, fit a scaler/model, choose features or thresholds, or run a
strategy.  Its purpose is to make the future policy boundary explicit while
P0/P1 and sample-size gates remain closed.

Phase A is intentionally entry-only: an opposite signal keeps V9's unconditional
close semantics and LR may decide only whether to reopen in the new direction.
Same-side signals and cooldown consumption remain deterministic.  A future
close-versus-hold model is a separate Phase B experiment because it needs paired
counterfactual labels, not the entry label reused under a different name.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
BASE_EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
BASE_RESULTS = BASE_EXPERIMENT / "results"
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-state-lr-contract-v1"
RESULTS = EXPERIMENT / "results"
OUTPUT = RESULTS / "state_aware_lr_contract_v1.json"
SURFACE_MANIFEST = BASE_RESULTS / "judgment_gate_surface_manifest.json"
REPLAY_AUDIT = BASE_RESULTS / "judgment_gate_replay_contract.json"
FEASIBILITY = BASE_RESULTS / "judgment_feasibility.json"
PATH_EFFICIENCY = (
    PROJECT
    / "experiments/active/exp-pine-eth-15m-path-efficiency-v1/results/path_efficiency_diagnostic.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_contract() -> dict[str, Any]:
    surface = _load(SURFACE_MANIFEST)
    replay = _load(REPLAY_AUDIT)
    feasibility = _load(FEASIBILITY)
    path_efficiency = _load(PATH_EFFICIENCY)
    if int(surface["rows"]) != 335 or int(surface["feature_count"]) != 28:
        raise RuntimeError("state-aware contract requires the frozen 335x28 base surface")
    if surface["labels_present"] or surface["scores_present"]:
        raise RuntimeError("base candidate surface unexpectedly contains labels or scores")
    if surface["holdout_rows_read"] or surface["consumed_final_rows_read"]:
        raise RuntimeError("base candidate surface crossed a protected boundary")
    if replay["status"] != "pass" or not replay["checks"][
        "allow_all_replays_both_split_ledgers_exactly"
    ]:
        raise RuntimeError("static entry gate identity replay is not proven")
    if replay["model_trained"] or replay["threshold_selected"]:
        raise RuntimeError("replay audit unexpectedly trained or selected a model")
    on_policy_rows = int(feasibility["rows"])
    positive_rows = int(feasibility["positive_events"])
    path_auc = float(path_efficiency["on_policy_diagnostic"]["auc_net_positive"])
    if path_efficiency["training_eligible"] or path_efficiency["model_fitted"]:
        raise RuntimeError("rejected path-efficiency artifact became eligible")

    return {
        "schema_version": "pine-eth-15m-state-aware-lr-v1",
        "artifact": "blocked interface for a future state-conditioned entry LR",
        "status": "interface_only_p0_p1_and_sample_size_blocked",
        "data_boundary": {
            "source_surface": str(SURFACE_MANIFEST.relative_to(PROJECT)),
            "source_surface_sha256": sha256_file(SURFACE_MANIFEST),
            "raw_guarded_candidates": int(surface["rows"]),
            "raw_long_candidates": int(surface["long_rows"]),
            "raw_short_candidates": int(surface["short_rows"]),
            "base_market_feature_count": int(surface["feature_count"]),
            "baseline_on_policy_rows": on_policy_rows,
            "baseline_net_positive_rows": positive_rows,
            "baseline_coverage_of_raw_surface": float(
                surface["executed_coverage"]["baseline_coverage_of_raw_surface"]
            ),
            "consumed_final_rows_read": 0,
            "holdout_rows_read": 0,
        },
        "why_static_scores_are_insufficient": (
            "A prior gate changes later position, stop, cooldown and equity state. The 335 "
            "market-feature rows are static, but every state feature and action event must be "
            "recomputed online on the policy path being replayed."
        ),
        "runtime_order_at_signal_bar_t": [
            "execute any order submitted by t-1 at open[t]",
            "resolve stop/target on bar t",
            "apply confirmed-bar break-even update for t+1",
            "detect the raw guarded candidate and snapshot causal pre-signal state at close[t]",
            "verify one finite score for every raw candidate no later than open[t+1]",
            "consume cooldown or apply the scored entry permission without suppressing an opposite close",
        ],
        "score_coverage_contract": {
            "required_rows": int(surface["rows"]),
            "required_long_rows": int(surface["long_rows"]),
            "required_short_rows": int(surface["short_rows"]),
            "one_finite_timely_score_per_raw_candidate": True,
            "baseline_executed_rows_only_allowed": False,
            "missing_duplicate_late_or_non_finite": "fail closed",
        },
        "action_contexts": {
            "flat_open": {
                "score_logged": True,
                "model_action_applied": True,
                "allowed_actions": ["open", "stay_flat"],
            },
            "opposite_reopen": {
                "score_logged": True,
                "model_action_applied": True,
                "baseline_close_remains_unconditional": True,
                "allowed_actions_after_close": ["reopen_new_side", "stay_flat"],
            },
            "same_side_noop": {
                "score_logged": True,
                "model_action_applied": False,
                "action": "preserve_current_position",
            },
            "cooldown_consume": {
                "score_logged": True,
                "model_action_applied": False,
                "action": "decrement_trades_to_skip_exactly_once",
            },
            "calendar_or_volatility_reject": {
                "score_logged": False,
                "model_action_applied": False,
                "action": "preserve_existing_V9_state_transition_order",
            },
        },
        "exit_reason_contract": {
            "reverse": (
                "an accepted opposite signal closes the current position and "
                "reopens the new side at the same next-open event"
            ),
            "opposite_signal_close_only": (
                "an accepted opposite signal closes the current position but "
                "does not reopen from that same signal"
            ),
            "labels_may_merge_these_reasons": False,
        },
        "online_state_context": {
            "computed_inside_each_dynamic_replay": True,
            "right_edge": "confirmed signal bar t close",
            "fields_logged_but_not_automatically_selected": [
                "policy_path_hash",
                "position_relation_to_signal",
                "current_position_side",
                "position_age_bars",
                "unrealized_gross_return_at_t_close",
                "stop_stage_none_initial_or_be_offset",
                "distance_to_active_stop_atr",
                "trades_to_skip_before_signal",
                "equity_fraction_of_initial",
            ],
            "action_event_identity": [
                "candidate_id",
                "policy_path_hash",
                "state_before_hash",
                "action_context",
            ],
            "score_availability": "after close[t] and no later than open[t+1]",
            "static_baseline_state_reuse_allowed": False,
        },
        "future_label_vocabulary": {
            "status": "vocabulary_only_not_materialized_or_selected",
            "outcome_events": [
                "target",
                "initial_stop",
                "break_even_stop",
                "opposite_signal_exit",
                "period_timeout",
                "intrabar_ambiguous",
                "censored",
            ],
            "continuous_target": "project_net_return_after_exact_total_cost",
            "label_end": "exit_time plus one 15m bar for conservative purging",
            "required_provenance_fields": [
                "label_available_at",
                "resolution_source",
                "tick_rounding_contract_sha256",
                "target_stop_basis_contract_sha256",
            ],
            "ambiguous_and_censored_are_nullable_not_negative": True,
            "first_touch_plus_1p5_is_trade_win": False,
            "ambiguous_15m_double_touch": "resolve with ordered 3m path or exclude with reason",
            "target_selected": False,
        },
        "phase_a_model_contract": {
            "scope": "entry permission for flat_open and opposite_reopen only",
            "model_family": "L2-regularized LogisticRegression",
            "standardization": "fit StandardScaler on each training fold only",
            "penalty": "l2",
            "solver": "liblinear",
            "class_weight": None,
            "regularization_strength_C": None,
            "market_feature_selection": None,
            "initial_max_effective_coefficients": 3,
            "required_context_indicator": "is_opposite_reopen",
            "path_efficiency_feature_allowed": False,
            "path_efficiency_rejection_evidence": {
                "auc": path_auc,
                "top_decile_permutation_p": float(
                    path_efficiency["on_policy_diagnostic"][
                        "top_decile_permutation_p_one_sided"
                    ]
                ),
            },
            "full_28_feature_lightgbm_allowed": False,
            "probability_threshold": None,
            "threshold_selected": False,
            "model_fitted": False,
        },
        "phase_b_close_policy": {
            "status": "no_go_until_separate_counterfactual_contract",
            "reason": (
                "Close-versus-hold changes the current trade path and cannot reuse an entry "
                "label. It needs paired keep/close counterfactual utilities, separate owner "
                "approval and a new experiment."
            ),
        },
        "time_split_and_capacity_gates": {
            "random_split_allowed": False,
            "expanding_time_folds_only": True,
            "purge_train_rows_whose_label_end_overlaps_validation": True,
            "threshold_uses_evaluation_outcomes": False,
            "current_positive_rows": positive_rows,
            "current_validation_positive_range": "4-8 per fold",
            "minimum_train_positives_per_effective_coefficient": 10,
            "minimum_validation_positives_per_fold": 20,
            "current_capacity_passed": False,
        },
        "dynamic_replay_acceptance": {
            "allow_all": "exact V9 ledger identity",
            "allow_none_flat": "no flat entries",
            "allow_none_opposite": "close opposite at next open but do not reopen",
            "score_coverage": "exactly one finite timely score for all 335 raw candidates",
            "missing_duplicate_early_late_or_hash_mismatch": "fail closed",
            "same_side_cooldown_stop_be_reverse_and_cost_semantics": "unchanged",
            "evaluation": [
                "full dynamic trade/equity path",
                "venue-exact net expectancy",
                "matched random control",
                "week-block inference",
                "leave-largest-winner-out",
            ],
        },
        "gates_before_any_fit": [
            "P0 semantic stability passed",
            "P1 Gold Dataset passed and relevant rows explicitly training_eligible",
            "label target and feature subset preregistered before outcomes",
            "capacity gates pass in every time fold",
            "owner explicitly approves the one model experiment",
        ],
        "gates_before_forward_use": [
            "all fit gates passed",
            "dynamic replay acceptance passed",
            "V9/V12F TradingView parity passed on exact venue",
            "new prospective paper protocol owner-approved and activated",
        ],
        "lr_fitted": False,
        "lightgbm_fitted": False,
        "threshold_selected": False,
        "labels_materialized": False,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
    }


def main() -> None:
    payload = build_contract()
    if any(
        payload[field]
        for field in (
            "lr_fitted",
            "lightgbm_fitted",
            "threshold_selected",
            "labels_materialized",
            "training_eligible",
            "forward_eligible",
            "production_eligible",
        )
    ):
        raise RuntimeError("state-aware LR interface must remain ineligible and unfitted")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()

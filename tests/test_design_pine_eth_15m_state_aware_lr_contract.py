"""Tests for the blocked state-aware Pine LR interface."""
from __future__ import annotations

from scripts.design_pine_eth_15m_state_aware_lr_contract import build_contract


def test_contract_is_online_state_aware_but_not_trainable() -> None:
    payload = build_contract()
    assert payload["data_boundary"]["raw_guarded_candidates"] == 335
    assert payload["data_boundary"]["base_market_feature_count"] == 28
    assert payload["data_boundary"]["raw_long_candidates"] == 170
    assert payload["data_boundary"]["raw_short_candidates"] == 165
    assert payload["data_boundary"]["holdout_rows_read"] == 0
    assert payload["online_state_context"]["computed_inside_each_dynamic_replay"] is True
    assert payload["online_state_context"]["static_baseline_state_reuse_allowed"] is False
    assert payload["lr_fitted"] is False
    assert payload["labels_materialized"] is False
    assert payload["training_eligible"] is False
    assert payload["forward_eligible"] is False
    assert payload["production_eligible"] is False


def test_phase_a_controls_entry_but_never_suppresses_baseline_close() -> None:
    payload = build_contract()
    contexts = payload["action_contexts"]
    assert contexts["flat_open"]["score_logged"] is True
    assert contexts["flat_open"]["model_action_applied"] is True
    assert contexts["opposite_reopen"]["score_logged"] is True
    assert contexts["opposite_reopen"]["model_action_applied"] is True
    assert contexts["opposite_reopen"]["baseline_close_remains_unconditional"] is True
    assert contexts["same_side_noop"]["score_logged"] is True
    assert contexts["same_side_noop"]["model_action_applied"] is False
    assert contexts["cooldown_consume"]["score_logged"] is True
    assert contexts["cooldown_consume"]["model_action_applied"] is False
    assert payload["phase_b_close_policy"]["status"].startswith("no_go")


def test_failed_path_factor_and_full_lightgbm_cannot_sneak_into_phase_a() -> None:
    payload = build_contract()
    model = payload["phase_a_model_contract"]
    assert model["model_family"] == "L2-regularized LogisticRegression"
    assert model["path_efficiency_feature_allowed"] is False
    assert 0.49 < model["path_efficiency_rejection_evidence"]["auc"] < 0.51
    assert model["full_28_feature_lightgbm_allowed"] is False
    assert model["market_feature_selection"] is None
    assert model["regularization_strength_C"] is None
    assert model["probability_threshold"] is None


def test_label_and_capacity_gates_fail_closed() -> None:
    payload = build_contract()
    labels = payload["future_label_vocabulary"]
    assert labels["target_selected"] is False
    assert labels["first_touch_plus_1p5_is_trade_win"] is False
    assert labels["ambiguous_and_censored_are_nullable_not_negative"] is True
    capacity = payload["time_split_and_capacity_gates"]
    assert capacity["random_split_allowed"] is False
    assert capacity["current_capacity_passed"] is False
    assert capacity["minimum_validation_positives_per_fold"] == 20
    assert payload["dynamic_replay_acceptance"]["allow_all"] == "exact V9 ledger identity"
    coverage = payload["score_coverage_contract"]
    assert coverage["required_rows"] == 335
    assert coverage["required_long_rows"] == 170
    assert coverage["required_short_rows"] == 165
    assert coverage["baseline_executed_rows_only_allowed"] is False

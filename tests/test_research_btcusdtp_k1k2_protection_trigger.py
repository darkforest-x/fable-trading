from __future__ import annotations

from scripts.research_btcusdtp_k1k2_protection_trigger import (
    DISABLED_TRIGGER_R,
    effective_trigger_r,
    select_trigger,
)


def test_disabled_trigger_maps_to_unreachable_finite_value() -> None:
    assert effective_trigger_r({"trigger_r": None}) == DISABLED_TRIGGER_R
    assert effective_trigger_r({"trigger_r": 1.25}) == 1.25


def test_selector_applies_improvement_and_worst_fold_guard() -> None:
    baseline = {
        "protection_arm": "trigger_1.50r",
        "eligible": True,
        "robust_score_bp": -10.0,
        "worst_fold_net_bp": -15.0,
        "events": 300,
        "distance_from_1.50r": 0.0,
        "protection_disabled": False,
    }
    passing = {
        "protection_arm": "trigger_2.00r",
        "eligible": True,
        "robust_score_bp": -7.5,
        "worst_fold_net_bp": -17.0,
        "events": 300,
        "distance_from_1.50r": 0.5,
        "protection_disabled": False,
    }
    rejected_worst = {
        "protection_arm": "protection_disabled",
        "eligible": True,
        "robust_score_bp": 2.0,
        "worst_fold_net_bp": -19.0,
        "events": 300,
        "distance_from_1.50r": DISABLED_TRIGGER_R,
        "protection_disabled": True,
    }
    chosen, reason = select_trigger([baseline, passing, rejected_worst], baseline)
    assert chosen["protection_arm"] == "trigger_2.00r"
    assert reason == "move_by_preregistered_rule"

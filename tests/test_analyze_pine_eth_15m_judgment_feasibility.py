"""Capacity audit must block an underpowered Pine judgment model."""
import json

from scripts.analyze_pine_eth_15m_judgment_feasibility import OUTPUT


def test_judgment_capacity_audit_fits_and_scores_nothing() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["holdout_rows_read"] == 0
    assert payload["consumed_final_rows_read"] == 0
    assert payload["training_or_scoring_performed"] is False
    assert payload["training_eligible"] is False
    assert payload["old_project_model_reusable"] is False
    assert payload["stateful_replay_required"] is True


def test_28_feature_model_is_explicitly_data_starved() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["rows"] == 166
    assert payload["positive_events"] == 27
    assert payload["candidate_features"] == 28
    assert payload["overall_positive_events_per_feature"] < 1.0
    assert max(row["validation_positive_events"] for row in payload["fold_capacity"]) == 8
    full_model = [
        row
        for row in payload["planning_scenarios"]
        if row["effective_features"] == 28
    ]
    assert full_model
    assert not any(row["current_rows_meet_scenario"] for row in full_model)
    one_feature = [
        row
        for row in payload["planning_scenarios"]
        if row["effective_features"] == 1
        and row["planning_events_per_feature"] == 10
    ][0]
    assert one_feature["current_rows_meet_scenario"] is True

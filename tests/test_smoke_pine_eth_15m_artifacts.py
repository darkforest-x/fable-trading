"""Dependency-light artifact smoke must preserve both passes and failures."""
from scripts.smoke_pine_eth_15m_artifacts import run_checks


def test_artifact_smoke_recomputes_accounting_without_overclaiming() -> None:
    payload = run_checks("pytest")
    assert payload["status"] == "pass"
    assert payload["count"] == 38
    assert payload["checks"]["offline_market_replay_exact_without_tv_claim"] is True
    assert payload["checks"]["judgment_signal_audit_blocks_flexible_model"] is True
    assert payload["checks"]["selection_budget_blocks_more_development_mining"] is True
    assert payload["checks"]["density_overlap_keeps_pine_and_project_semantics_distinct"] is True
    assert payload["checks"]["migration_audit_hashes_original_and_keeps_limits_visible"] is True
    assert payload["checks"]["complete_gate_surface_blocks_static_executed_ledger_filtering"] is True
    assert payload["checks"]["dynamic_gate_contract_is_exact_and_fail_closed"] is True
    assert payload["checks"]["summary_expectancy_recomputes"] is True
    assert payload["checks"]["statistical_failure_visible"] is True
    assert payload["pinned_docker_recipe_built"] is False
    assert payload["tradingview_parity_passed"] is False

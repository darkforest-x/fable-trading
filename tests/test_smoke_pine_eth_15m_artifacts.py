"""Dependency-light artifact smoke must preserve both passes and failures."""
from scripts.smoke_pine_eth_15m_artifacts import run_checks


def test_artifact_smoke_recomputes_accounting_without_overclaiming() -> None:
    payload = run_checks("pytest")
    assert payload["status"] == "pass"
    assert payload["count"] == 32
    assert payload["checks"]["offline_market_replay_exact_without_tv_claim"] is True
    assert payload["checks"]["summary_expectancy_recomputes"] is True
    assert payload["checks"]["statistical_failure_visible"] is True
    assert payload["pinned_docker_recipe_built"] is False
    assert payload["tradingview_parity_passed"] is False

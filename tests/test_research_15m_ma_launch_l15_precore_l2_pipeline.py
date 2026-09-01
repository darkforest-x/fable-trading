"""Safety contract tests for the leakage-resistant pre-core L1.5 experiment."""

from __future__ import annotations

from scripts.research_15m_ma_launch_l15_precore_l2_pipeline import (
    EXPERIMENT_ID,
    GLOBAL_PRECORE_FEATURE_COLUMNS,
    load_preregistration,
)


def test_v2_preregistration_physically_excludes_confirmation_move() -> None:
    prereg = load_preregistration()
    assert prereg["experiment_id"] == EXPERIMENT_ID
    assert prereg["l15"]["right_edge"] == "mapped core_end_i"
    assert prereg["l15"]["post_core_bars_visible"] == 0
    assert "confirmation_bars" not in GLOBAL_PRECORE_FEATURE_COLUMNS
    assert "aligned_core_to_decision_atr" not in GLOBAL_PRECORE_FEATURE_COLUMNS
    assert tuple(prereg["l15"]["feature_columns"]) == GLOBAL_PRECORE_FEATURE_COLUMNS
    assert prereg["safety"]["holdout_read"] is False
    assert prereg["safety"]["promote"] is False
    assert prereg["outcome"]["changed"] is False


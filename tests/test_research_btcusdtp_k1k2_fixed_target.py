from __future__ import annotations

import pytest

from scripts.research_btcusdtp_k1k2_fixed_target import select_target


def row(target_r: float, robust: float, worst: float, events: int = 300) -> dict:
    return {
        "target_r": target_r,
        "eligible": True,
        "robust_score_bp": robust,
        "worst_fold_net_bp": worst,
        "events": events,
    }


def test_selector_requires_registered_robust_improvement() -> None:
    baseline = row(3.0, -10.0, -12.0)
    selected, reason = select_target(
        [baseline, row(5.0, -8.01, -12.0)], baseline
    )
    assert selected["target_r"] == pytest.approx(3.0)
    assert reason.startswith("retain")


def test_selector_enforces_worst_fold_guard() -> None:
    baseline = row(3.0, -10.0, -12.0)
    selected, reason = select_target(
        [baseline, row(5.0, -7.0, -15.01)], baseline
    )
    assert selected["target_r"] == pytest.approx(3.0)
    assert reason.startswith("retain")


def test_selector_uses_best_passing_target() -> None:
    baseline = row(3.0, -10.0, -12.0)
    selected, reason = select_target(
        [baseline, row(4.0, -7.5, -14.0), row(5.0, -6.0, -14.5)], baseline
    )
    assert selected["target_r"] == pytest.approx(5.0)
    assert reason == "move_by_preregistered_rule"

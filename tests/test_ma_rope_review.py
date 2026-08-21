"""Tests for deterministic rope-review calibration and countercheck logic."""

from __future__ import annotations

import pytest

from yoyo.datasets.ma_rope_review import (
    evaluate_countercheck,
    lower_quantile,
    tier_for_score,
    wilson_interval,
    yolo_iou,
)


def test_lower_quantile_and_tiers_are_deterministic() -> None:
    values = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert lower_quantile(values, 0.10) == pytest.approx(0.1)
    assert lower_quantile(values, 0.50) == pytest.approx(0.3)
    assert tier_for_score(0.31, core_threshold=0.3, broad_threshold=0.1) == "A_CORE"
    assert tier_for_score(0.2, core_threshold=0.3, broad_threshold=0.1) == "B_BROAD"
    assert tier_for_score(0.09, core_threshold=0.3, broad_threshold=0.1) == "C_REST"


def test_yolo_iou_identity_and_disjoint() -> None:
    box = (0.5, 0.5, 0.2, 0.2)
    assert yolo_iou(box, box) == pytest.approx(1.0)
    assert yolo_iou(box, (0.9, 0.9, 0.1, 0.1)) == 0.0


def test_wilson_interval_contains_observed_proportion() -> None:
    low, high = wilson_interval(20, 100)
    assert low < 0.2 < high


def test_countercheck_rejects_uninformative_ranking(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep/drop have identical score distributions.  Patch the fixed population
    # constant only for this small unit test; production still requires 390.
    import yoyo.datasets.ma_rope_review as module

    monkeypatch.setattr(module, "EXPECTED_COUNTERCHECK_REVIEWED", 20)
    monkeypatch.setattr(module, "PERMUTATIONS", 100)
    rows = []
    for i in range(20):
        rows.append(
            {
                "review_status": "keep" if i % 2 == 0 else "drop",
                "rope_score": 0.1 + 0.01 * (i // 2),
            }
        )
    result = evaluate_countercheck(rows, core_threshold=0.15, broad_threshold=0.11)
    assert result["auc"] == pytest.approx(0.5)
    assert result["auto_filter_supported"] is False
    assert "do not auto-delete" in result["verdict"]

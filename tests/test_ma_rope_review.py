"""Tests for deterministic rope-review calibration and countercheck logic."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from yoyo.artifacts import load_registries
from yoyo.datasets.ma_rope_review import (
    evaluate_countercheck,
    lower_quantile,
    render_page,
    tier_for_score,
    wilson_interval,
    yolo_iou,
)


PROJECT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def test_render_page_uses_population_specific_identity() -> None:
    page = render_page(
        [],
        "storage-key",
        pack_id="population-specific-pack",
        title="测试标题",
        contract="测试合同",
    )
    assert "测试标题" in page
    assert "测试合同" in page
    assert 'packId="population-specific-pack"' in page
    assert "__TITLE__" not in page


def test_formal_rope_registry_points_to_exact_manifest() -> None:
    registries = load_registries(root=PROJECT)
    artifact = registries.artifact("owner-ma-rope-prefilter-v1")
    path = PROJECT / artifact.source_path
    assert path.is_file()
    assert artifact.sha256 == _sha(path)
    assert artifact.size_bytes == path.stat().st_size
    assert artifact.training_eligible is False
    assert artifact.production_eligible is False

    experiment = next(
        row
        for row in registries.experiments
        if row.experiment_id == "exp-p1-owner-ma-rope-prefilter-v1"
    )
    assert experiment.status == "rejected"
    assert experiment.artifacts == ["owner-ma-rope-prefilter-v1"]
    assert experiment.holdout_consumed is False
    assert experiment.training_eligible is False
    assert experiment.production_eligible is False

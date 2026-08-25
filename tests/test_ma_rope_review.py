"""Tests for deterministic rope-review calibration and countercheck logic."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.artifacts import load_registries
from yoyo.datasets.ma_rope_review import (
    RopeReviewBuildError,
    attach_focus_geometry,
    evaluate_countercheck,
    lower_quantile,
    render_page,
    summarize_answers,
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
    assert '<option value="ALL" selected>全部方向</option>' in page
    assert '<canvas id="chart"' in page
    assert "绿色框附近 · 自动放大" in page
    assert "完整原图" not in page
    assert "原尺寸" not in page
    assert "__TITLE__" not in page


def test_render_page_can_default_to_owner_long_without_dropping_other_sides() -> None:
    page = render_page(
        [{"review_id": "r1", "sample_id": "s1", "side": "long"}],
        "storage-key",
        pack_id="owner-direction-pack",
        title="方向复核",
        contract="测试合同",
        default_side="long",
    )
    assert '<option value="long" selected>只看 long</option>' in page
    assert '<option value="short" >只看 short</option>' in page
    assert "sideFilter.value!=='ALL'&&x.side!==sideFilter.value" in page


def test_attach_focus_geometry_uses_original_owner_box_coordinates() -> None:
    metadata = pd.DataFrame(
        [
            {
                "box_id": "s1",
                "yolo_xc": 0.4,
                "yolo_yc": 0.3,
                "yolo_w": 0.05,
                "yolo_h": 0.1,
            }
        ]
    ).set_index("box_id", drop=False)
    rows = attach_focus_geometry([{"sample_id": "s1", "score": 0.9}], metadata)
    assert rows == [
        {
            "sample_id": "s1",
            "score": 0.9,
            "focus_x": 0.4,
            "focus_y": 0.3,
            "focus_w": 0.05,
            "focus_h": 0.1,
        }
    ]


def test_attach_focus_geometry_fails_closed_on_missing_sample() -> None:
    metadata = pd.DataFrame(
        columns=["box_id", "yolo_xc", "yolo_yc", "yolo_w", "yolo_h"]
    ).set_index("box_id", drop=False)
    with pytest.raises(RopeReviewBuildError, match="missing review focus geometry"):
        attach_focus_geometry([{"sample_id": "missing"}], metadata)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _answer_join_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, Path]:
    import yoyo.datasets.ma_rope_review as module

    monkeypatch.setattr(module, "EXPECTED_POSITIVES", 2)
    pack = tmp_path / "pack"
    source_pack = tmp_path / "source_pack"
    positive_manifest = tmp_path / "positive_manifest.jsonl"
    public_items = [
        {"review_id": "r1", "sample_id": "s1", "tier": "A_CORE", "score": 0.9},
        {"review_id": "r2", "sample_id": "s2", "tier": "B_BROAD", "score": 0.5},
    ]
    positives = [
        {
            "sample_id": "s1",
            "split": "train",
            "image_sha256": "image-1",
            "label_sha256": "label-1",
            "production_eligible": False,
        },
        {
            "sample_id": "s2",
            "split": "val",
            "image_sha256": "image-2",
            "label_sha256": "label-2",
            "production_eligible": False,
        },
    ]
    truth = [
        {
            "review_id": "r1",
            "sample_id": "s1",
            "training_image_sha256": "image-1",
            "training_label_sha256": "label-1",
        },
        {
            "review_id": "r2",
            "sample_id": "s2",
            "training_image_sha256": "image-2",
            "training_label_sha256": "label-2",
        },
    ]
    scores = [
        {"review_id": "r1", "sample_id": "s1"},
        {"review_id": "r2", "sample_id": "s2"},
    ]
    public_manifest = pack / "public" / "manifest.json"
    source_truth = source_pack / "admin" / "truth.jsonl"
    score_path = pack / "admin" / "positive_1345_scores.jsonl"
    _write_json(public_manifest, {"pack_id": module.PACK_ID, "items": public_items})
    _write_jsonl(positive_manifest, positives)
    _write_jsonl(source_truth, truth)
    _write_jsonl(score_path, scores)
    _write_json(
        pack / "build_summary.json",
        {
            "public_manifest_sha256": _sha(public_manifest),
            "positive_manifest_sha256": _sha(positive_manifest),
            "source_pack_truth_sha256": _sha(source_truth),
            "positive_scores_sha256": _sha(score_path),
        },
    )
    answers = tmp_path / "answers.json"
    _write_json(
        answers,
        {
            "schema_version": 1,
            "pack_id": module.PACK_ID,
            "n_total": 2,
            "n_answered": 1,
            "complete": False,
            "answers": [
                {
                    "review_id": "r1",
                    "sample_id": "s1",
                    "decision": "KEEP",
                    "note": "clean",
                    "decided_at": "2026-08-24T12:00:00Z",
                }
            ],
        },
    )
    return answers, pack, positive_manifest, source_pack


def test_summarize_answers_joins_frozen_short_lineage_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers, pack, positive_manifest, source_pack = _answer_join_fixture(tmp_path, monkeypatch)
    summary = summarize_answers(
        answers,
        output_dir=pack,
        positive_manifest=positive_manifest,
        source_pack=source_pack,
    )
    assert summary["n_answered"] == 1
    assert summary["owner_review_complete"] is False
    assert summary["counts"] == {
        "KEEP": 1,
        "REMOVE": 0,
        "UNCERTAIN": 0,
        "PENDING": 1,
    }
    assert [row["owner_refilter_decision"] for row in summary["joined_rows"]] == [
        "KEEP",
        "PENDING",
    ]
    assert all(row["training_eligible"] is False for row in summary["joined_rows"])
    assert all(row["production_eligible"] is False for row in summary["joined_rows"])
    assert summary["holdout_read"] is False
    assert summary["new_dataset_materialized"] is False


def test_summarize_answers_rejects_sample_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers, pack, positive_manifest, source_pack = _answer_join_fixture(tmp_path, monkeypatch)
    payload = json.loads(answers.read_text(encoding="utf-8"))
    payload["answers"][0]["sample_id"] = "wrong"
    _write_json(answers, payload)
    with pytest.raises(RopeReviewBuildError, match="answer sample_id mismatch"):
        summarize_answers(
            answers,
            output_dir=pack,
            positive_manifest=positive_manifest,
            source_pack=source_pack,
        )


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

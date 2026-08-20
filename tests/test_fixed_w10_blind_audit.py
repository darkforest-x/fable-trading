"""Sampling and page blinding for the fixed-W10 P1 audit."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yoyo.datasets.fixed_w10_blind_audit as audit_module
from yoyo.artifacts import load_registries
from yoyo.datasets.fixed_w10_blind_audit import (
    _build_one_pack,
    cohen_kappa,
    sha256_file,
    stratified_sample,
)


PROJECT = Path(__file__).resolve().parents[1]


def _rows(tmp_path: Path) -> list[dict]:
    rows = []
    for i in range(40):
        image = tmp_path / f"source_{i}.png"
        image.write_bytes(f"fake-png-{i}".encode())
        direct = i < 20
        rows.append(
            {
                "gold_id": f"secret_gold_{i:03d}",
                "migration_status": "DIRECT" if direct else "IGNORE",
                "shape_label": "SIGNAL" if i % 2 else "NO_SIGNAL",
                "source_annotation_type": "owner" if i % 3 else "easy_negative_pool",
                "split": ("train", "val", "test")[i % 3],
                "_image_path": str(image),
                "_image_rel": f"classification/secret/source_{i}.png",
                "_image_sha256": sha256_file(image),
            }
        )
    return rows


def test_stratified_sample_is_exact_unique_and_deterministic(tmp_path: Path) -> None:
    rows = _rows(tmp_path)
    first, table = stratified_sample(rows, 20, seed=7)
    second, _ = stratified_sample(list(reversed(rows)), 20, seed=7)
    assert len(first) == len({row["gold_id"] for row in first}) == 20
    assert [row["gold_id"] for row in first] == [row["gold_id"] for row in second]
    assert sum(row["sampled"] for row in table) == 20
    assert all(row["sampled"] >= 1 for row in table)


def test_public_pack_hides_truth_source_split_and_repeat_identity(tmp_path: Path) -> None:
    rows = _rows(tmp_path)[:12]
    out = tmp_path / "pack"
    summary = _build_one_pack(
        rows,
        pack_root=out,
        pack_id="test-pack",
        seed=9,
        repeat_target=3,
        title="test",
        notice="decision only",
    )
    manifest = json.loads((out / "public" / "manifest.json").read_text())
    page = (out / "public" / "index.html").read_text()
    public = json.dumps(manifest["items"]) + page
    assert '<link rel="icon" href="data:,">' in page
    for leak in (
        "secret_gold_",
        "easy_negative_pool",
        "classification/secret",
        "repeat_of_review_id",
        '"split"',
        '"migration_status"',
    ):
        assert leak not in public
    truth = [json.loads(line) for line in (out / "admin" / "truth.jsonl").read_text().splitlines()]
    assert len(truth) == summary["n_items"] == 15
    assert sum(not row["is_primary"] for row in truth) == 3
    assert all((out / "public" / row["blind_image"]).is_file() for row in truth)


def test_cohen_kappa_reports_repeat_consistency() -> None:
    perfect = [("SIGNAL", "SIGNAL"), ("NO_SIGNAL", "NO_SIGNAL")]
    assert cohen_kappa(perfect) == 1.0
    mixed = [("SIGNAL", "SIGNAL"), ("SIGNAL", "NO_SIGNAL"), ("NO_SIGNAL", "NO_SIGNAL")]
    value = cohen_kappa(mixed)
    assert value is not None and -1.0 <= value <= 1.0


def test_cli_can_be_invoked_by_path_outside_the_repository(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT / "tools/datasets/fixed_w10_p1_audit.py"), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "build" in result.stdout and "score" in result.stdout


def test_complete_export_scores_rows_and_keeps_owner_gate_closed(
    tmp_path: Path, monkeypatch
) -> None:
    pack = tmp_path / "pack"
    (pack / "public").mkdir(parents=True)
    (pack / "admin").mkdir(parents=True)
    truth = []
    answers = []
    events = []
    for i in range(398):
        review_id = f"p{i:03d}"
        label = "SIGNAL" if i % 2 else "NO_SIGNAL"
        direct = i < 188
        truth.append(
            {
                "review_id": review_id,
                "gold_id": f"g{i:03d}",
                "is_primary": True,
                "repeat_of_review_id": None,
                "given_label": label,
                "counts_toward_direct": direct,
            }
        )
        answers.append(
            {
                "review_id": review_id,
                "review_label": label,
                "core_start_position": 6 if label == "SIGNAL" else None,
            }
        )
        events.append(
            {
                "gold_id": f"g{i:03d}",
                "event_group_id": f"eg{i:03d}",
                "shape_label": label,
                "migration_status": "DIRECT" if direct else "IGNORE",
                "source_annotation_type": "human_gold_owner_box",
                "window_length": 10,
                "core_length": 4 if label == "SIGNAL" else None,
                "local_core_start": 5,
                "local_core_end_exclusive": 9,
                "local_confirmation_position": 9,
                "contains_other_core": False,
                "future_used_in_model_input": False,
                "holdout_read": False,
                "split": "train",
            }
        )
    for i in range(50):
        primary = truth[i]
        review_id = f"r{i:03d}"
        truth.append(
            {
                **primary,
                "review_id": review_id,
                "is_primary": False,
                "repeat_of_review_id": primary["review_id"],
            }
        )
        answers.append(
            {
                "review_id": review_id,
                "review_label": primary["given_label"],
                "core_start_position": 6 if primary["given_label"] == "SIGNAL" else None,
            }
        )
    (pack / "admin" / "truth.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in truth)
    )
    (pack / "public" / "manifest.json").write_text(
        json.dumps(
            {
                "pack_id": audit_module.PACK_ID,
                "items": [{"review_id": row["review_id"]} for row in truth],
            }
        )
    )
    gold_sha = "b" * 64
    (pack / "prereg.json").write_text(
        json.dumps(
            {
                "pack_id": audit_module.PACK_ID,
                "population": {"gold_events_sha256": gold_sha},
            }
        )
    )
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(
        json.dumps(
            {
                "pack_id": audit_module.PACK_ID,
                "answers": answers,
            }
        )
    )
    monkeypatch.setattr(
        audit_module,
        "validate_dataset",
        lambda _: {
            "dataset_root": tmp_path,
            "gold_sha256": gold_sha,
            "events": events,
            "duplicate_image_sha_across_splits": 0,
        },
    )
    result = audit_module.score_audit(tmp_path, pack, answers_path)
    assert result["n_direct"] == 188
    assert result["n_spot"] == 188
    assert result["direct_error_rate"] == 0.0
    assert result["review_evidence"]["repeat_metrics"]["cohen_kappa"] == 1.0
    assert result["review_evidence"]["boundary_metrics"]["exact_agreement"] == 1.0
    assert result["gates"]["owner_training_approval"] is False
    assert result["training_eligible"] is False


def test_fixed_w10_registry_points_to_the_2649_artifact_bytes() -> None:
    registries = load_registries(root=PROJECT)
    artifact = registries.artifact("fixed-w10-core4-confirm1-v1-2649")
    path = PROJECT / artifact.source_path
    assert path.is_file()
    assert artifact.sha256 == sha256_file(path)
    assert artifact.size_bytes == path.stat().st_size
    by_experiment = {row.experiment_id: row for row in registries.experiments}
    freeze = by_experiment["exp-yoyo-trading-fixed-w10-gold-freeze"]
    cleanlab = by_experiment["exp-p1-gold-label-quality-cleanlab-v1"]
    assert freeze.artifacts == ["fixed-w10-core4-confirm1-v1-2649"]
    assert cleanlab.artifacts == ["fixed-w10-core4-confirm1-v1-2649"]
    assert "yoyo-trading-dataset-v3-gold-core" not in freeze.artifacts + cleanlab.artifacts

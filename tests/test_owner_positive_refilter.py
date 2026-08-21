from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from yoyo.artifacts import load_registries
from yoyo.datasets import owner_positive_refilter as mod


PROJECT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path, Path]:
    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPECTED_POSITIVES", 2)
    monkeypatch.setattr(mod, "EXPECTED_REVIEW_ROWS", 3)
    monkeypatch.setattr(mod, "EXPECTED_OWNER_SHORT", 3)
    monkeypatch.setattr(mod, "EXPECTED_DUPLICATE_ALIASES", 1)
    review_root = tmp_path / "review"
    previews = review_root / "previews"
    previews.mkdir(parents=True)
    for name, color in [("a", "white"), ("b", "gray"), ("c", "black")]:
        Image.new("RGB", mod.EXPECTED_CANVAS, color).save(previews / f"{name}.jpg")

    sheet = review_root / "review_sheet.csv"
    fields = ["box_id", "owner_side", "preview_path"]
    with sheet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            [
                {"box_id": "a", "owner_side": "short", "preview_path": "previews/a.jpg"},
                {"box_id": "b", "owner_side": "short", "preview_path": "previews/b.jpg"},
                {"box_id": "c", "owner_side": "short", "preview_path": "previews/c.jpg"},
            ]
        )

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    rows = []
    for sample_id, annotation_ids in [("s1", ["a", "b"]), ("s2", ["c"])]:
        image = dataset / f"{sample_id}.png"
        label = dataset / f"{sample_id}.txt"
        Image.new("RGB", (20, 20), "red").save(image)
        label.write_text("0 0.5 0.5 0.2 0.2\n")
        rows.append(
            {
                "sample_id": sample_id,
                "symbol": "TEST",
                "split": "train",
                "class": "positive",
                "source_owner_gold_confirmed": True,
                "owner_annotation_ids": annotation_ids,
                "source_owner_global": [1, 2],
                "source_owner_cut_time": "2025-01-01T00:00:00Z",
                "image_path": str(image.relative_to(tmp_path)),
                "label_path": str(label.relative_to(tmp_path)),
                "image_sha256": _sha(image),
                "label_sha256": _sha(label),
            }
        )
    manifest = tmp_path / "positive_manifest.jsonl"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return manifest, sheet, review_root, tmp_path / "out"


def test_builds_single_image_owner_positive_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, sheet, review_root, out = _fixture(tmp_path, monkeypatch)
    summary = mod.build_pack(
        positive_manifest=manifest,
        review_sheet=sheet,
        review_root=review_root,
        output_dir=out,
        generator_commit="test-commit",
    )
    assert summary["n_items"] == 2
    assert mod.verify_pack(out) == {"ok": True, "n_items": 2, "pack_id": mod.PACK_ID}
    public = json.loads((out / "public" / "manifest.json").read_text())
    assert all(set(item) == {"review_id", "image"} for item in public["items"])
    page = (out / "public" / "index.html").read_text()
    assert page.count('<img id="chart"') == 1
    assert "historical_image" not in page
    assert "toggleReference" not in page
    assert "只判断绿色框里的形态" in page
    assert "K / 1 · 保留" in page
    assert "X / 2 · 去掉" in page


def test_rejects_wrong_owner_side(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, sheet, review_root, out = _fixture(tmp_path, monkeypatch)
    text = sheet.read_text().replace("b,short", "b,long")
    sheet.write_text(text)
    with pytest.raises(mod.RefilterBuildError, match="owner short population changed"):
        mod.build_pack(
            positive_manifest=manifest,
            review_sheet=sheet,
            review_root=review_root,
            output_dir=out,
            generator_commit="test-commit",
        )


def test_verify_detects_image_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, sheet, review_root, out = _fixture(tmp_path, monkeypatch)
    mod.build_pack(
        positive_manifest=manifest,
        review_sheet=sheet,
        review_root=review_root,
        output_dir=out,
        generator_commit="test-commit",
    )
    first = next((out / "public" / "images").glob("*.jpg"))
    first.write_bytes(b"changed")
    with pytest.raises(mod.RefilterBuildError, match="review image SHA mismatch"):
        mod.verify_pack(out)


def test_summarize_answers_joins_private_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, sheet, review_root, out = _fixture(tmp_path, monkeypatch)
    mod.build_pack(
        positive_manifest=manifest,
        review_sheet=sheet,
        review_root=review_root,
        output_dir=out,
        generator_commit="test-commit",
    )
    public = json.loads((out / "public" / "manifest.json").read_text())
    review_id = public["items"][0]["review_id"]
    answers = tmp_path / "answers.json"
    answers.write_text(
        json.dumps(
            {
                "pack_id": mod.PACK_ID,
                "answers": [{"review_id": review_id, "decision": "KEEP", "note": "best"}],
            }
        )
    )
    result = mod.summarize_answers(answers, out)
    assert result["counts"] == {"KEEP": 1, "REMOVE": 0, "UNCERTAIN": 0}
    assert result["joined"][0]["sample_id"] in {"s1", "s2"}
    assert result["training_eligible_changed"] is False


def test_formal_refilter_registry_points_to_exact_manifest() -> None:
    registries = load_registries(root=PROJECT)
    artifact = registries.artifact("owner-short-positive-refilter-v1")
    path = PROJECT / artifact.source_path
    assert path.is_file()
    assert artifact.sha256 == _sha(path)
    assert artifact.size_bytes == path.stat().st_size
    assert artifact.training_eligible is False
    assert artifact.production_eligible is False

    experiment = next(
        row
        for row in registries.experiments
        if row.experiment_id == "exp-p1-owner-short-positive-refilter-v1"
    )
    assert experiment.status == "superseded"
    assert experiment.artifacts == ["owner-short-positive-refilter-v1"]
    assert experiment.holdout_consumed is False
    assert experiment.training_eligible is False
    assert experiment.production_eligible is False


def test_cli_can_be_invoked_outside_repository(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT / "tools/datasets/owner_positive_refilter.py"), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "build" in result.stdout and "summarize" in result.stdout

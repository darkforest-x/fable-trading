"""Sampling and page blinding for the fixed-W10 P1 audit."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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
    public = json.dumps(manifest["items"]) + (out / "public" / "index.html").read_text()
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

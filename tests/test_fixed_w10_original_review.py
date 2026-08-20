"""Original-source triage pack and offline page behavior."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from yoyo.datasets.fixed_w10_blind_audit import AuditBuildError, sha256_file
from yoyo.datasets.fixed_w10_original_review import (
    PACK_ID,
    _page_html,
    build_pack_from_resolved,
    summarize_export,
)


PROJECT = Path(__file__).resolve().parents[1]


def _resolved(tmp_path: Path) -> list[dict]:
    rows = []
    for i in range(3):
        primary = tmp_path / f"original_{i}.png"
        primary.write_bytes(f"original-{i}".encode())
        reference = tmp_path / f"reference_{i}.png"
        if i == 1:
            reference.write_bytes(b"reference")
        rows.append(
            {
                "gold_id": f"secret-gold-{i}",
                "source_kind": "owner_original_long_chart",
                "source_dataset": f"secret-source-{i}",
                "source_record_id": f"secret-record-{i}",
                "source_annotation_type": "secret-type",
                "shape_label": "SIGNAL" if i else "NO_SIGNAL",
                "migration_status": "DIRECT",
                "split": "train",
                "decision_time": "2026-01-01T00:00:00+00:00",
                "primary": {
                    "path": str(primary),
                    "sha256": sha256_file(primary),
                    "size_bytes": primary.stat().st_size,
                },
                "reference": (
                    {
                        "path": str(reference),
                        "sha256": sha256_file(reference),
                        "size_bytes": reference.stat().st_size,
                    }
                    if i == 1
                    else None
                ),
            }
        )
    return rows


def test_page_has_fast_shortcuts_resume_import_and_export() -> None:
    page = _page_html(
        [{"review_id": "r1", "image": "images/r1.png", "reference_image": None}],
        gold_sha256="a" * 64,
    )
    for required in (
        "K / 1 · 保留",
        "X / 2 · 去掉",
        "? / 3 · 待定",
        "localStorage",
        "导入进度",
        "导出 JSON",
        "autoNext",
        "reference.removeAttribute('src')",
    ):
        assert required in page


def test_pack_uses_original_bytes_and_hides_private_lineage(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    summary = build_pack_from_resolved(_resolved(tmp_path), pack, gold_sha256="b" * 64)
    manifest = json.loads((pack / "public" / "manifest.json").read_text())
    page = (pack / "public" / "index.html").read_text()
    public = json.dumps(manifest, ensure_ascii=False) + page
    assert summary["n_items"] == 3
    assert summary["n_reference_images"] == 1
    assert summary["w10_images_used"] is False
    assert "secret-gold" not in public
    assert "secret-source" not in public
    assert "shape_label" not in public
    truth = [json.loads(line) for line in (pack / "admin" / "truth.jsonl").read_text().splitlines()]
    assert len(truth) == 3
    for row in truth:
        copied = pack / "public" / row["public_image"]
        assert copied.is_file()
        assert sha256_file(copied) == row["original_primary_sha256"]


def test_export_summary_joins_decisions_without_mutating_eligibility(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    build_pack_from_resolved(_resolved(tmp_path), pack, gold_sha256="c" * 64)
    truth = [json.loads(line) for line in (pack / "admin" / "truth.jsonl").read_text().splitlines()]
    answers = {
        "pack_id": PACK_ID,
        "answers": [
            {"review_id": truth[0]["review_id"], "decision": "KEEP"},
            {"review_id": truth[1]["review_id"], "decision": "REMOVE"},
        ],
    }
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers))
    summary = summarize_export(pack, path)
    assert summary["n_total"] == 3
    assert summary["n_answered"] == 2
    assert summary["counts"] == {"KEEP": 1, "REMOVE": 1}
    assert summary["complete"] is False
    assert summary["training_eligible_changed"] is False

    answers["answers"].append(
        {"review_id": truth[2]["review_id"], "decision": "NOT_VALID"}
    )
    path.write_text(json.dumps(answers))
    with pytest.raises(AuditBuildError, match="invalid decision"):
        summarize_export(pack, path)


def test_cli_can_be_invoked_by_path_outside_repository(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT / "tools/datasets/fixed_w10_original_review.py"), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "build" in result.stdout and "summarize" in result.stdout

#!/usr/bin/env python3
"""Audit Grade-A YOLO train/val identity and complete source-interval isolation.

This post-score leakage audit reads only the immutable pre-holdout manifest and
its independent QA receipt.  It does not read OHLCV, model predictions, val
metrics, or holdout data.  A positive dependency interval ends at the later of
the rendered window and ``core_end + 5`` (the frozen completed-path selection
dependency); a negative uses its manifest-pinned dependency end.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
EXPERIMENT_ID = "exp-15m-ma-launch-owner-grade-a8000-neg24000-train960-v1"
EXPECTED_MANIFEST_SHA256 = (
    "22e95465b072fdfc4b0284f439c73a7f1cc9be9ab998ea768b2857a7cec798e2"
)
EXPECTED_QA_SHA256 = (
    "364ac95c2ec71427f6062a04edac1d8e2be9d8c7f04e1752a41762171d901473"
)
EXPECTED_COUNTS = {"train": 27200, "val": 4800}
BAR_MINUTES = 15


class GradeASplitAuditError(ValueError):
    """Fail-closed split-audit contract error."""


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generator_commit() -> str:
    """Bind the receipt to a committed implementation on main."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise GradeASplitAuditError("split audit must run on main")
    relative = str(SCRIPT.relative_to(ROOT))
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", relative], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise GradeASplitAuditError(f"split audit is not committed: {dirty}")
    commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", relative],
        cwd=ROOT,
        text=True,
    ).strip()
    if len(commit) != 40:
        raise GradeASplitAuditError("could not resolve split-audit commit")
    return commit


def dependency_interval(row: Mapping[str, Any]) -> tuple[int, int, datetime]:
    """Return inclusive source indices and dependency-end time for one image."""

    start = int(row["window_start_i"])
    render_end = int(row["window_end_i"])
    if row.get("sample_kind") == "positive":
        core_end = int(row["source_core_end_i"])
        end = max(render_end, core_end + 5)
        core_end_time = datetime.fromisoformat(str(row["core_end_time"]))
        end_time = core_end_time + timedelta(minutes=(end - core_end) * BAR_MINUTES)
    elif row.get("sample_kind") == "negative":
        end = int(row["dependency_end_i"])
        end_time = datetime.fromisoformat(str(row["dependency_end_time"]))
    else:
        raise GradeASplitAuditError(f"unexpected sample kind: {row.get('sample_kind')}")
    if start > render_end or render_end > end:
        raise GradeASplitAuditError(
            f"invalid dependency interval: start={start} render_end={render_end} end={end}"
        )
    return start, end, end_time


def row_event_identity(row: Mapping[str, Any]) -> str:
    """Return a direction-safe positive or negative event identity."""

    if row.get("sample_kind") == "positive":
        value = row.get("event_id")
        prefix = "positive"
    else:
        value = row.get("negative_event_id")
        prefix = "negative"
    if not value:
        raise GradeASplitAuditError("manifest row has no event identity")
    return f"{prefix}:{value}"


def interval_rows(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, list[tuple[int, int, str]]]:
    """Group dependency intervals by immutable source path."""

    grouped: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for row in rows:
        start, end, _end_time = dependency_interval(row)
        grouped[str(row["source_path"])].append(
            (start, end, str(row["dataset_sample_id"]))
        )
    for values in grouped.values():
        values.sort()
    return grouped


def cross_split_interval_overlaps(
    train: Iterable[Mapping[str, Any]], val: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Return the first intersecting dependency pair for each affected source."""

    left, right = interval_rows(train), interval_rows(val)
    overlaps: list[dict[str, Any]] = []
    for source in sorted(left.keys() & right.keys()):
        train_rows, val_rows = left[source], right[source]
        i = j = 0
        while i < len(train_rows) and j < len(val_rows):
            train_start, train_end, train_id = train_rows[i]
            val_start, val_end, val_id = val_rows[j]
            if train_end < val_start:
                i += 1
            elif val_end < train_start:
                j += 1
            else:
                overlaps.append(
                    {
                        "source_path": source,
                        "train_dataset_sample_id": train_id,
                        "val_dataset_sample_id": val_id,
                        "train_interval": [train_start, train_end],
                        "val_interval": [val_start, val_end],
                    }
                )
                break
    return overlaps


def audit(dataset: Path, qa_receipt: Path, output: Path) -> dict[str, Any]:
    """Run exact identity, event, chronology and dependency isolation gates."""

    if output.exists():
        raise FileExistsError(f"refusing to overwrite split audit: {output}")
    manifest = dataset / "manifest.jsonl"
    if not manifest.is_file() or sha256_file(manifest) != EXPECTED_MANIFEST_SHA256:
        raise GradeASplitAuditError("Grade-A manifest identity drifted")
    if not qa_receipt.is_file() or sha256_file(qa_receipt) != EXPECTED_QA_SHA256:
        raise GradeASplitAuditError("independent QA receipt identity drifted")
    qa = json.loads(qa_receipt.read_text(encoding="utf-8"))
    if qa.get("passed") is not True or qa.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256:
        raise GradeASplitAuditError("independent QA receipt does not bind the dataset")

    rows = [
        json.loads(line)
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line
    ]
    counts = {
        split: sum(row.get("split") == split for row in rows)
        for split in EXPECTED_COUNTS
    }
    if counts != EXPECTED_COUNTS or sum(counts.values()) != len(rows):
        raise GradeASplitAuditError(f"split counts drifted: {counts}")
    train = [row for row in rows if row["split"] == "train"]
    val = [row for row in rows if row["split"] == "val"]

    intersections = {
        "image_sha256": len(
            {str(row["image_sha256"]) for row in train}
            & {str(row["image_sha256"]) for row in val}
        ),
        "dataset_sample_id": len(
            {str(row["dataset_sample_id"]) for row in train}
            & {str(row["dataset_sample_id"]) for row in val}
        ),
        "event_identity": len(
            {row_event_identity(row) for row in train}
            & {row_event_identity(row) for row in val}
        ),
    }
    if any(intersections.values()):
        raise GradeASplitAuditError(f"cross-split identity intersection: {intersections}")

    overlaps = cross_split_interval_overlaps(train, val)
    if overlaps:
        raise GradeASplitAuditError(f"cross-split source intervals overlap: {overlaps[:3]}")

    train_end = max(dependency_interval(row)[2] for row in train)
    val_start = min(datetime.fromisoformat(str(row["window_start_time"])) for row in val)
    gap_hours = (val_start - train_end).total_seconds() / 3600.0
    if gap_hours <= 0:
        raise GradeASplitAuditError(f"non-positive chronological gap: {gap_hours}")
    shared_sources = {
        str(row["source_path"]) for row in train
    } & {str(row["source_path"]) for row in val}
    payload = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "generator_commit": generator_commit(),
        "generator_sha256": sha256_file(SCRIPT),
        "audit_trigger": "post_epoch6_mAP50_95_exceeded_0.7",
        "audit_inputs": "immutable manifest plus independent QA receipt only",
        "model_predictions_read": False,
        "holdout_consumed": False,
        "manifest_sha256": sha256_file(manifest),
        "independent_qa_sha256": sha256_file(qa_receipt),
        "split_counts": counts,
        "train_val_identity_intersections": intersections,
        "shared_source_files": len(shared_sources),
        "cross_split_source_interval_overlaps": 0,
        "dependency_end_rule": {
            "positive": "max(window_end_i, source_core_end_i + 5)",
            "negative": "manifest dependency_end_i",
        },
        "train_latest_dependency_end_time": train_end.isoformat(),
        "val_earliest_window_start_time": val_start.isoformat(),
        "cross_split_gap_hours": gap_hours,
        "passed": True,
        "active_or_frozen_changed": False,
        "production_eligible": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "datasets" / "ma_launch_owner_grade_a8000_yolo_neg24000_v1",
    )
    parser.add_argument(
        "--qa-receipt",
        type=Path,
        default=ROOT
        / "experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-v1/results/independent_qa_receipt.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = audit(args.dataset.resolve(), args.qa_receipt.resolve(), args.output.resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

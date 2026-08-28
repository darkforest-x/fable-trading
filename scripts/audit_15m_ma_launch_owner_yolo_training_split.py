#!/usr/bin/env python3
"""Audit train/val identity and source-interval isolation for owner-v2 YOLO.

Inputs are the immutable pre-holdout dataset manifest and its build receipt.
The audit never reads OHLCV or model predictions. It fails on any exact image,
sample identity, source identity, or same-source dependency-interval overlap
between the chronological train and validation splits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
EXPECTED_MANIFEST_SHA256 = (
    "6e601034ab15765a74b788cc6d094e9326c3044c1fb615c908ef9de897d6e0af"
)
EXPECTED_BUILD_RECEIPT_SHA256 = (
    "1deeebc93c94902a67ef1dcdcad0c4593a53b7e7627db3778b7541d2ceb8766a"
)
EXPECTED_COUNTS = {"train": 32644, "val": 7260, "excluded": 96}


class SplitAuditError(ValueError):
    """Fail-closed split-isolation error."""


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generator_commit() -> str:
    """Bind the receipt to this committed audit implementation on main."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise SplitAuditError("split audit must run on main")
    relative = str(SCRIPT.relative_to(ROOT))
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", relative], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise SplitAuditError(f"split audit is not committed: {dirty}")
    commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", relative],
        cwd=ROOT,
        text=True,
    ).strip()
    if len(commit) != 40:
        raise SplitAuditError("could not resolve split-audit commit")
    return commit


def interval_rows(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, list[tuple[int, int, str]]]:
    """Group complete render-plus-label dependency intervals by source file."""

    grouped: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["source_path"])].append(
            (
                int(row["window_start_i"]),
                int(row["dependency_end_i"]),
                str(row["sample_id"]),
            )
        )
    for values in grouped.values():
        values.sort()
    return grouped


def cross_split_interval_overlaps(
    train: Iterable[Mapping[str, Any]], val: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Return the first intersecting dependency pair for every affected source."""

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
                        "train_sample_id": train_id,
                        "val_sample_id": val_id,
                        "train_interval": [train_start, train_end],
                        "val_interval": [val_start, val_end],
                    }
                )
                break
    return overlaps


def audit(dataset: Path, build_receipt: Path, output: Path) -> dict[str, Any]:
    """Run exact identity, chronology and dependency-interval isolation gates."""

    if output.exists():
        raise FileExistsError(f"refusing to overwrite split audit: {output}")
    manifest = dataset / "manifest.jsonl"
    if sha256_file(manifest) != EXPECTED_MANIFEST_SHA256:
        raise SplitAuditError("manifest identity drifted")
    if sha256_file(build_receipt) != EXPECTED_BUILD_RECEIPT_SHA256:
        raise SplitAuditError("build receipt identity drifted")
    rows = [
        json.loads(line)
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line
    ]
    counts = {
        split: sum(row.get("split") == split for row in rows)
        for split in EXPECTED_COUNTS
    }
    if counts != EXPECTED_COUNTS:
        raise SplitAuditError(f"split counts drifted: {counts}")
    train = [row for row in rows if row["split"] == "train"]
    val = [row for row in rows if row["split"] == "val"]
    intersections = {
        "image_sha256": len(
            {row["image_sha256"] for row in train}
            & {row["image_sha256"] for row in val}
        ),
        "sample_id": len(
            {row["sample_id"] for row in train}
            & {row["sample_id"] for row in val}
        ),
        "source_sample_id": len(
            {row["source_sample_id"] for row in train}
            & {row["source_sample_id"] for row in val}
        ),
    }
    if any(intersections.values()):
        raise SplitAuditError(f"cross-split identity intersection: {intersections}")
    overlaps = cross_split_interval_overlaps(train, val)
    if overlaps:
        raise SplitAuditError(f"cross-split source intervals overlap: {overlaps[:3]}")

    train_end = max(str(row["dependency_end_time"]) for row in train)
    val_start = min(str(row["window_start_time"]) for row in val)
    gap_hours = (
        datetime.fromisoformat(val_start) - datetime.fromisoformat(train_end)
    ).total_seconds() / 3600.0
    shared_sources = {
        row["source_path"] for row in train
    } & {row["source_path"] for row in val}
    payload = {
        "experiment_id": "exp-15m-ma-launch-owner-yolo-neg30000-train960-v1",
        "generator_commit": generator_commit(),
        "manifest_sha256": sha256_file(manifest),
        "build_receipt_sha256": sha256_file(build_receipt),
        "split_counts": counts,
        "train_val_identity_intersections": intersections,
        "shared_source_files": len(shared_sources),
        "cross_split_source_interval_overlaps": 0,
        "train_latest_dependency_end_time": train_end,
        "val_earliest_window_start_time": val_start,
        "cross_split_gap_hours": gap_hours,
        "passed": True,
        "holdout_consumed": False,
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
        default=ROOT / "datasets" / "ma_launch_owner_autofill10000_yolo_neg30000_v2",
    )
    parser.add_argument(
        "--build-receipt",
        type=Path,
        default=ROOT
        / "experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2/results/dataset_build_receipt.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = audit(
        args.dataset.resolve(), args.build_receipt.resolve(), args.output.resolve()
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Verify one committed 15m candidate review pool without reading OHLCV.

Inputs are the preregistration, public review manifest, scan summary, gallery,
rendered PNGs and causality receipt.  The verifier rehashes every PNG, decodes
its dimensions, checks review-marker arithmetic, replays prior-plus-new quota
and deduplication checks, and confirms all rows remain PENDING and ineligible.
It never opens source kline files, Owner label manifests, model weights,
holdout data, forward state or order state.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import pandas as pd

from scripts.collect_15m_ma_launch_candidates import (
    ROOT,
    load_existing_candidate_rows,
    repo_path,
    selection_audit,
    validate_preregistration,
)
from yoyo.datasets.fifteen_minute_launch_candidates import (
    CandidateCollectionError,
    CandidateSpec,
    sha256_file,
    utc,
    write_json,
)


DEFAULT_PREREG = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-candidate9000-v1"
    / "preregistration.json"
)


def git_output(*args: str) -> str:
    """Run a read-only Git query from the repository root."""

    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def verify_self_committed() -> str:
    """Return this verifier's commit or fail before writing evidence."""

    if git_output("branch", "--show-current") != "main":
        raise RuntimeError("candidate verifier must run on main")
    relative = str(Path(__file__).resolve().relative_to(ROOT))
    if git_output("status", "--short", "--", relative):
        raise RuntimeError("candidate verifier is not committed")
    commit = git_output("log", "-1", "--format=%H", "--", relative)
    if len(commit) != 40:
        raise RuntimeError("could not resolve verifier commit")
    return commit


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSON-lines artifact into dictionaries."""

    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def verify_marker_contract(
    rows: Sequence[Mapping[str, Any]], *, spec: CandidateSpec
) -> dict[str, Any]:
    """Verify that the review-only marker is an exact bar offset from anchor t."""

    expected_delta = pd.Timedelta(
        minutes=spec.bar_minutes * spec.review_marker_offset_bars
    )
    for row in rows:
        if int(row["review_marker_offset_bars"]) != spec.review_marker_offset_bars:
            raise CandidateCollectionError("review marker offset drifted")
        if row.get("review_marker_is_training_label") is not False:
            raise CandidateCollectionError("review marker became a training label")
        if int(row["review_marker_source_i"]) != (
            int(row["source_anchor_i"]) + spec.review_marker_offset_bars
        ):
            raise CandidateCollectionError("review marker source index drifted")
        if utc(row["review_marker_time"]) != utc(row["anchor_time"]) + expected_delta:
            raise CandidateCollectionError("review marker timestamp drifted")
    return {
        "rows": len(rows),
        "offset_bars": spec.review_marker_offset_bars,
        "offset_minutes": int(expected_delta / pd.Timedelta(minutes=1)),
        "training_label_true": 0,
        "passed": True,
    }


def verify_images(
    rows: Sequence[Mapping[str, Any]], *, results_dir: Path
) -> dict[str, Any]:
    """Rehash and decode every manifest PNG, rejecting missing or extra files."""

    chart_dir = (results_dir / "review_charts").resolve()
    manifest_paths: list[Path] = []
    dimensions: Counter[str] = Counter()
    for row in rows:
        path = repo_path(row["review_path"])
        if path.parent != chart_dir:
            raise CandidateCollectionError(f"review path escaped chart directory: {path}")
        if not path.is_file():
            raise CandidateCollectionError(f"review PNG is missing: {path}")
        if sha256_file(path) != str(row["review_sha256"]):
            raise CandidateCollectionError(f"review PNG hash drifted: {path}")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise CandidateCollectionError(f"review PNG did not decode: {path}")
        height, width = image.shape[:2]
        dimensions[f"{width}x{height}"] += 1
        manifest_paths.append(path)
    if len(manifest_paths) != len(set(manifest_paths)):
        raise CandidateCollectionError("review manifest contains duplicate paths")
    disk_paths = set(chart_dir.glob("*.png"))
    if disk_paths != set(manifest_paths):
        raise CandidateCollectionError(
            "review chart directory differs from the exact manifest path set"
        )
    if dimensions != Counter({"1280x770": len(rows)}):
        raise CandidateCollectionError(f"unexpected review dimensions: {dimensions}")
    return {
        "manifest_paths": len(manifest_paths),
        "disk_pngs": len(disk_paths),
        "hashes_verified": len(manifest_paths),
        "decoded_dimensions": dict(dimensions),
        "passed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    prereg_path = args.prereg.resolve()
    prereg, spec, _, _ = validate_preregistration(prereg_path)
    experiment_dir = prereg_path.parent
    results_dir = (
        args.results.resolve() if args.results else (experiment_dir / "results").resolve()
    )
    output = args.out.resolve() if args.out else results_dir / "verification_receipt.json"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite verification receipt: {output}")
    verifier_commit = verify_self_committed()

    manifest_path = results_dir / "review_manifest.jsonl"
    summary_path = results_dir / "scan_summary.json"
    causality_path = results_dir / "causality_audit.json"
    gallery_path = results_dir / "index.html"
    rows = read_jsonl(manifest_path)
    expected_rows = 2 * spec.target_per_side
    if len(rows) != expected_rows:
        raise CandidateCollectionError(
            f"manifest expected {expected_rows} rows, got {len(rows)}"
        )

    event_ids = [str(row["event_id"]) for row in rows]
    if len(event_ids) != len(set(event_ids)):
        raise CandidateCollectionError("candidate event IDs are not unique")
    side_counts = Counter(str(row["direction"]) for row in rows)
    expected_sides = Counter({"LONG": spec.target_per_side, "SHORT": spec.target_per_side})
    if side_counts != expected_sides:
        raise CandidateCollectionError(f"candidate sides drifted: {side_counts}")
    for side in ("LONG", "SHORT"):
        ranks = sorted(int(row["rank"]) for row in rows if row["direction"] == side)
        if ranks != list(range(1, spec.target_per_side + 1)):
            raise CandidateCollectionError(f"{side} ranks are not contiguous")
    if any(row.get("owner_verdict") != "PENDING" for row in rows):
        raise CandidateCollectionError("one or more candidates are not PENDING")
    if any(row.get("training_eligible") is not False for row in rows):
        raise CandidateCollectionError("one or more candidates became training eligible")
    if any(row.get("production_eligible") is not False for row in rows):
        raise CandidateCollectionError("one or more candidates became production eligible")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary["preregistration_sha256"] != sha256_file(prereg_path):
        raise CandidateCollectionError("scan summary preregistration hash drifted")
    if summary["output"]["manifest_sha256"] != sha256_file(manifest_path):
        raise CandidateCollectionError("scan summary manifest hash drifted")
    if int(summary["holdout"]["ohlcv_rows_materialized"]) != 0:
        raise CandidateCollectionError("scan summary reports holdout OHLCV")
    if summary["holdout"]["read"] is not False:
        raise CandidateCollectionError("scan summary reports a holdout read")

    prior = load_existing_candidate_rows(prereg)
    selected = {
        side: [dict(row) for row in rows if str(row["direction"]) == side]
        for side in ("LONG", "SHORT")
    }
    quota = selection_audit(selected, spec=spec, existing_rows=prior)
    markers = verify_marker_contract(rows, spec=spec)
    images = verify_images(rows, results_dir=results_dir)

    causality = json.loads(causality_path.read_text(encoding="utf-8"))
    if len(causality) != spec.causality_audit_rows:
        raise CandidateCollectionError("causality row count drifted")
    if not all(bool(row["passed"]) for row in causality):
        raise CandidateCollectionError("causality audit contains a failure")
    maximum_difference = max(float(row["max_abs_difference"]) for row in causality)
    if maximum_difference != 0.0:
        raise CandidateCollectionError("causality audit is not exactly invariant")
    if summary["causality_null"]["audit_sha256"] != sha256_file(causality_path):
        raise CandidateCollectionError("causality audit hash drifted")

    gallery = gallery_path.read_text(encoding="utf-8")
    gallery_articles = gallery.count('<article class="card"')
    gallery_images = gallery.count('<img loading="lazy"')
    if gallery_articles != expected_rows or gallery_images != expected_rows:
        raise CandidateCollectionError("gallery card/image count drifted")
    forbidden_training_dirs = [
        results_dir / name for name in ("images", "labels", "train", "val", "dataset")
    ]
    present_training_dirs = [str(path) for path in forbidden_training_dirs if path.exists()]
    if present_training_dirs:
        raise CandidateCollectionError(
            f"training directories appeared in review results: {present_training_dirs}"
        )

    receipt = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verifier_commit": verifier_commit,
        "verification_scope": "static artifact integrity, marker arithmetic, union-pool quota, eligibility, and causality checks",
        "status": "passed",
        "inputs": {
            "preregistration_path": str(prereg_path.relative_to(ROOT)),
            "preregistration_sha256": sha256_file(prereg_path),
            "manifest_path": str(manifest_path.relative_to(ROOT)),
            "manifest_sha256": sha256_file(manifest_path),
        },
        "counts": {
            "manifest_rows": len(rows),
            "unique_event_ids": len(set(event_ids)),
            "side_counts": dict(side_counts),
            "owner_verdict_counts": dict(
                Counter(str(row["owner_verdict"]) for row in rows)
            ),
            "gallery_articles": gallery_articles,
            "gallery_image_refs": gallery_images,
        },
        "images": images,
        "review_marker": markers,
        "eligibility": {
            "training_eligible_true": 0,
            "production_eligible_true": 0,
            "training_directories_present": 0,
        },
        "union_pool_quota_recheck": quota,
        "causality": {
            "deterministic_rows": len(causality),
            "rows_passed": sum(bool(row["passed"]) for row in causality),
            "max_abs_difference": maximum_difference,
        },
        "holdout": summary["holdout"],
        "errors": [],
    }
    write_json(output, receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

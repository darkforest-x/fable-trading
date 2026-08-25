#!/usr/bin/env python3
"""Plan a released 15m SHORT dataset without reading or rendering market rows.

The planner consumes only the complete boundary-review summary directory and
an explicit Owner release receipt bound to the exact summary and SHORT preview
hashes.  It derives timestamps from ``anchor_time`` and the reviewed
W/core/confirmation integers, creates chronological dependency splits and a
new-batch Owner-box guard ledger, and stops before OHLCV, images, labels,
negative selection, model training or remote writes.

``--status-only`` records the current fail-closed gate without inventing
answers or release authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from yoyo.datasets.candidate_dataset_release import plan_dataset_release


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ROOT = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-dataset-release-gate9000-v1"
)
DEFAULT_PREREG = EXPERIMENT_ROOT / "preregistration.json"
DEFAULT_REVIEW_DIR = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-boundary-review9000-v1"
    / "results"
    / "owner_review_summary"
)
DEFAULT_RELEASE = EXPERIMENT_ROOT / "owner_dataset_release.json"
DEFAULT_STATUS_OUT = EXPERIMENT_ROOT / "results" / "current_gate_status.json"
MODULE_PATH = ROOT / "yoyo/datasets/candidate_dataset_release.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def repo_path(value: str | Path) -> Path:
    path = (ROOT / Path(value)).resolve()
    if path != ROOT and ROOT not in path.parents:
        raise ValueError(f"path escapes repository: {value}")
    return path


def git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def verify_planner_committed(prereg_path: Path) -> str:
    if git_output("branch", "--show-current") != "main":
        raise RuntimeError("dataset release planner must run on main")
    paths = [Path(__file__).resolve(), MODULE_PATH, prereg_path.resolve()]
    relatives = [str(path.relative_to(ROOT)) for path in paths]
    dirty = git_output("status", "--short", "--", *relatives)
    if dirty:
        raise RuntimeError(f"dataset release planner is not committed:\n{dirty}")
    return git_output("rev-parse", "HEAD")


def verify_pinned_sources(prereg: Mapping[str, Any]) -> dict[str, str]:
    source = prereg["source"]
    pairs = {
        "review_preregistration": (
            source["review_preregistration_path"],
            source["review_preregistration_sha256"],
        ),
        "review_qa_receipt": (
            source["review_qa_receipt_path"], source["review_qa_receipt_sha256"]
        ),
        "candidate_manifest": (
            source["candidate_manifest_path"], source["candidate_manifest_sha256"]
        ),
        "local_signal_v2_protocol": (
            source["local_signal_v2_protocol_path"],
            source["local_signal_v2_protocol_sha256"],
        ),
    }
    actual: dict[str, str] = {}
    for name, (raw_path, expected_hash) in pairs.items():
        path = repo_path(raw_path)
        digest = sha256_file(path)
        if digest != expected_hash:
            raise ValueError(f"pinned source hash drifted: {name}")
        actual[name] = digest
    return actual


def current_gate_status(
    *,
    prereg_path: Path,
    review_dir: Path,
    release_path: Path,
    planner_commit: str,
) -> dict[str, Any]:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    source_hashes = verify_pinned_sources(prereg)
    required_review_files = [
        review_dir / name
        for name in prereg["required_review_summary"]["required_files"]
    ]
    review_present = all(path.is_file() for path in required_review_files)
    release_present = release_path.is_file()
    missing: list[str] = []
    if not review_present:
        missing.append("complete_owner_review_summary")
    if not release_present:
        missing.append("explicit_hash_bound_owner_short_release")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": prereg["experiment_id"],
        "planner_commit": planner_commit,
        "preregistration_sha256": sha256_file(prereg_path),
        "status": (
            "ready_for_release_plan_validation"
            if not missing
            else "blocked_pending_complete_owner_review_and_explicit_release"
        ),
        "inputs": {
            "review_summary_dir": str(review_dir),
            "required_review_files_present": review_present,
            "owner_release_path": str(release_path),
            "owner_release_present": release_present,
            "pinned_source_hashes": source_hashes,
        },
        "missing_requirements": missing,
        "counts": {
            "repository_formal_owner_answers": 0 if not review_present else None,
            "short_positive_plans": 0,
            "guard_intervals": 0,
            "negative_rows": 0,
            "training_images": 0,
            "yolo_labels": 0,
            "epochs": 0,
            "weights": 0,
        },
        "eligibility": {
            "dataset_plan_eligible": False,
            "dataset_materialization_eligible": False,
            "training_eligible": False,
            "production_eligible": False,
        },
        "holdout": {
            "read": False,
            "ohlcv_rows_materialized": 0,
            "source_ohlcv_files_opened": 0,
        },
        "remote": {"writes": 0, "training_started": False},
    }


def write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_release_plan(
    *,
    prereg_path: Path,
    review_dir: Path,
    release_path: Path,
    output_dir: Path,
    planner_commit: str,
) -> dict[str, Any]:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    pinned_hashes = verify_pinned_sources(prereg)
    summary_path = review_dir / "review_summary.json"
    joined_path = review_dir / "review_joined.jsonl"
    preview_path = review_dir / "short_keep_release_preview.jsonl"
    for path in (summary_path, joined_path, preview_path, release_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    review_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    joined_rows = read_jsonl(joined_path)
    preview_rows = read_jsonl(preview_path)
    outputs = review_summary.get("outputs", {})
    if outputs.get("review_joined_sha256") != sha256_file(joined_path):
        raise ValueError("review_joined hash differs from review summary")
    if outputs.get("short_keep_release_preview_sha256") != sha256_file(preview_path):
        raise ValueError("SHORT KEEP preview hash differs from review summary")
    release_receipt = json.loads(release_path.read_text(encoding="utf-8"))
    summary_hash = sha256_file(summary_path)
    preview_hash = sha256_file(preview_path)
    plan = plan_dataset_release(
        review_summary=review_summary,
        joined_rows=joined_rows,
        preview_rows=preview_rows,
        release_receipt=release_receipt,
        review_summary_sha256=summary_hash,
        preview_sha256=preview_hash,
        prereg=prereg,
    )

    building = output_dir.with_name(f"{output_dir.name}.building")
    if output_dir.exists() or building.exists():
        raise FileExistsError(
            f"refusing to overwrite release plan: final={output_dir} building={building}"
        )
    building.mkdir(parents=True)
    positive_path = building / "short_positive_split_plan.jsonl"
    guard_path = building / "new_keep_guard_ledger.jsonl"
    negative_path = building / "negative_target_profile.json"
    write_jsonl(positive_path, plan.positive_rows)
    write_jsonl(guard_path, plan.guard_rows)
    negative_path.write_text(
        json.dumps(plan.negative_target_profile, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": prereg["experiment_id"],
        "planner_commit": planner_commit,
        "inputs": {
            "preregistration_path": str(prereg_path.relative_to(ROOT)),
            "preregistration_sha256": sha256_file(prereg_path),
            "review_summary_path": str(summary_path),
            "review_summary_sha256": summary_hash,
            "review_joined_sha256": sha256_file(joined_path),
            "short_keep_preview_sha256": preview_hash,
            "owner_release_sha256": sha256_file(release_path),
            "pinned_source_hashes": pinned_hashes,
        },
        **plan.summary,
        "output_files": {
            "short_positive_split_plan": "short_positive_split_plan.jsonl",
            "short_positive_split_plan_sha256": sha256_file(positive_path),
            "new_keep_guard_ledger": "new_keep_guard_ledger.jsonl",
            "new_keep_guard_ledger_sha256": sha256_file(guard_path),
            "negative_target_profile": "negative_target_profile.json",
            "negative_target_profile_sha256": sha256_file(negative_path),
        },
    }
    (building / "release_plan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(building, output_dir)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--status-out", type=Path, default=DEFAULT_STATUS_OUT)
    args = parser.parse_args()

    prereg_path = args.prereg.resolve()
    planner_commit = verify_planner_committed(prereg_path)
    if args.status_only:
        status = current_gate_status(
            prereg_path=prereg_path,
            review_dir=args.review_dir.resolve(),
            release_path=args.release.resolve(),
            planner_commit=planner_commit,
        )
        write_new_json(args.status_out.resolve(), status)
        print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.out is None:
        parser.error("--out is required unless --status-only is used")
    summary = build_release_plan(
        prereg_path=prereg_path,
        review_dir=args.review_dir.resolve(),
        release_path=args.release.resolve(),
        output_dir=args.out.resolve(),
        planner_commit=planner_commit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate an exported 15m candidate boundary review without making a dataset.

The browser export is bound to the hash-pinned PENDING candidate manifest and
Local Signal V2 protocol.  Complete mode requires exactly one decision per
event; KEEP requires exact W14--22/core4--7/confirmation3--5 arithmetic, while
DROP and UNCERTAIN forbid geometry.  Outputs are review receipts and an
eligibility *preview* only.  Every row remains training/negative/production
ineligible, and this script never reads OHLCV, holdout data or model files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from yoyo.datasets.candidate_boundary_review import validate_export


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREREG = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-boundary-review9000-v1"
    / "preregistration.json"
)
MODULE_PATH = ROOT / "yoyo/datasets/candidate_boundary_review.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_path(value: str | Path) -> Path:
    path = (ROOT / Path(value)).resolve()
    if path != ROOT and ROOT not in path.parents:
        raise ValueError(f"path escapes repository: {value}")
    return path


def git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def verify_summarizer_committed(paths: Sequence[Path]) -> str:
    if git_output("branch", "--show-current") != "main":
        raise RuntimeError("boundary review summarizer must run on main")
    relatives = [str(path.resolve().relative_to(ROOT)) for path in paths]
    dirty = git_output("status", "--short", "--", *relatives)
    if dirty:
        raise RuntimeError(f"boundary review summarizer inputs are not committed:\n{dirty}")
    if any(
        len(git_output("log", "-1", "--format=%H", "--", relative)) != 40
        for relative in relatives
    ):
        raise RuntimeError("could not resolve summarizer/config commits")
    return git_output("rev-parse", "HEAD")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def summarize(
    answers_path: Path,
    prereg_path: Path,
    output_dir: Path,
    *,
    require_complete: bool = True,
) -> dict[str, Any]:
    answers_path = answers_path.resolve()
    prereg_path = prereg_path.resolve()
    output_dir = output_dir.resolve()
    summarizer_commit = verify_summarizer_committed(
        [Path(__file__).resolve(), MODULE_PATH, prereg_path]
    )
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    source_path = repo_path(prereg["source"]["candidate_manifest_path"])
    protocol_path = repo_path(prereg["protocol"]["path"])
    if sha256_file(source_path) != prereg["source"]["candidate_manifest_sha256"]:
        raise ValueError("source candidate manifest hash drifted")
    if sha256_file(protocol_path) != prereg["protocol"]["sha256"]:
        raise ValueError("Local Signal V2 protocol hash drifted")
    payload = json.loads(answers_path.read_text(encoding="utf-8"))
    source_rows = read_jsonl(source_path)
    validation = validate_export(
        payload,
        source_rows=source_rows,
        prereg=prereg,
        require_complete=require_complete,
    )

    building = output_dir.with_name(f"{output_dir.name}.building")
    if output_dir.exists() or building.exists():
        raise FileExistsError(
            f"refusing to overwrite review summary: final={output_dir} building={building}"
        )
    building.mkdir(parents=True)
    joined_path = building / "review_joined.jsonl"
    preview_path = building / "short_keep_release_preview.jsonl"
    write_jsonl(joined_path, list(validation.joined_rows))
    preview = [
        row
        for row in validation.joined_rows
        if row["eligible_for_later_owner_release_preview"]
    ]
    write_jsonl(preview_path, preview)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": prereg["experiment_id"],
        "summarizer_commit": summarizer_commit,
        "status": validation.summary["status"],
        "inputs": {
            "answers_path": str(answers_path),
            "answers_sha256": sha256_file(answers_path),
            "preregistration_path": str(prereg_path.relative_to(ROOT)),
            "preregistration_sha256": sha256_file(prereg_path),
            "source_manifest_path": str(source_path.relative_to(ROOT)),
            "source_manifest_sha256": sha256_file(source_path),
            "protocol_sha256": sha256_file(protocol_path),
        },
        **validation.summary,
        "outputs": {
            "review_joined": "review_joined.jsonl",
            "review_joined_sha256": sha256_file(joined_path),
            "short_keep_release_preview": "short_keep_release_preview.jsonl",
            "short_keep_release_preview_sha256": sha256_file(preview_path),
            "short_keep_release_preview_rows": len(preview),
            "training_images": 0,
            "yolo_labels": 0,
            "negatives": 0,
            "models": 0,
        },
        "release_gate": {
            "owner_dataset_release_received": False,
            "training_eligible": False,
            "production_eligible": False,
            "next_action": "Owner reviews this summary and explicitly decides whether to release the audited SHORT KEEP geometry for a separately preregistered Gold Dataset build.",
        },
    }
    (building / "review_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(building, output_dir)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="validate a progress export; default requires all 9,000 answers",
    )
    args = parser.parse_args()
    summary = summarize(
        args.answers,
        args.prereg,
        args.out,
        require_complete=not args.allow_incomplete,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

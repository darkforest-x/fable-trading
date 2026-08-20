#!/usr/bin/env python3
"""Build or summarize the 2,649-row original-source keep/remove review.

This tool copies only existing source review images.  It does not read OHLC,
holdout rows, or the W10 classifier images, and it never mutates the frozen
dataset or changes training eligibility.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from yoyo.datasets.fixed_w10_original_review import (
    DEFAULT_SEED,
    build_original_review,
    summarize_export,
)


DEFAULT_DATASET = PROJECT / "datasets" / "fixed_w10_core4_confirm1_v1"
DEFAULT_PACK = DEFAULT_DATASET / "review" / "original_source_triage_v1"
DEFAULT_LEGACY_YOYO = PROJECT.parent / "yoyo-trading"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="resolve all original images and build the page")
    build.add_argument("--project-root", type=Path, default=PROJECT)
    build.add_argument("--legacy-yoyo-root", type=Path, default=DEFAULT_LEGACY_YOYO)
    build.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET)
    build.add_argument("--pack-root", type=Path, default=DEFAULT_PACK)
    build.add_argument("--seed", type=int, default=DEFAULT_SEED)

    summary = sub.add_parser("summarize", help="validate and summarize an exported JSON")
    summary.add_argument("--pack-root", type=Path, default=DEFAULT_PACK)
    summary.add_argument("--answers", type=Path, required=True)
    return ap


def main() -> int:
    args = parser().parse_args()
    if args.command == "build":
        result = build_original_review(
            args.project_root,
            args.legacy_yoyo_root,
            args.dataset_root,
            args.pack_root,
            seed=args.seed,
        )
    else:
        result = summarize_export(args.pack_root, args.answers)
        result = {key: value for key, value in result.items() if key != "joined_rows"}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

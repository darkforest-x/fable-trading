#!/usr/bin/env python3
"""Build or score the fixed-W10 P0/P1 blind audit without holdout access.

Build validates every image hash, freezes 398 unique stratified items (including
188 final-DIRECT rows), adds 50 indistinguishable repeats, and creates a
separate 28-item Cleanlab priority queue.  It never trains and never reads
market rows after the already-rendered decision-visible W10 image.

Reproduction::

    python3 tools/datasets/fixed_w10_p1_audit.py build
    python3 tools/datasets/fixed_w10_p1_audit.py score --answers /path/to/export.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.datasets.fixed_w10_blind_audit import (
    DEFAULT_SEED,
    build_audit,
    score_audit,
)


PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = PROJECT / "datasets" / "fixed_w10_core4_confirm1_v1"
DEFAULT_PACK = DEFAULT_DATASET / "review" / "p1_blind_audit_v1"
DEFAULT_CLEANLAB = (
    PROJECT
    / "experiments"
    / "active"
    / "exp-p1-gold-label-quality-cleanlab-v1"
    / "per_image_test.jsonl"
)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="validate lineage and build both review packs")
    build.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET)
    build.add_argument("--pack-root", type=Path, default=DEFAULT_PACK)
    build.add_argument("--cleanlab-per-image", type=Path, default=DEFAULT_CLEANLAB)
    build.add_argument("--seed", type=int, default=DEFAULT_SEED)

    score = sub.add_parser("score", help="score a complete public answer export")
    score.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET)
    score.add_argument("--pack-root", type=Path, default=DEFAULT_PACK)
    score.add_argument("--answers", type=Path, required=True)
    return ap


def main() -> int:
    args = parser().parse_args()
    if args.command == "build":
        result = build_audit(
            args.dataset_root,
            args.pack_root,
            args.cleanlab_per_image,
            seed=args.seed,
        )
    else:
        result = score_audit(args.dataset_root, args.pack_root, args.answers)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Plan or materialize an Owner-approved 10k-positive YOLO dataset version."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.datasets.ma_launch_owner_yolo_dataset import (
    DEFAULT_DATASET,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    build_dataset,
    plan_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Select and audit all negatives without writing training images.",
    )
    args = parser.parse_args()
    if args.plan_only:
        result = plan_dataset(prereg_path=args.prereg, results_path=args.results)
    else:
        result = build_dataset(
            prereg_path=args.prereg,
            dataset_path=args.dataset,
            results_path=args.results,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

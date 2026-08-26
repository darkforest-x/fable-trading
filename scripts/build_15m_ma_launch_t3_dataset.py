#!/usr/bin/env python3
"""Plan or build the Owner-authorized 10,000-candidate t-3 YOLO dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.datasets.ma_launch_t3_training import (
    DEFAULT_DATASET,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    ROOT,
    build_dataset,
    verify_builder_committed,
)


BUILDER_PATHS = (
    ROOT / "yoyo" / "datasets" / "ma_launch_t3_training.py",
    Path(__file__).resolve(),
    ROOT / "tests" / "test_ma_launch_t3_training.py",
    DEFAULT_PREREG,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="verify all positive/negative capacity and write no training images",
    )
    args = parser.parse_args()
    commit = verify_builder_committed((*BUILDER_PATHS[:-1], args.prereg.resolve()))
    summary = build_dataset(
        prereg_path=args.prereg.resolve(),
        dataset_path=args.dataset.resolve(),
        results_path=args.results.resolve(),
        materialize=not args.plan_only,
        builder_commit=commit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Plan or materialize the nuisance-matched Grade-A 8k + negative 24k dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.datasets.ma_launch_owner_grade_a_negatives import (
    DEFAULT_DATASET,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    build_dataset,
    plan_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    result = (
        plan_dataset(prereg_path=args.prereg, results_path=args.results)
        if args.plan_only
        else build_dataset(
            prereg_path=args.prereg,
            results_path=args.results,
            dataset_path=args.dataset,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

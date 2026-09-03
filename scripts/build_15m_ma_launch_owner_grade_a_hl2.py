#!/usr/bin/env python3
"""Build the paired Grade-A 32,000-image HL2 six-MA treatment dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.datasets.ma_launch_owner_grade_a_hl2 import (
    DEFAULT_DATASET,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    build,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    result = build(
        args.prereg,
        results_dir=args.results,
        dataset_dir=args.dataset,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

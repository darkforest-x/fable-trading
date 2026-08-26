#!/usr/bin/env python3
"""Build the immutable 15m MA-launch hard-negative validation sidecar."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.datasets.ma_launch_t3_hard_val import (
    DEFAULT_DATASET,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    build_hard_val,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--page-size", type=int, default=100)
    args = parser.parse_args()
    receipt = build_hard_val(
        args.prereg.resolve(),
        dataset_path=args.dataset.resolve(),
        results_path=args.results.resolve(),
        page_size=args.page_size,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

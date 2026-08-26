#!/usr/bin/env python3
"""Build the non-destructive 15m t-3 causal/review parity gallery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.datasets.ma_launch_review_parity import ROOT, build_review_parity


DEFAULT_EXPERIMENT = ROOT / "experiments/active/exp-15m-ma-launch-t3-review-parity-v2"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prereg",
        type=Path,
        default=DEFAULT_EXPERIMENT / "preregistration.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_EXPERIMENT / "results",
    )
    parser.add_argument("--page-size", type=int, default=250)
    args = parser.parse_args()
    if args.page_size <= 0:
        parser.error("--page-size must be positive")
    receipt = build_review_parity(
        args.prereg.resolve(), output_dir=args.out.resolve(), page_size=args.page_size
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()


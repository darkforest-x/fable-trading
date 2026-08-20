#!/usr/bin/env python3
"""Build or summarize the uniform 2,649-row causal-OHLC review pack.

The builder re-anchors events by exact decision_time, streams only pre-holdout
OHLC rows through the latest requested decision, and renders a fixed causal
W200 review surface.  It does not use migrated W10 pixels/geometry, train a
model, mutate the frozen dataset, or change eligibility.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from yoyo.datasets.fixed_w10_canonical_review import (
    DEFAULT_SEED,
    WINDOW_BARS,
    build_canonical_review,
    summarize_export,
)


DEFAULT_DATASET = PROJECT / "datasets" / "fixed_w10_core4_confirm1_v1"
DEFAULT_PACK = DEFAULT_DATASET / "review" / "canonical_ohlc_triage_v2"
DEFAULT_ARCHIVE = PROJECT / "archive" / "consolidated"
DEFAULT_DATA = PROJECT / "data" / "kline_fetched"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="render uniform causal OHLC images and build page")
    build.add_argument("--project-root", type=Path, default=PROJECT)
    build.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE)
    build.add_argument("--data-root", type=Path, default=DEFAULT_DATA)
    build.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET)
    build.add_argument("--pack-root", type=Path, default=DEFAULT_PACK)
    build.add_argument("--seed", type=int, default=DEFAULT_SEED)
    build.add_argument("--window-bars", type=int, default=WINDOW_BARS)

    summary = sub.add_parser("summarize", help="validate and summarize exported JSON")
    summary.add_argument("--pack-root", type=Path, default=DEFAULT_PACK)
    summary.add_argument("--answers", type=Path, required=True)
    return ap


def main() -> int:
    args = parser().parse_args()
    if args.command == "build":
        result = build_canonical_review(
            args.project_root,
            args.archive_root,
            args.data_root,
            args.dataset_root,
            args.pack_root,
            seed=args.seed,
            window_bars=args.window_bars,
        )
    else:
        result = summarize_export(args.pack_root, args.answers)
        result = {key: value for key, value in result.items() if key != "joined_rows"}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

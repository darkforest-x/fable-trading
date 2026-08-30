#!/usr/bin/env python3
"""Fetch checksum-verified Binance USD-M 15m history below the holdout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.data.binance_um_archives import fetch_universe


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "kline_preholdout_binance_um15m",
    )
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--interval", default="15m", help="Binance kline interval, e.g. 5m")
    args = parser.parse_args()
    summary = fetch_universe(
        output_dir=args.output_dir,
        archive_start="2019-09-01T00:00:00Z",
        archive_end_inclusive="2026-04-30T23:59:59Z",
        archive_max_exclusive="2026-05-01T00:00:00Z",
        holdout_start="2026-05-04T00:00:00Z",
        workers=args.workers,
        interval=args.interval,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

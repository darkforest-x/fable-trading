# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lightgbm>=4.0",
#   "numpy>=1.24",
#   "pandas>=2.0",
# ]
# ///
# --- How to run ---
# PYTHONPATH=. python3 scripts/forward_track.py
"""Forward validation runner for the frozen tp5_sl2 SWAP model.

Reads current OKX SWAP data, records threshold signals from the configured
forward start, and fills exits for previously open rows without retraining.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.judgment.forward import (
    FORWARD_LOG_PATH,
    FORWARD_START,
    normalize_start_time,
    run_forward_tracking,
    summary_to_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=FORWARD_LOG_PATH)
    parser.add_argument("--start", default=str(FORWARD_START))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = run_forward_tracking(output_path=args.out, start_time=normalize_start_time(pd.Timestamp(args.start)))
    print(summary_to_json(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

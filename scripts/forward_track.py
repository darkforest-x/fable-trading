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
    start = normalize_start_time(pd.Timestamp(args.start))
    # Ops breadcrumb: detect FORWARD_START-ahead-of-data races (incomplete bar).
    try:
        from src.data.loader import list_series, load_series

        sample_last = None
        for (src, sym), paths in list_series(bar="15m").items():
            if src == "okx" and sym.endswith("_USDT_SWAP"):
                frame = load_series(paths)
                if frame is not None and len(frame):
                    sample_last = pd.Timestamp(frame["open_time"].iloc[-1])
                    if sample_last.tzinfo is None:
                        sample_last = sample_last.tz_localize("UTC")
                    break
        if sample_last is not None:
            lag_min = (start - sample_last).total_seconds() / 60.0
            print(
                f"forward_clock: start={start} sample_last_bar={sample_last} "
                f"start_minus_last_min={lag_min:.1f}",
                flush=True,
            )
            if lag_min > 0:
                print(
                    "forward_clock: WARNING start is ahead of last closed bar "
                    "(incomplete candle); tip scan still runs after 2026-07-19 fix",
                    flush=True,
                )
    except Exception as exc:  # noqa: BLE001
        print(f"forward_clock: probe skipped ({exc})", flush=True)

    summary = run_forward_tracking(output_path=args.out, start_time=start)
    print(summary_to_json(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

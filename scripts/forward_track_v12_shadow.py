# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lightgbm>=4.0",
#   "numpy>=1.24",
#   "pandas>=2.0",
# ]
# ///
# --- How to run ---
# PYTHONPATH=. .venv/bin/python scripts/forward_track_v12_shadow.py
"""H-TIP v12 detector SHADOW forward logger (parallel paper book).

Single variables vs mainline:
  - YOLO weights = models/owner_v12_htip.pt (or --weights)
  - scan mode = tip (1 rightmost window only)

Unchanged: judgment freeze (v11 reg), val-q90 threshold, TP5/SL2 exits,
FORWARD_START, stockish filter. Writes only to data/forward_log_v12_shadow.csv.

Does NOT promote owner_best, does NOT touch data/forward_log.csv.
See analysis/week_plan_20260720.md D2–D3.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.judgment.forward import (
    DEFAULT_V12_WEIGHTS,
    FORWARD_LOG_PATH,
    FORWARD_LOG_V12_SHADOW_PATH,
    FORWARD_START,
    normalize_start_time,
    run_forward_tracking_v12_shadow,
    summary_to_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="v12 tip-only YOLO shadow forward track (side log; mainline untouched)."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=FORWARD_LOG_V12_SHADOW_PATH,
        help=f"shadow log path (default: {FORWARD_LOG_V12_SHADOW_PATH})",
    )
    parser.add_argument("--start", default=str(FORWARD_START))
    parser.add_argument(
        "--weights",
        type=Path,
        default=None,
        help=f"YOLO weights (default: {DEFAULT_V12_WEIGHTS} or run best.pt fallback)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out = Path(args.out)
    if out.resolve() == Path(FORWARD_LOG_PATH).resolve():
        print(
            json.dumps(
                {
                    "error": "refusing_mainline_log",
                    "message": (
                        "v12 shadow must not write to data/forward_log.csv. "
                        f"Default is {FORWARD_LOG_V12_SHADOW_PATH}."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    summary = run_forward_tracking_v12_shadow(
        output_path=out,
        start_time=normalize_start_time(pd.Timestamp(args.start)),
        yolo_weights=args.weights,
    )
    payload = summary_to_json(summary)
    payload["shadow"] = "v12_htip_tip_only"
    payload["note"] = (
        "detector=v12 tip-window; judgment=mainline freeze; not counted in 0/100"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

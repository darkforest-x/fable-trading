# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lightgbm>=4.0",
#   "numpy>=1.24",
#   "pandas>=2.0",
# ]
# ///
# --- How to run ---
# PYTHONPATH=. python3 scripts/forward_track_h1_shadow.py
"""H1 scaled-exit SHADOW forward logger (parallel paper book).

Same mainline freeze for entries (candidates + score + val-q90 threshold);
outcomes resolved with scaled 2.5 bank + 3 trail math. Writes only to
`data/forward_log_h1_scaled_ma206.csv` — never replaces mainline `forward_log_ma206.csv`
or the TP5/SL2 frozen model path.

See docs/H1_SCALED_FORWARD_SHADOW_PLAN.md.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.judgment.forward import (
    FORWARD_LOG_H1_SCALED_PATH,
    FORWARD_LOG_PATH,
    FORWARD_START,
    normalize_start_time,
    run_forward_tracking_h1_shadow,
    summary_to_json,
)

EXIT_FAMILY = "scaled_25_t3"
# Honest note for operators / digest tooling.
SHADOW_MODE = "main_freeze_entries_scaled_exits"
SCALED_STUB_NOTE = (
    "models/frozen_scaled_25_t3_2026-07-09.json is a lightweight stub "
    "(missing feature_columns / dataset_path / full SHA); load_artifact rejects it. "
    "This shadow run scores entries with mainline frozen_tp5_sl2_swap_ma206 and labels "
    "exits via resolve_forward_exit_scaled (label_candidate_scaled math)."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="H1 scaled shadow forward track (side log only; mainline untouched)."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=FORWARD_LOG_H1_SCALED_PATH,
        help=f"shadow log path (default: {FORWARD_LOG_H1_SCALED_PATH})",
    )
    parser.add_argument("--start", default=str(FORWARD_START))
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
                        "H1 shadow must not write to data/forward_log_ma206.csv. "
                        f"Default is {FORWARD_LOG_H1_SCALED_PATH}."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    summary = run_forward_tracking_h1_shadow(
        output_path=out,
        start_time=normalize_start_time(pd.Timestamp(args.start)),
    )
    payload = json.loads(summary_to_json(summary))
    payload["shadow"] = True
    payload["exit_family"] = EXIT_FAMILY
    payload["shadow_mode"] = SHADOW_MODE
    payload["scaled_artifact_note"] = SCALED_STUB_NOTE
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

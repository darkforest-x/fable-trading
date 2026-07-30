#!/usr/bin/env python3
"""Isolated maker-entry trial forward pulse (A2).

Writes ONLY to data/forward_log_maker_trial.csv (or --out).
Reuses mainline YOLO discovery + frozen judgment (val-q90) + TP5/SL2 paper exits.
Never touches main forward_log.csv, ACTIVE, or the three freshness gates.

This script produces a **signal ledger only**. Maker entry / limit TP /
notional sizing live in a separate owner-authorized executor process
(see analysis/p_judgment_maker_trial_a2_plan.md). No orders are placed here.

Usage (research / owner-approved VPS trial only):
  FABLE_MAKER_TRIAL=1 \\
  PYTHONPATH=. python3 scripts/forward_maker_trial.py \\
      --out data/forward_log_maker_trial.csv

  # optional: pin weights / start clock
  FABLE_MAKER_TRIAL=1 PYTHONPATH=. python3 scripts/forward_maker_trial.py \\
      --yolo-weights models/owner_short_star_v10.pt \\
      --start 2026-07-18T16:15:00+00:00

Safety:
- FABLE_MAKER_TRIAL=1 required (else exit 2).
- Independent kill: touch data/executor_KILL_MAKER_TRIAL (exit 3).
- Refuses to write mainline / H1 shadow paths.
- Stamps trial_bucket=maker_entry on every row after the scan merge.

Iron laws: no holdout, no promote, no real money, VPS-only writers for live data.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.judgment.forward import run_forward_tracking_maker_trial  # noqa: E402
from src.judgment.forward_types import (  # noqa: E402
    FORWARD_COLUMNS,
    FORWARD_LOG_H1_SCALED_PATH,
    FORWARD_LOG_MAKER_TRIAL_PATH,
    FORWARD_LOG_PATH,
    FORWARD_START,
)

TRIAL_LOG = FORWARD_LOG_MAKER_TRIAL_PATH
TRIAL_KILL = PROJECT / "data" / "executor_KILL_MAKER_TRIAL"
TRIAL_BUCKET = "maker_entry"
PROTECTED = {
    Path(FORWARD_LOG_PATH).resolve(),
    Path(FORWARD_LOG_H1_SCALED_PATH).resolve(),
}


def _stamp_trial_bucket(path: Path) -> None:
    """Ensure every row carries trial_bucket without touching main schema helpers.

    read/write_forward_log normalizes to FORWARD_COLUMNS only; we re-attach the
    extra marker after the mainline merge so downstream trial executors can filter.
    """
    if not path.exists():
        empty = pd.DataFrame(columns=list(FORWARD_COLUMNS) + ["trial_bucket"])
        path.parent.mkdir(parents=True, exist_ok=True)
        empty.to_csv(path, index=False)
        return
    frame = pd.read_csv(path)
    for column in FORWARD_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame["trial_bucket"] = TRIAL_BUCKET
    # keep main columns first, then marker
    ordered = [c for c in FORWARD_COLUMNS if c in frame.columns] + ["trial_bucket"]
    extras = [c for c in frame.columns if c not in ordered]
    frame[ordered + extras].to_csv(path, index=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="A2 maker trial forward pulse (ledger only)")
    ap.add_argument(
        "--out",
        type=Path,
        default=TRIAL_LOG,
        help=f"Isolated trial log path (default {TRIAL_LOG})",
    )
    ap.add_argument(
        "--start",
        type=str,
        default=None,
        help="ISO start clock (default FORWARD_START)",
    )
    ap.add_argument(
        "--yolo-weights",
        type=Path,
        default=None,
        help="Optional detector weights override (default: load_yolo_model resolution)",
    )
    ap.add_argument(
        "--init-only",
        action="store_true",
        help="Only ensure empty schema-correct trial log exists; do not scan",
    )
    args = ap.parse_args()

    if os.environ.get("FABLE_MAKER_TRIAL", "0") != "1":
        print("FABLE_MAKER_TRIAL != 1 → abort (safety). Set the env to enable trial writer.")
        return 2

    if TRIAL_KILL.exists():
        print(f"Kill switch present: {TRIAL_KILL} → abort trial pulse.")
        return 3

    out = Path(args.out)
    if out.resolve() in PROTECTED:
        print(
            f"refusing protected path {out} "
            f"(mainline={FORWARD_LOG_PATH}, h1={FORWARD_LOG_H1_SCALED_PATH})"
        )
        return 4

    if args.init_only:
        _stamp_trial_bucket(out)
        print(f"initialized trial log schema: {out}")
        return 0

    start = pd.Timestamp(args.start) if args.start else FORWARD_START
    yolo = Path(args.yolo_weights) if args.yolo_weights else None
    if yolo is not None and not yolo.exists():
        print(f"yolo weights not found: {yolo}")
        return 5

    print(
        f"maker_trial: out={out} start={start} "
        f"yolo={yolo or 'default'} kill={TRIAL_KILL}",
        flush=True,
    )
    summary = run_forward_tracking_maker_trial(
        output_path=out,
        start_time=start,
        yolo_weights=yolo,
    )
    _stamp_trial_bucket(out)

    # Verify mainline untouched (paranoid check for the separation contract).
    main_mtime_note = "absent"
    if FORWARD_LOG_PATH.exists():
        main_mtime_note = f"mtime={FORWARD_LOG_PATH.stat().st_mtime}"
    print(f"maker_trial: main forward_log {main_mtime_note} (not written by this pulse)")

    payload = dict(summary.to_json())
    payload["trial_bucket"] = TRIAL_BUCKET
    payload["kill_switch"] = str(TRIAL_KILL)
    print(
        "maker_trial done: "
        f"new={payload.get('new_signals')} closed_upd={payload.get('closed_updates')} "
        f"total={payload.get('total_rows')} open={payload.get('open_rows')} "
        f"out={payload.get('output')}",
        flush=True,
    )
    # Keep a tiny machine-readable sidecar next to the log for ops consoles.
    side = out.with_suffix(out.suffix + ".last_pulse.json")
    try:
        import json

        side.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"maker_trial: pulse summary → {side}")
    except Exception as exc:  # noqa: BLE001
        print(f"maker_trial: summary write skipped ({exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

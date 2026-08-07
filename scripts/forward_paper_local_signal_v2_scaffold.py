#!/usr/bin/env python3
"""P3 scaffold only: paper/forward wiring for local_signal_v2 Stage-B detector.

Does **not** touch mainline ``data/forward_log.csv``, ACTIVE, or owner_best.
Writes to a dedicated shadow/paper log path. Owner auth 2026-08-07: P3 =
paper/forward scaffold only.

This is intentionally thin: once a Stage-B best.pt exists, point --weights at it
and run tip-only scans into ``data/forward_log_lsv2_paper.csv`` with
``execution_eligible=false``.

Usage:
  PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/forward_paper_local_signal_v2_scaffold.py --check
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
PAPER_LOG = PROJECT / "data" / "forward_log_lsv2_paper.csv"
STATUS = PROJECT / "analysis" / "output" / "lsv2_paper_status.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument(
        "--weights",
        type=Path,
        default=PROJECT
        / "analysis"
        / "output"
        / "lsv2_stageb"
        / "owner_lsv2_stageb_cold"
        / "weights"
        / "best.pt",
    )
    args = ap.parse_args()

    status = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "phase": "P3_scaffold",
        "execution_eligible": False,
        "active_untouched": True,
        "owner_best_untouched": True,
        "mainline_forward_log_untouched": True,
        "paper_log": str(PAPER_LOG),
        "weights": str(args.weights),
        "weights_exist": args.weights.is_file(),
        "protocol": "local_signal_v2_paper_scaffold_20260807",
        "notes": [
            "Scaffold only — reuses tip scan + barrier book patterns from w20 shadow.",
            "Wire full scanner after P1 best.pt lands; never auto-promote.",
        ],
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(status, indent=2))
    print(json.dumps(status, indent=2))
    if args.check and not args.weights.is_file():
        print("weights missing — expected after P1 train; scaffold OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

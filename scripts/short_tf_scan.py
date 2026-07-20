#!/usr/bin/env python3
"""Short-TF tip rules scan (1m/5m majors).

  PYTHONPATH=. python3 scripts/short_tf_scan.py --once
  PYTHONPATH=. python3 scripts/short_tf_scan.py --once --dry-run
  PYTHONPATH=. python3 scripts/short_tf_scan.py --loop --interval 60
  PYTHONPATH=. python3 scripts/short_tf_scan.py --once --notify   # TG

Never writes data/forward_log.csv. Never opens OKX orders.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.short_tf.scan import run_loop, run_once  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--once", action="store_true")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--dry-run", action="store_true", help="scan only, no log write")
    p.add_argument("--notify", action="store_true", help="also push TG for new tip hits")
    p.add_argument("--symbols", default="", help="comma symbols override")
    p.add_argument("--bars", default="1m,5m")
    args = p.parse_args()
    bars = tuple(b.strip() for b in args.bars.split(",") if b.strip())
    kwargs = {"dry_run": args.dry_run, "notify": args.notify, "bars": bars}
    if args.symbols:
        kwargs["symbols"] = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    if args.loop:
        run_loop(interval_sec=args.interval, dry_run=args.dry_run, notify=args.notify)
        return 0
    summary = run_once(**kwargs)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

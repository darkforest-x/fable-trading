#!/usr/bin/env python3
"""ETH micro live monitor → TG. Requires prior eth_micro_backtest models.

  PYTHONPATH=. python3 scripts/eth_micro_monitor.py --once
  PYTHONPATH=. python3 scripts/eth_micro_monitor.py --loop --interval 60
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.eth_micro.monitor import run_loop, run_once  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if args.loop:
        run_loop(interval_sec=args.interval, dry_run=args.dry_run)
        return 0
    summary = run_once(dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

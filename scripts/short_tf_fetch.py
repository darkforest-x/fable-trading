#!/usr/bin/env python3
"""Fetch 1m/5m history for short_tf major SWAP symbols (resumable via fetch_okx).

  PYTHONPATH=. python3 scripts/short_tf_fetch.py --bar 5m --days 30
  PYTHONPATH=. python3 scripts/short_tf_fetch.py --bar 1m --days 7
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.short_tf.config import SYMBOLS  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bar", default="5m", choices=("1m", "5m", "15m"))
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--symbols", default="", help="comma override; default short_tf majors")
    args = p.parse_args()
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()] or list(SYMBOLS)
    cmd = [
        sys.executable,
        "-m",
        "src.data.fetch_okx",
        "--bar",
        args.bar,
        "--days",
        str(args.days),
        "--workers",
        str(args.workers),
        "--symbols",
        *syms,
    ]
    print(" ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(PROJECT), env={**dict(**__import__("os").environ), "PYTHONPATH": str(PROJECT)})


if __name__ == "__main__":
    raise SystemExit(main())

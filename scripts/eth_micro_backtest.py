#!/usr/bin/env python3
"""Run ETH micro-channel (1/2/3/5m) backtest and write data/eth_micro/backtest_summary.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.eth_micro.backtest import run_all  # noqa: E402


def main() -> int:
    payload = run_all()
    print(json.dumps({
        "best_bar": payload.get("best_bar_by_top_net"),
        "n_results": len(payload.get("results") or []),
        "out": "data/eth_micro/backtest_summary.json",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

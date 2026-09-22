"""Command-line entry point for the receipt-bound SPIKE joint BTC gate study.

The implementation lives with the causal gate primitives in
``spike_joint_btc_gate`` so the runner and its audit fields cannot drift.
This small entry point is retained as the experiment's stable runnable name.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from yoyo.evaluation.spike_joint_btc_gate import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--allow-uncommitted", action="store_true", help="explicit smoke subset only")
    args = parser.parse_args()
    run(args.output, workers=args.workers, symbols=args.symbols, allow_uncommitted=args.allow_uncommitted)


if __name__ == "__main__":
    main()

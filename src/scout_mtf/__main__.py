"""CLI: python3 -m src.scout_mtf [--top 12] [--no-loss] [--max-symbols 10]

Side-branch multi-TF radar over OKX SWAP 24h movers.
"""
from __future__ import annotations

import argparse
import json
import sys

from src.scout_mtf.pipeline import format_table, run_scout
from src.scout_mtf.tf_scan import TIMEFRAMES


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Multi-TF rank scout (1/3/5/15/30m) — side branch")
    p.add_argument("--top", type=int, default=12, help="top N gainers and top N losers")
    p.add_argument("--min-vol", type=float, default=5_000_000, help="min 24h quote volume (USDT)")
    p.add_argument("--no-loss", action="store_true", help="only top gainers")
    p.add_argument("--max-symbols", type=int, default=None, help="cap total symbols scanned")
    p.add_argument("--json", action="store_true", help="print full JSON to stdout")
    p.add_argument("--limit", type=int, default=40, help="table rows")
    args = p.parse_args(argv)

    report = run_scout(
        top_n=args.top,
        min_vol_usdt=args.min_vol,
        include_loss=not args.no_loss,
        max_symbols=args.max_symbols,
        bars=TIMEFRAMES,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_table(report, limit=args.limit))
        print(f"\nwrote {report.get('output_path')}")
        print(report.get("disclaimer"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lightgbm>=4.0",
#   "numpy>=1.24",
#   "pandas>=2.0",
# ]
# ///
# --- How to run ---
# PYTHONPATH=. python3 scripts/forward_track_shadows.py
# PYTHONPATH=. python3 scripts/forward_track_shadows.py --compare-only
"""Run predeclared champion + supported challenger forward books.

Unsupported books (H8 30m, H10 short without freezes) are reported, never
approximated. ACTIVE / mainline user path is never rewritten from challenger PnL.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.judgment.forward import FORWARD_START, normalize_start_time, summary_to_json
from src.judgment.shadow_compare import compare_shadow_books, format_comparison_text
from src.judgment.shadow_registry import (
    get_shadow_book,
    list_shadow_books,
    registry_snapshot,
    resolve_runner,
    supported_books,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prospective champion/challenger shadow forward tracking."
    )
    parser.add_argument(
        "--compare-only",
        action="store_true",
        help="Read existing logs only; do not scan markets or write.",
    )
    parser.add_argument(
        "--books",
        nargs="*",
        default=None,
        help="Optional subset of book names (default: all supported runners).",
    )
    parser.add_argument("--start", default=str(FORWARD_START))
    return parser.parse_args()


def _run_supported(names: list[str] | None, start: pd.Timestamp) -> list[dict]:
    results: list[dict] = []
    targets = supported_books()
    if names:
        wanted = set(names)
        targets = tuple(b for b in targets if b.name in wanted)
        unknown = wanted - {b.name for b in list_shadow_books()}
        if unknown:
            raise SystemExit(f"unknown book names: {sorted(unknown)}")
        # Explicit unsupported request → report only.
        for name in names:
            book = get_shadow_book(name)
            if book.status == "unsupported":
                results.append(
                    {
                        "name": book.name,
                        "status": "unsupported",
                        "unsupported_reason": book.unsupported_reason,
                        "ran": False,
                    }
                )
    for book in targets:
        runner = resolve_runner(book)
        summary = runner(output_path=book.log_path, start_time=start)
        payload = json.loads(summary_to_json(summary))
        payload["name"] = book.name
        payload["role"] = book.role
        payload["status"] = "supported"
        payload["ran"] = True
        payload["promotes_active"] = False
        payload["exit_family"] = book.exit_family
        results.append(payload)
    return results


def main() -> int:
    args = parse_args()
    start = normalize_start_time(pd.Timestamp(args.start))
    payload: dict = {
        "registry": registry_snapshot(),
        "promotes_active": False,
        "evidence_class": "prospective_forward_observation",
    }
    if not args.compare_only:
        payload["runs"] = _run_supported(args.books, start)
    else:
        payload["runs"] = []
        if args.books:
            for name in args.books:
                book = get_shadow_book(name)
                payload["runs"].append(
                    {
                        "name": book.name,
                        "status": book.status,
                        "unsupported_reason": book.unsupported_reason or None,
                        "ran": False,
                        "compare_only": True,
                    }
                )
    comparison = compare_shadow_books()
    payload["comparison"] = comparison
    payload["comparison_text"] = format_comparison_text(comparison)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

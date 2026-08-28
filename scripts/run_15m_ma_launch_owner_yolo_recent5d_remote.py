#!/usr/bin/env python3
"""Run the committed five-day Owner YOLO scan in a disposable compute bundle.

The Mac has already performed and receipted the only authorized network read.
This runner accepts only that hash-bound snapshot bundle plus the frozen model,
manifest, renderer and preregistration.  A source commit must be supplied so
the remote receipt never invents Git state on a worker that intentionally has
no repository metadata.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts import scan_15m_ma_launch_t3_daily_movers as common
from scripts.scan_15m_ma_launch_owner_yolo_recent5d import (
    DEFAULT_OUT,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    load_preregistration,
    verify_immutable_inputs,
)


def commit_sha(value: str) -> str:
    """Accept one exact lowercase 40-character Git SHA."""

    text = str(value)
    if len(text) != 40 or any(char not in "0123456789abcdef" for char in text):
        raise argparse.ArgumentTypeError("--source-commit must be a lowercase 40-char SHA")
    return text


def main() -> int:
    """Validate the staged bundle, run CUDA inference and render its boards."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True, type=commit_sha)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--device", default="0")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    prereg = load_preregistration(args.prereg.resolve())
    verify_immutable_inputs(prereg)
    common.scan_and_render(
        prereg,
        out=args.out.resolve(),
        results=args.results.resolve(),
        device=args.device,
        batch_size=args.batch_size,
        source_commit=args.source_commit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

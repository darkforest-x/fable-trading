#!/usr/bin/env python3
"""Build the versioned Stage-B dataset with strict-time negative windows.

This is the corrected successor to ``local_signal_v2_stageb``.  It reuses the
V1 positive renderer and event split, but requires every train/val negative
window to stay inside the corresponding positive time block.  The V1 entrypoint
and dataset remain unchanged for historical reproducibility.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.build_local_signal_v2_stageb import (
    DEFAULT_SRC_MANIFEST,
    PROJECT,
    STRICT_NEG_PROTOCOL,
    WIN_MAX,
    WIN_MIN,
    run_preview,
    run_full,
)

DEFAULT_OUT = PROJECT / "datasets" / "local_signal_v2_stageb_strictneg_v2"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src-manifest", type=Path, default=DEFAULT_SRC_MANIFEST)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=20260807)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--neg-ratio", type=float, default=1.0)
    ap.add_argument("--fixed-window-len", type=int, choices=range(WIN_MIN, WIN_MAX + 1))
    ap.add_argument("--preview", type=int, default=0)
    ap.add_argument(
        "--preview-dir",
        type=Path,
        default=PROJECT
        / "analysis"
        / "output"
        / "local_signal_v2_stageb_strictneg_v2_preview",
    )
    args = ap.parse_args()
    protocol = (
        STRICT_NEG_PROTOCOL
        if args.fixed_window_len is None
        else f"{STRICT_NEG_PROTOCOL}_w{args.fixed_window_len}"
    )
    if not args.src_manifest.exists():
        ap.error(f"missing source manifest: {args.src_manifest}")
    if args.preview > 0:
        run_preview(
            args.src_manifest,
            args.preview,
            args.preview_dir,
            args.seed,
            protocol=protocol,
            fixed_window_len=args.fixed_window_len,
        )
        return 0
    run_full(
        args.src_manifest,
        args.out,
        seed=args.seed,
        limit=args.limit,
        neg_ratio=args.neg_ratio,
        strict_negative_time_split=True,
        protocol=protocol,
        fixed_window_len=args.fixed_window_len,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

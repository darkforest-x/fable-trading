#!/usr/bin/env python3
"""Build the position-only Local-Signal V2 correction after B2 shortcut audit.

This arm keeps B2's 30 visible causal bars, Mode-C label geometry, source
events, time split, purge, seed, and 1:1 strict-time easy negatives.  Its only
experimental variable is an opt-in V2 canvas layout with 0--12 empty slots to
the right of the decision bar.  The slots contain no market rows, so
``visible_end == decision`` and ``future_bars == 0`` remain invariant while
positive box centers cover the handoff's Stage-B 65%--95% horizontal range.

The legacy renderer and all earlier datasets remain untouched.  This dataset
must pass P0 and a position histogram audit before any training starts.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.build_local_signal_v2_stageb import (
    DEFAULT_SRC_MANIFEST,
    PROJECT,
    STAGE_B_POSITION_MAX,
    STAGE_B_POSITION_MIN,
    run_full,
    run_preview,
)

PROTOCOL = "local_signal_v2_stageb_causal_blank_v3_20260811"
FIXED_WINDOW_LEN = 30
RIGHT_BLANK_RANGE = (0, 12)
TARGET_BOX_POSITION_RANGE = (STAGE_B_POSITION_MIN, STAGE_B_POSITION_MAX)
DEFAULT_OUT = PROJECT / "datasets" / "local_signal_v2_p1_causal_blank_w30_v3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src-manifest", type=Path, default=DEFAULT_SRC_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=20260807)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--neg-ratio", type=float, default=1.0)
    parser.add_argument("--preview", type=int, default=0)
    parser.add_argument(
        "--preview-dir",
        type=Path,
        default=PROJECT
        / "analysis"
        / "output"
        / "local_signal_v2_p1_causal_blank_w30_v3_preview",
    )
    args = parser.parse_args()
    if not args.src_manifest.exists():
        parser.error(f"missing source manifest: {args.src_manifest}")
    kwargs = {
        "protocol": PROTOCOL,
        "fixed_window_len": FIXED_WINDOW_LEN,
        "right_blank_range": RIGHT_BLANK_RANGE,
        "target_box_position_range": TARGET_BOX_POSITION_RANGE,
    }
    if args.preview > 0:
        run_preview(
            args.src_manifest,
            args.preview,
            args.preview_dir,
            args.seed,
            **kwargs,
        )
        return 0
    run_full(
        args.src_manifest,
        args.out,
        seed=args.seed,
        limit=args.limit,
        neg_ratio=args.neg_ratio,
        strict_negative_time_split=True,
        **kwargs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

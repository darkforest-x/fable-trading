#!/usr/bin/env python3
"""Thin CLI for the ETH 3m short-start pilot v2 dataset builder.

Implementation lives under ``src.detection.eth3m_v2_*`` per the v2a maintenance
plan.  This wrapper preserves the historical script imports used by tests.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.detection.eth3m_v2_evidence import (
    BAR_DELTA,
    BAR_MINUTES,
    CALIBRATION_COLUMNS,
    DEFAULT_CALIBRATION,
    DEFAULT_CALIBRATION_MOBILE_HTML,
    DEFAULT_DETAIL,
    DEFAULT_INPUT,
    DEFAULT_OUT,
    DETAIL_COLUMNS,
    FUTURE_BARS,
    HOLDOUT_START,
    MIN_LEAD_BARS,
    PROJECT,
    TARGET_TRAIN_FRACTION,
    WEAK_REVIEW_OFFSETS,
    WINDOW,
    _sha256,
    _utc,
    load_pre_holdout_ohlc,
    load_sources,
)
from src.detection.eth3m_v2_events import (
    SourceInterval,
    build_source_intervals,
    choose_purged_split,
    merge_calibration_events,
    merge_source_intervals,
)
from src.detection.eth3m_v2_render import build_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--mobile-html", type=Path, default=DEFAULT_CALIBRATION_MOBILE_HTML)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    meta, _ = build_dataset(
        input_path=args.input,
        detail_path=args.detail,
        calibration_path=args.calibration,
        mobile_html_path=args.mobile_html,
        out=args.out,
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

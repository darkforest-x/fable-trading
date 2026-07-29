#!/usr/bin/env python3
"""Audit ETH 3m v2 semantic quality without loading holdout or market bars."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.detection.eth3m_v2_quality_audit import audit_dataset_quality


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT / "datasets/eth_3m_short_pilot_v2/manifest.csv"
DEFAULT_TIMING = (
    PROJECT / "analysis/output/eth3m_v10_label_timing/task_timing_metrics.csv"
)
DEFAULT_OUTPUT = (
    PROJECT
    / "analysis/output/eth3m_v2_problem_analysis_20260730/dataset_quality_audit.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--timing", type=Path, default=DEFAULT_TIMING)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    manifest = pd.read_csv(args.manifest)
    timing = pd.read_csv(
        args.timing,
        usecols=[
            "task_id",
            "candidate_time",
            "first_below_all_mas_lag_bars",
        ],
    )
    result = audit_dataset_quality(manifest, timing)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), "status": result["status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "catboost>=1.2.8,<2",
#   "lightgbm>=4.6,<5",
#   "numpy>=1.24",
#   "pandas>=2.0",
#   "scikit-learn>=1.3",
#   "xgboost>=3.0,<4",
# ]
# ///
# --- How to run ---
# PYTHONPATH=. uv run scripts/benchmark_shadow_boosters.py
"""Run isolated pre-holdout booster challengers without saving models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Final

from src.judgment.frozen import DEFAULT_FROZEN_CONFIG, file_sha256
from src.judgment.shadow_boosters import load_shadow_splits, run_shadow_benchmark

PROJECT_DIR: Final = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT: Final = PROJECT_DIR / "analysis" / "output" / "shadow_booster_benchmark.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_FROZEN_CONFIG.dataset_path)
    parser.add_argument("--models", default="lightgbm,catboost,xgboost,ensemble")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    train, val = load_shadow_splits(args.data)
    result = run_shadow_benchmark(train, val, tuple(args.models.split(",")))
    artifact = {
        "models": result["models"],
        "base_score_spearman": result["base_score_spearman"],
        "warning": result["warning"],
        "dataset": str(args.data),
        "dataset_sha256": file_sha256(args.data),
        "holdout_used": False,
        "split": {
            "train": {
                "n": len(train),
                "range": [str(train["signal_time"].min()), str(train["signal_time"].max())],
            },
            "val": {
                "n": len(val),
                "range": [str(val["signal_time"].min()), str(val["signal_time"].max())],
            },
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lightgbm>=4.0",
#   "numpy>=1.24",
#   "pandas>=2.0",
# ]
# ///
# --- How to run ---
# PYTHONPATH=. python3 scripts/freeze_model.py
"""Freeze the selected judgment model for forward validation.

Default config (2026-07-15+): tp5_sl2_swap_yolo — LightGBM on YOLO-proposed
candidates (data/judgment_yolo_swap.csv). Trains train-only, threshold = val
q90, never evaluates holdout.

Rollback: --legacy-rules freezes/points at the old rule-scan config name.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from src.judgment.frozen import (
    DEFAULT_FROZEN_CONFIG,
    default_config,
    rules_legacy_config,
    train_frozen_artifact,
)

PROJECT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument(
        "--legacy-rules",
        action="store_true",
        help="use pre-cutover rule-scan dataset/config name (rollback)",
    )
    parser.add_argument(
        "--write-active",
        action="store_true",
        help="write models/ACTIVE to the new artifact model path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = rules_legacy_config() if args.legacy_rules else default_config()
    if not config.dataset_path.exists():
        raise SystemExit(f"dataset missing: {config.dataset_path}")
    artifact = train_frozen_artifact(config, args.date)
    meta = {
        "model_path": artifact.relative_model_path,
        "metadata_path": artifact.metadata_path.relative_to(artifact.config.project_dir).as_posix(),
        "dataset_path": artifact.relative_dataset_path,
        "dataset_sha256": artifact.dataset_sha256,
        "threshold_val_q90": artifact.threshold,
        "best_iteration": artifact.best_iteration,
        "config": config.name,
        "candidate_source": "rules" if args.legacy_rules else "yolo",
    }
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if args.write_active:
        active = PROJECT / "models" / "ACTIVE"
        active.write_text(artifact.relative_model_path + "\n", encoding="utf-8")
        print(f"ACTIVE -> {artifact.relative_model_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

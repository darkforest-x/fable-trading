# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lightgbm>=4.0",
#   "numpy>=1.24",
#   "pandas>=2.0",
# ]
# ///
# --- How to run ---
# PYTHONPATH=. python3 scripts/freeze_model.py --write-active
"""Freeze the selected judgment model for forward validation.

Default (2026-07-15+ owner): tp5_sl2_swap_yolo_reg — LightGBM regression on
realized_ret over YOLO candidates (data/judgment_yolo_swap.csv).

Rollback:
  --binary-yolo   previous YOLO binary freeze config name
  --legacy-rules  pre-cutover rule-scan config
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from src.judgment.frozen import (
    binary_yolo_shadow_config,
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
        "--binary-yolo",
        action="store_true",
        help="freeze/point at previous YOLO binary config (shadow / rollback)",
    )
    parser.add_argument(
        "--write-active",
        action="store_true",
        help="write models/ACTIVE to the new artifact model path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.legacy_rules and args.binary_yolo:
        raise SystemExit("choose at most one of --legacy-rules / --binary-yolo")
    if args.legacy_rules:
        config = rules_legacy_config()
        candidate_source = "rules"
    elif args.binary_yolo:
        config = binary_yolo_shadow_config()
        candidate_source = "yolo"
    else:
        config = default_config()
        candidate_source = "yolo"
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
        "objective": config.objective,
        "candidate_source": candidate_source,
    }
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if args.write_active:
        active = PROJECT / "models" / "ACTIVE"
        prev = PROJECT / "models" / "ACTIVE_PREV"
        if active.exists():
            prev.write_text(active.read_text(encoding="utf-8"), encoding="utf-8")
        active.write_text(artifact.relative_model_path + "\n", encoding="utf-8")
        shadow = PROJECT / "models" / "SHADOW_BINARY_YOLO"
        shadow.write_text(
            "models/frozen_tp5_sl2_swap_yolo_20260715.txt\n"
            "# previous binary YOLO freeze; dashboard compare + emergency rollback\n",
            encoding="utf-8",
        )
        print(f"ACTIVE -> {artifact.relative_model_path}")
        print(f"ACTIVE_PREV kept; SHADOW_BINARY_YOLO -> binary yolo freeze")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

The default config is the owner-approved tp5_sl2 SWAP mainline. The script
trains only on the train split, fixes the entry threshold from val q90, and
does not evaluate holdout.
"""
from __future__ import annotations

import argparse
import json
from datetime import date

from src.judgment.frozen import DEFAULT_FROZEN_CONFIG, train_frozen_artifact


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifact = train_frozen_artifact(DEFAULT_FROZEN_CONFIG, args.date)
    print(
        json.dumps(
            {
                "model_path": artifact.relative_model_path,
                "metadata_path": artifact.metadata_path.relative_to(artifact.config.project_dir).as_posix(),
                "dataset_path": artifact.relative_dataset_path,
                "dataset_sha256": artifact.dataset_sha256,
                "threshold_val_q90": artifact.threshold,
                "best_iteration": artifact.best_iteration,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

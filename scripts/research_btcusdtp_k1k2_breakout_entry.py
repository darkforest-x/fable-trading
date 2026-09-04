#!/usr/bin/env python3
"""Run the preregistered causal post-K2 direction-breakout entry family."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.optimize_btcusdtp_k1k2_independent_timeframes import utc
from scripts.research_btcusdtp_k1k2_sweep_reclaim_entry import (
    SCRIPT_PATH as ENGINE_PATH,
    audit_phase,
    development_phase,
)

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/"
    "exp-btcusdtp-k1k2-breakout-entry-preholdout-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("development", "audit"), required=True)
    args = parser.parse_args()
    config = load_config()
    if utc(config["window"]["audit_end_exclusive"]) >= utc(
        config["window"]["holdout_start"]
    ):
        raise RuntimeError("configured audit boundary reaches repository holdout")
    kwargs = {
        "results": RESULTS,
        "selection_path": SELECTION_PATH,
        "config_path": CONFIG_PATH,
        "script_path": SCRIPT_PATH,
        "engine_path": ENGINE_PATH,
    }
    if args.phase == "development":
        development_phase(config, **kwargs)
    else:
        audit_phase(config, **kwargs)


if __name__ == "__main__":
    main()

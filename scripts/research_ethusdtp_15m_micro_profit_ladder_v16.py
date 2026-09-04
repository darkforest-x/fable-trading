#!/usr/bin/env python3
"""Select and audit a micro ETHUSDT.P 15m fixed profit ladder.

This wrapper reuses the frozen V6 four-level bank-only resolver while limiting
selection to 10%--20% total banking.  The selected variable is total bank size;
entries, +2/+4/+8/+12 ATR targets, runner, stops, horizon and cost are fixed.
Repository holdout rows are never parsed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import scripts.research_ethusdtp_15m_wide_profit_ladder_v6 as base

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-micro-profit-ladder-preholdout-20260905-v16"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
PREREG_PATH = EXPERIMENT / "preregistration.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _install_base_bindings() -> None:
    base.EXPERIMENT_ID = EXPERIMENT_ID
    base.EXPERIMENT = EXPERIMENT
    base.CONFIG_PATH = CONFIG_PATH
    base.PREREG_PATH = PREREG_PATH
    base.RESULTS = RESULTS
    base.SELECTION_PATH = SELECTION_PATH
    base.SCRIPT_PATH = SCRIPT_PATH


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("selection", "audit"))
    args = parser.parse_args()
    _install_base_bindings()
    config = load_config()
    if args.phase == "selection":
        base.selection_phase(config)
    else:
        base.audit_phase(config)


if __name__ == "__main__":
    main()

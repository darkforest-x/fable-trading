#!/usr/bin/env python3
"""Audit the frozen v10 report's return/cost route without training or holdout.

The frozen dataset ends before 2026-05-04. Its ``realized_ret`` column is
``net_barrier_taker`` (cost already included), while the historical report
called ``realized_ret - SWAP_MAKER`` "net maker". This versioned audit preserves
the historical script and computes the only valid route conversion:
net_taker -> gross -> net_maker.

Writes only ``analysis/output/p0_return_semantics_20260803.json``. It never
changes models/ACTIVE, model files, datasets, forward logs, thresholds, or reports.
"""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.costs import SWAP_MAKER, SWAP_TAKER, convert_return

PROJECT = Path(__file__).resolve().parents[1]
META = PROJECT / "models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.json"
OUT = PROJECT / "analysis/output/p0_return_semantics_20260803.json"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")


def main() -> int:
    meta = json.loads(META.read_text(encoding="utf-8"))
    dataset_path = PROJECT / str(meta["dataset_path"])
    frame = pd.read_csv(dataset_path, parse_dates=["signal_time"])
    times = pd.to_datetime(frame["signal_time"], utc=True)
    if times.max() >= HOLDOUT_START:
        raise SystemExit(
            f"refusing dataset with holdout rows: max={times.max()} boundary={HOLDOUT_START}"
        )
    val_start = pd.Timestamp(meta["splits"]["val"]["range"][0]).tz_convert("UTC")
    val_end = pd.Timestamp(meta["splits"]["val"]["range"][1]).tz_convert("UTC")
    val = frame[(times >= val_start) & (times <= val_end)].copy()
    features = list(meta["feature_columns"])
    booster = lgb.Booster(model_file=str(PROJECT / str(meta["model_path"])))
    scores = booster.predict(val[features], num_iteration=int(meta["best_iteration"]))
    threshold = float(meta["threshold_val_q90"])
    passed = scores >= threshold
    equal = scores == threshold
    k = max(1, len(val) // 10)
    top = np.argsort(-scores)[:k]
    net_taker = val["realized_ret"].to_numpy(dtype=float)
    gross = np.array(
        [convert_return(v, source_semantics="net_taker", target_semantics="gross") for v in net_taker]
    )
    net_maker = np.array(
        [
            convert_return(v, source_semantics="net_taker", target_semantics="net_maker")
            for v in net_taker
        ]
    )

    def mean(values) -> float | None:
        return float(np.mean(values)) if len(values) else None

    result = {
        "audit_version": "p0_return_semantics_20260803",
        "holdout_read": False,
        "dataset_path": str(dataset_path.relative_to(PROJECT)),
        "dataset_max_signal_time": str(times.max()),
        "val_n": int(len(val)),
        "target_ret_column": "net_barrier_taker",
        "target_semantics": "net_taker",
        "target_cost_included": True,
        "return_convention": "linear_short",
        "costs": {"swap_taker_round_trip": SWAP_TAKER, "swap_maker_round_trip": SWAP_MAKER},
        "threshold": threshold,
        "threshold_operator": ">=",
        "tie_policy": "legacy_large_tie_mass",
        "calibration_pass_rate": float(np.mean(passed)),
        "threshold_equal_rate": float(np.mean(equal)),
        "top_decile_n": int(k),
        "route_means": {
            "pass_gross": mean(gross[passed]),
            "pass_net_taker": mean(net_taker[passed]),
            "pass_net_maker_correct": mean(net_maker[passed]),
            "pass_net_maker_historical_wrong_double_deduction": mean(
                net_taker[passed] - SWAP_MAKER
            ),
            "top_gross": mean(gross[top]),
            "top_net_taker": mean(net_taker[top]),
            "top_net_maker_correct": mean(net_maker[top]),
            "top_net_maker_historical_wrong_double_deduction": mean(
                net_taker[top] - SWAP_MAKER
            ),
        },
        "historical_script": "scripts/regen_l2_v10_freeze_report.py",
        "historical_script_status": "superseded_for_return_cost_route_only",
        "formula": {
            "gross_to_taker": "gross - SWAP_TAKER",
            "gross_to_maker": "gross - SWAP_MAKER",
            "taker_to_maker": "net_taker + SWAP_TAKER - SWAP_MAKER",
            "forbidden": "net_taker - SWAP_MAKER",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

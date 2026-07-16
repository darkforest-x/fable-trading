"""Portfolio simulation of the v3 candidate on the VAL WINDOW ONLY.

Reuses the stage-3 simulator (per-symbol lock, 10-slot cap, score priority);
adds the maker fill rule from barrier_sweep: unfilled signals are missed, not
losses. H9 trend-filter pools are evaluated with the same val q90 threshold.

Usage: python3 -m src.backtest.maker_val_sim
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.backtest.run import BAR, simulate, window_metrics
from src.judgment.frozen import DEFAULT_FROZEN_CONFIG
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import load_splits, train_model
from src.judgment.trend_filter import add_h9_flags

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA = DEFAULT_FROZEN_CONFIG.dataset_path
OUT = PROJECT_DIR / "analysis" / "output" / "p3_maker_val_sim.json"
from src.costs import (SPOT_MAKER as MAKER_COST, SPOT_TAKER as TAKER_COST,
                       SWAP_MAKER as SWAP_MAKER_COST, SWAP_TAKER as SWAP_TAKER_COST)


def is_swap_dataset(data_path: Path) -> bool:
    return "swap" in data_path.name or "swap_replication" in str(data_path)


def maker_cost_for_dataset(data_path: Path) -> float:
    if is_swap_dataset(data_path):
        return SWAP_MAKER_COST
    return MAKER_COST


def taker_cost_for_dataset(data_path: Path) -> float:
    if is_swap_dataset(data_path):
        return SWAP_TAKER_COST
    return TAKER_COST


def _pool_metrics(sig: pd.DataFrame, threshold: float, pool: pd.DataFrame, cost: float) -> dict:
    trades = simulate(pool, threshold)
    metrics = window_metrics(trades, cost)
    metrics["candidate_pool"] = int(len(pool))
    metrics["pool_share"] = round(float(len(pool) / len(sig)), 4) if len(sig) else 0.0
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--horizon-bars", type=int, default=72)
    args = parser.parse_args()

    train, val, _ = load_splits(args.data, horizon_bars=args.horizon_bars)
    model = train_model(train, val)
    threshold = float(np.quantile(
        model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration), 0.90))

    sig = val.copy()  # val window only
    sig["score"] = model.predict(sig[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    sig["entry_time"] = sig["signal_time"] + BAR
    sig["exit_time"] = sig["entry_time"] + sig["exit_offset"] * BAR
    # YOLO candidate CSVs may omit maker_filled; treat as all filled and flag it.
    if "maker_filled" not in sig.columns:
        sig["maker_filled"] = True
        maker_filled_assumed = True
    else:
        maker_filled_assumed = False
    sig = add_h9_flags(sig)
    sig = sig.sort_values(["entry_time", "score"], ascending=[True, False])

    maker_cost = maker_cost_for_dataset(args.data)
    # judgment_yolo_swap is SWAP economics even if path has no "swap_replication"
    if "yolo" in args.data.name or "swap" in args.data.name.lower():
        maker_cost = SWAP_MAKER_COST
        taker_cost = SWAP_TAKER_COST
    else:
        taker_cost = taker_cost_for_dataset(args.data)
    maker = sig[sig["maker_filled"]]
    h9_above = sig[sig["maker_filled"] & sig["h1_ok"] & sig["h1_above_ma"]]
    h9_slope = sig[sig["maker_filled"] & sig["h1_ok"] & sig["h1_up_slope"]]
    results = {
        "dataset": str(args.data),
        "horizon_bars": args.horizon_bars,
        "threshold_val_q90": round(threshold, 5),
        "val_range": [str(sig["signal_time"].min()), str(sig["signal_time"].max())],
        "costs": {"maker": maker_cost, "taker": taker_cost},
        "maker_filled_assumed": maker_filled_assumed,
        "flag_coverage": round(float(sig["h1_ok"].mean()), 4),
        "pass_rate": {
            "h1_above_ma": round(float((sig["h1_ok"] & sig["h1_above_ma"]).mean()), 4),
            "h1_up_slope": round(float((sig["h1_ok"] & sig["h1_up_slope"]).mean()), 4),
        },
        "maker": _pool_metrics(sig, threshold, maker, maker_cost),
        "maker_h9_above_ma": _pool_metrics(sig, threshold, h9_above, maker_cost),
        "maker_h9_up_slope": _pool_metrics(sig, threshold, h9_slope, maker_cost),
        "taker": _pool_metrics(sig, threshold, sig, taker_cost),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

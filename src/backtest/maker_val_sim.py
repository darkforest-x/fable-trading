"""Portfolio simulation of the v3 candidate (TP5/SL2 + maker entries) on the
VAL WINDOW ONLY -- the twice-seen acceptance window is not touched.

Reuses the stage-3 simulator (per-symbol lock, 10-slot cap, score priority);
adds the maker fill rule from barrier_sweep: unfilled signals are missed, not
losses. Costs: maker 0.16% round trip; taker 0.30% shown for contrast.

Usage: python3 -m src.backtest.maker_val_sim
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.backtest.run import BAR, simulate, window_metrics
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import load_splits, train_model

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA = PROJECT_DIR / "data" / "sweep_v3" / "judgment_v3_tp5_sl2_h72.csv"
OUT = PROJECT_DIR / "analysis" / "output" / "p3_maker_val_sim.json"
MAKER_COST = 0.0016
TAKER_COST = 0.003


def main() -> int:
    train, val, _ = load_splits(DATA, horizon_bars=72)
    model = train_model(train, val)
    threshold = float(np.quantile(
        model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration), 0.90))

    sig = val.copy()  # val window only
    sig["score"] = model.predict(sig[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    sig["entry_time"] = sig["signal_time"] + BAR
    sig["exit_time"] = sig["entry_time"] + sig["exit_offset"] * BAR
    sig = sig.sort_values(["entry_time", "score"], ascending=[True, False])

    results = {"dataset": str(DATA), "threshold_val_q90": round(threshold, 5),
               "val_range": [str(sig["signal_time"].min()), str(sig["signal_time"].max())]}
    for name, pool, cost in (
        ("maker", sig[sig["maker_filled"]], MAKER_COST),
        ("taker", sig, TAKER_COST),
    ):
        trades = simulate(pool, threshold)
        m = window_metrics(trades, cost)
        m["fill_rate"] = round(float(len(pool) / len(sig)), 3)
        results[name] = m
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

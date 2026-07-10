"""H9: higher-timeframe trend alignment filter (RESEARCH_AGENDA priority #1).

Hypothesis: 15m dense-breakout signals taken only when the 1h trend agrees
("顺大势的密集启动") have materially better economics.

1h bars are AGGREGATED from the 15m series (no new fetch). Lookahead rule:
at a signal bar with open_time s the decision happens at s+15m; a 1h bar
opening at T is complete at T+1h, so only bars with T <= s + 15m - 1h are
usable. We map each signal to the last completed 1h bar via searchsorted
on (T + 1h) <= (s + 15m).

Trend flags on completed 1h data only:
  up_slope : 1h EMA60 slope over 12 bars > 0
  above_ma : 1h close > 1h EMA120

Evaluation (val only, TP5/SL2 h72 dataset from the round-2 sweep): fixed
threshold = val q90 of model scores (as in stage 3); compare top-bucket
maker-net with no filter vs each flag vs both, plus pass rates. Discovery
tier -- confirmation belongs to forward data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.backtest.maker_val_sim import maker_cost_for_dataset
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import load_splits, train_model
from src.judgment.trend_filter import add_h9_flags

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA = PROJECT_DIR / "data" / "sweep_v3" / "judgment_v3_tp5_sl2_h72.csv"
OUT = PROJECT_DIR / "analysis" / "output" / "h9_trend_filter.json"


def bucket_stats(sub: pd.DataFrame, maker_cost: float) -> dict:
    if sub.empty:
        return {"n": 0}
    net = sub["realized_ret"] - maker_cost
    return {"n": int(len(sub)), "mean_net_maker": round(float(net.mean()), 5),
            "win_rate": round(float((sub["realized_ret"] > 0).mean()), 4)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--horizon-bars", type=int, default=72)
    args = parser.parse_args()

    train, val, _ = load_splits(args.data, horizon_bars=args.horizon_bars)  # holdout untouched
    model = train_model(train, val)
    val = val.copy()
    val["score"] = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    threshold = float(np.quantile(val["score"], 0.90))
    val = add_h9_flags(val)
    top = val[(val["score"] >= threshold) & val["h1_ok"]]

    maker_cost = maker_cost_for_dataset(args.data)
    results = {
        "dataset": str(args.data), "threshold_q90": round(threshold, 5),
        "horizon_bars": args.horizon_bars,
        "maker_cost": maker_cost,
        "flag_coverage": round(float(val["h1_ok"].mean()), 4),
        "top_bucket": {
            "no_filter": bucket_stats(top, maker_cost),
            "up_slope_only": bucket_stats(top[top["h1_up_slope"]], maker_cost),
            "above_ma_only": bucket_stats(top[top["h1_above_ma"]], maker_cost),
            "both": bucket_stats(top[top["h1_up_slope"] & top["h1_above_ma"]], maker_cost),
            "against_trend": bucket_stats(top[~top["h1_up_slope"] & ~top["h1_above_ma"]], maker_cost),
        },
        "all_val_pass_rate": {
            "up_slope": round(float(val["h1_up_slope"].mean()), 4),
            "above_ma": round(float(val["h1_above_ma"].mean()), 4),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

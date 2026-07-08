"""H9: higher-timeframe trend alignment filter (RESEARCH_AGENDA priority #1).

Hypothesis: 15m dense-breakout signals taken only when the 1h trend agrees
("顺大势的密集启动") have materially better economics.

1h bars are AGGREGATED from the 15m series (no new fetch). Lookahead rule:
at a signal bar with open_time s the decision happens at s+15m; a 1h bar
opening at T is complete at T+1h, so only bars with T <= s + 15m - 1h are
usable. We map each signal to the last completed 1h bar via searchsorted
on (T + 1h) <= (s + 15m).

Trend flags on completed 1h data only:
  up_slope : 1h EMA55 slope over 12 bars > 0
  above_ma : 1h close > 1h EMA144

Evaluation (val only, TP5/SL2 h72 dataset from the round-2 sweep): fixed
threshold = val q90 of model scores (as in stage 3); compare top-bucket
maker-net with no filter vs each flag vs both, plus pass rates. Discovery
tier -- confirmation belongs to forward data.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.loader import list_series, load_series
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import load_splits, train_model

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA = PROJECT_DIR / "data" / "sweep_v3" / "judgment_v3_tp5_sl2_h72.csv"
OUT = PROJECT_DIR / "analysis" / "output" / "h9_trend_filter.json"
MAKER_COST = 0.0016
BAR15 = pd.Timedelta(minutes=15)
H1 = pd.Timedelta(hours=1)


def hourly_state(frame: pd.DataFrame) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]:
    """Completed-1h-bar trend flags for one 15m series."""
    close = frame.set_index("open_time")["close"].resample("1h").last().dropna()
    ema55 = close.ewm(span=55, adjust=False).mean()
    ema144 = close.ewm(span=144, adjust=False).mean()
    up_slope = (ema55.diff(12) > 0).to_numpy()
    above_ma = (close > ema144).to_numpy()
    return close.index, up_slope, above_ma


def add_flags(rows: pd.DataFrame) -> pd.DataFrame:
    groups = list_series()
    out_slope = np.full(len(rows), False)
    out_above = np.full(len(rows), False)
    ok = np.full(len(rows), False)
    for (source, symbol), g in rows.groupby(["source", "symbol"]):
        key = (source, symbol)
        if key not in groups:
            continue
        frame = load_series(groups[key])
        idx, up_slope, above_ma = hourly_state(frame)
        # usable = last 1h bar with open T such that T + 1h <= signal_time + 15m
        cutoff = (g["signal_time"] + BAR15 - H1).to_numpy()
        pos = idx.searchsorted(cutoff, side="right") - 1
        valid = (pos >= 55 * 1)  # need EMA warmup on 1h
        loc = rows.index.get_indexer(g.index)
        out_slope[loc] = np.where(valid, up_slope[np.clip(pos, 0, len(idx) - 1)], False)
        out_above[loc] = np.where(valid, above_ma[np.clip(pos, 0, len(idx) - 1)], False)
        ok[loc] = valid
    rows = rows.copy()
    rows["h1_up_slope"], rows["h1_above_ma"], rows["h1_ok"] = out_slope, out_above, ok
    return rows


def bucket_stats(sub: pd.DataFrame) -> dict:
    if sub.empty:
        return {"n": 0}
    net = sub["realized_ret"] - MAKER_COST
    return {"n": int(len(sub)), "mean_net_maker": round(float(net.mean()), 5),
            "win_rate": round(float((sub["realized_ret"] > 0).mean()), 4)}


def main() -> int:
    train, val, _ = load_splits(DATA, horizon_bars=72)  # holdout untouched
    model = train_model(train, val)
    val = val.copy()
    val["score"] = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    threshold = float(np.quantile(val["score"], 0.90))
    val = add_flags(val)
    top = val[(val["score"] >= threshold) & val["h1_ok"]]

    results = {
        "dataset": str(DATA), "threshold_q90": round(threshold, 5),
        "flag_coverage": round(float(val["h1_ok"].mean()), 4),
        "top_bucket": {
            "no_filter": bucket_stats(top),
            "up_slope_only": bucket_stats(top[top["h1_up_slope"]]),
            "above_ma_only": bucket_stats(top[top["h1_above_ma"]]),
            "both": bucket_stats(top[top["h1_up_slope"] & top["h1_above_ma"]]),
            "against_trend": bucket_stats(top[~top["h1_up_slope"] & ~top["h1_above_ma"]]),
        },
        "all_val_pass_rate": {
            "up_slope": round(float(val["h1_up_slope"].mean()), 4),
            "above_ma": round(float(val["h1_above_ma"].mean()), 4),
        },
    }
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

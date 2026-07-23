#!/usr/bin/env python3
"""Train a SAME-SOURCE judgment layer on v16's own candidates.

The fix for the anti-selection problem (owner 2026-07-23): the judgment layer
scores the SAME detector's candidates it was trained on. dump_v16_candidates.py
produced v16's tip candidates with the 28 judgment features + TP5/SL2/72bar
forward net (maker cost). Here we train a LightGBM regressor (predict net from
features) with a strict TIME split (train early, test late -- no leakage), then
ask the decisive question:

  Does filtering v16's candidates by this same-source judgment score lift the
  net-positive subset above the raw base rate (and above the rule+judgment
  1.0-1.27 baseline)?

If the top score-decile of the held-out period is clearly net-positive after
cost -> the two-layer pipeline works same-source, thesis alive. If not -> even
same-source judgment cannot purify v16's candidates, signal too thin.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/train_samesource_judgment.py --data data/v16_candidates.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
from src.judgment.features import FEATURE_COLUMNS  # noqa: E402

FEATURES = list(FEATURE_COLUMNS)


def metrics(net: np.ndarray) -> dict:
    if not len(net):
        return {"n": 0, "win_rate": None, "profit_factor": None, "mean_net": None}
    w, l = net[net > 0].sum(), net[net < 0].sum()
    return {
        "n": int(len(net)),
        "win_rate": round(float((net > 0).mean()), 4),
        "profit_factor": round(float(w / -l), 3) if l < 0 else None,
        "mean_net": round(float(net.mean()), 5),
        "total_net": round(float(net.sum()), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data/v16_candidates.csv")
    ap.add_argument("--test-frac", type=float, default=0.3, help="latest fraction = held-out test")
    ap.add_argument("--tag", default="samesource_v16_judgment")
    args = ap.parse_args()

    df = pd.read_csv(PROJECT / args.data)
    df = df.dropna(subset=FEATURES + ["net"]).sort_values("signal_time").reset_index(drop=True)
    n = len(df)
    cut = int(n * (1 - args.test_frac))
    train, test = df.iloc[:cut], df.iloc[cut:]
    print(f"rows={n} train={len(train)} test={len(test)} "
          f"train_range={train['signal_time'].iloc[0][:10]}..{train['signal_time'].iloc[-1][:10]} "
          f"test_range={test['signal_time'].iloc[0][:10]}..{test['signal_time'].iloc[-1][:10]}")

    booster = lgb.train(
        {"objective": "regression", "metric": "l2", "num_leaves": 31,
         "learning_rate": 0.03, "feature_fraction": 0.8, "bagging_fraction": 0.8,
         "bagging_freq": 5, "min_data_in_leaf": 50, "verbose": -1},
        lgb.Dataset(train[FEATURES], label=train["net"]),
        num_boost_round=300,
    )
    test = test.copy()
    test["score"] = booster.predict(test[FEATURES])
    net = test["net"].to_numpy()

    out = {"tag": args.tag, "rows": n, "test_n": len(test),
           "raw_test_base_rate": metrics(net)}
    # score-decile lift: is the top decile net-positive?
    deciles = {}
    test_sorted = test.sort_values("score", ascending=False).reset_index(drop=True)
    for q, label in ((0.10, "top10"), (0.20, "top20"), (0.30, "top30"), (0.50, "top50")):
        k = max(int(len(test_sorted) * q), 1)
        deciles[label] = metrics(test_sorted["net"].to_numpy()[:k])
    out["score_top_slices"] = deciles
    # feature importance (what the same-source judgment keys on)
    imp = sorted(zip(FEATURES, booster.feature_importance(importance_type="gain")),
                 key=lambda x: -x[1])[:10]
    out["top_features_by_gain"] = [{"f": f, "gain": int(g)} for f, g in imp]

    p = PROJECT / "analysis" / "output" / f"{args.tag}.json"
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""IT-17: is the short judgment's edge just one volatility threshold? Does it flip?

Why: the 100-coin short walk-forward has best_iteration=1 in 4 of 5 folds (the
model early-stops after a single boosting round) and rho_mean is NEGATIVE
(-0.0103), yet top-decile net is positive. A one-round LightGBM is essentially
one threshold on one feature -- and the reported top gains are all volatility
features (atr_pct / pre_range168 / atr_pct_ratio96). Lab IT-06/IT-07 already
found the same thing on the v16 pool, and IT-07 showed the winning volatility
bucket FLIPS over time, which is why a fixed gate never held.

So: pull the edge out of the model and state it as an explicit rule, then ask
the two questions that decide deployability:
  Q1 does a plain rule match the LGBM top-decile? (if yes, the "judgment" is
     a threshold, not learned skill -- and should be tested as a rule)
  Q2 does the sign of the volatility/return relationship FLIP across folds?
     (if yes, this is IT-07's wall again, reached from the short-only side)

Walk-forward, expanding train with a purge gap (labels look 72 bars ahead), same
candidate pool the parallel session built. Reports net at BOTH the 0.2% legacy
cost used by their walkforward and the 0.06% swap maker cost.

Read-only: consumes data/judgment_yolo_owner_side_short_100_6m.csv, writes only
its own JSON. No holdout (pool ends 2026-05-03). No promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/it17_short_rule_vs_lgbm.py
"""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

POOL = PROJECT / "data" / "judgment_yolo_owner_side_short_100_6m.csv"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
LEGACY_COST, MAKER_COST = 0.002, 0.0006
N_FOLDS, TOP_FRAC = 5, 0.10
PURGE_BARS, BAR_MIN = 72, 15  # labels look 72 bars ahead -> purge train/val boundary

NON_FEAT = {"source", "symbol", "side", "signal_i", "signal_time", "label",
            "outcome", "exit_offset", "entry_price", "realized_ret"}


def load_pool():
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT_START].sort_values("t").reset_index(drop=True)
    feats = [c for c in d.columns if c not in NON_FEAT and c != "t"
             and pd.api.types.is_numeric_dtype(d[c])]
    for c in feats:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["realized_ret"]).reset_index(drop=True)
    d[feats] = d[feats].fillna(0.0)
    return d, feats


def folds(d):
    """Expanding-window folds over the last N_FOLDS chunks, purged at the boundary."""
    n = len(d)
    val_size = n // (N_FOLDS + 1)
    purge = pd.Timedelta(minutes=BAR_MIN * PURGE_BARS)
    out = []
    for k in range(N_FOLDS):
        lo = n - (N_FOLDS - k) * val_size
        hi = lo + val_size
        val = d.iloc[lo:hi]
        tr = d[d["t"] < val["t"].iloc[0] - purge]
        if len(tr) < 500 or len(val) < 200:
            continue
        out.append((tr, val))
    return out


def top_net(val, score, cost):
    """Mean net return of the top TOP_FRAC by score (higher score = pick first)."""
    k = max(int(len(val) * TOP_FRAC), 1)
    idx = np.argsort(-np.asarray(score))[:k]
    r = val["realized_ret"].to_numpy()[idx]
    return round(float(r.mean() - cost), 5), int(k)


def main() -> int:
    d, feats = load_pool()
    print(f"pool={len(d)} feats={len(feats)} {d['t'].min()} → {d['t'].max()}")
    print(f"raw: gross={d['realized_ret'].mean():.5f} "
          f"net@0.2%={d['realized_ret'].mean()-LEGACY_COST:.5f} "
          f"tp_rate={(d['label']==1).mean():.3f} (TP5/SL2 breakeven 0.286)")

    RULES = {
        "atr_pct_HIGH": lambda v: v["atr_pct"].to_numpy(),
        "atr_pct_LOW": lambda v: -v["atr_pct"].to_numpy(),
        "pre_range168_HIGH": lambda v: v["pre_range168"].to_numpy(),
        "pre_range168_LOW": lambda v: -v["pre_range168"].to_numpy(),
        "close_vs_ema200_LOW": lambda v: -v["close_vs_ema200"].to_numpy(),
        "slow_slope_12_LOW": lambda v: -v["slow_slope_12"].to_numpy(),
    }
    P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03,
         "min_data_in_leaf": 30, "feature_fraction": 0.8, "bagging_fraction": 0.8,
         "bagging_freq": 5, "verbose": -1}

    res = {name: [] for name in RULES}
    res["LGBM"] = []
    res["ALL_candidates"] = []
    rho_atr, best_iters = [], []

    for fi, (tr, val) in enumerate(folds(d), 1):
        # LGBM reference, early-stopped on a held-out tail of train (never val)
        cut = int(len(tr) * 0.85)
        ds_tr = lgb.Dataset(tr[feats].iloc[:cut], label=tr["realized_ret"].iloc[:cut])
        ds_va = lgb.Dataset(tr[feats].iloc[cut:], label=tr["realized_ret"].iloc[cut:])
        bo = lgb.train(P, ds_tr, num_boost_round=400, valid_sets=[ds_va],
                       callbacks=[lgb.early_stopping(30, verbose=False)])
        best_iters.append(int(bo.best_iteration or 0))
        res["LGBM"].append(top_net(val, bo.predict(val[feats]), LEGACY_COST)[0])
        res["ALL_candidates"].append(round(float(val["realized_ret"].mean() - LEGACY_COST), 5))
        for name, fn in RULES.items():
            res[name].append(top_net(val, fn(val), LEGACY_COST)[0])
        rho = spearmanr(val["atr_pct"], val["realized_ret"]).correlation
        rho_atr.append(round(float(rho), 4))
        print(f"  fold{fi}: n_tr={len(tr)} n_val={len(val)} "
              f"val={str(val['t'].iloc[0])[:10]}..{str(val['t'].iloc[-1])[:10]} "
              f"best_iter={best_iters[-1]} rho(atr,ret)={rho_atr[-1]:+.4f}")

    print("\n=== top-decile net @0.2% per fold (正=赚) ===")
    summary = {}
    for name, vals in res.items():
        arr = np.array(vals)
        summary[name] = {"folds": vals, "mean": round(float(arr.mean()), 5),
                         "min": round(float(arr.min()), 5),
                         "n_pos": int((arr > 0).sum()), "n_folds": len(arr)}
        flag = "✓all+" if (arr > 0).all() else f"{(arr>0).sum()}/{len(arr)}+"
        print(f"  {name:22s} {[f'{v:+.4f}' for v in vals]} mean={arr.mean():+.5f} {flag}")

    print(f"\nrho(atr_pct, realized_ret) per fold: {rho_atr}")
    flips = (np.sign(rho_atr) != np.sign(rho_atr[0])).sum()
    print(f"  符号翻转折数: {flips}/{len(rho_atr)-1}  → "
          f"{'波动率关系不稳定(IT-07 同一堵墙)' if flips else '方向一致'}")
    print(f"best_iteration per fold: {best_iters}")

    out = {"pool": str(POOL), "n": len(d), "cost_primary": LEGACY_COST,
           "raw_gross_mean": round(float(d["realized_ret"].mean()), 5),
           "tp_rate": round(float((d["label"] == 1).mean()), 4),
           "tp5sl2_breakeven": 0.286,
           "per_fold_best_iteration": best_iters,
           "rho_atr_vs_ret_per_fold": rho_atr,
           "rho_sign_flips": int(flips),
           "results": summary}
    (PROJECT / "analysis" / "output" / "it17_short_rule_vs_lgbm.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

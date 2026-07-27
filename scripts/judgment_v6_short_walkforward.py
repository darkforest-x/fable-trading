"""The second layer on v6's candidates: does selection rescue a losing detector?

Owner's point, and it is right: YOLO is only the first filter. The tip-replay
backtest traded EVERY v6 fire raw -- 388 trades, PF 0.832, win 17.5% against a
28.6% breakeven -- which measures the detector's pool, not the two-layer system.
This asks the question that was actually skipped: can a judgment layer pick a
profitable subset out of that pool?

Discipline, because this is exactly where earlier rounds fooled themselves:
  * walk-forward, expanding train with a purge gap (labels look 72 bars ahead),
    never a single split -- IT-01 looked positive on one split and died on three
  * the raw pool is reported beside every top-decile number, since a top decile
    can only be credited with what it adds over taking everything
  * a single-feature baseline runs alongside; the judgment has to beat it
  * results print across the cost ladder, with the taker floor the executor
    actually pays as the reference, not the unreachable maker route
  * gross PF is shown too: net gains that come only from a fixed cost being
    diluted are not selection skill (see the ATR-scaling learning)

No holdout: the pool ends 2026-05-03. No promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/judgment_v6_short_walkforward.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.costs import LEGACY_P0_ROUND_TRIP, SWAP_MAKER, SWAP_TAKER  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_short_v6.csv"
N_FOLDS, TOP_FRAC = 4, 0.20
PURGE_BARS, BAR_MIN = 72, 15
NON_FEAT = {"source", "symbol", "side", "signal_i", "signal_time", "label",
            "outcome", "exit_offset", "entry_price", "realized_ret", "t"}
LADDER = [("maker 0.06% (拿不到)", SWAP_MAKER), ("taker 0.10% (真实地板)", SWAP_TAKER),
          ("taker+滑点 0.15%", 0.0015), ("legacy 0.20%", LEGACY_P0_ROUND_TRIP)]


def pf(x):
    x = np.asarray(x)
    w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d.sort_values("t").reset_index(drop=True)
    feats = [c for c in d.columns if c not in NON_FEAT and pd.api.types.is_numeric_dtype(d[c])]
    d[feats] = d[feats].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    d = d.dropna(subset=["realized_ret"]).reset_index(drop=True)
    print(f"候选 {len(d)}  特征 {len(feats)}  {d['t'].min()} → {d['t'].max()}")
    g = d["realized_ret"].to_numpy()
    print(f"裸池: 毛均值 {g.mean():+.5f}  毛PF {pf(g)}  "
          f"胜率 {(d['label'] == 1).mean() * 100:.1f}% (TP5/SL2 平衡 28.6%)\n")

    P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03,
         "min_data_in_leaf": 30, "feature_fraction": 0.8, "bagging_fraction": 0.8,
         "bagging_freq": 5, "verbose": -1}
    purge = pd.Timedelta(minutes=BAR_MIN * PURGE_BARS)
    n = len(d)
    val = n // (N_FOLDS + 1)
    folds = []
    for k in range(N_FOLDS):
        lo = n - (N_FOLDS - k) * val
        te = d.iloc[lo:lo + val]
        tr = d[d["t"] < te["t"].iloc[0] - purge]
        if len(tr) < 200 or len(te) < 60:
            continue
        folds.append((tr, te))

    rows = []
    for i, (tr, te) in enumerate(folds, 1):
        cut = int(len(tr) * 0.85)
        bo = lgb.train(P, lgb.Dataset(tr[feats].iloc[:cut], label=tr["realized_ret"].iloc[:cut]),
                       num_boost_round=400,
                       valid_sets=[lgb.Dataset(tr[feats].iloc[cut:],
                                               label=tr["realized_ret"].iloc[cut:])],
                       callbacks=[lgb.early_stopping(30, verbose=False)])
        s = bo.predict(te[feats])
        k = max(int(len(te) * TOP_FRAC), 1)
        top = te["realized_ret"].to_numpy()[np.argsort(-s)][:k]
        raw = te["realized_ret"].to_numpy()
        # single-feature control: the pool's own densest signal
        base_col = "ma_spread_pct" if "ma_spread_pct" in feats else feats[0]
        b = te["realized_ret"].to_numpy()[np.argsort(te[base_col].to_numpy())][:k]
        rows.append({"fold": i, "start": str(te["t"].iloc[0])[:10], "n_test": len(te),
                     "best_iter": int(bo.best_iteration or 0),
                     "raw_gross_PF": pf(raw), "raw_mean": round(float(raw.mean()), 5),
                     "top_gross_PF": pf(top), "top_mean": round(float(top.mean()), 5),
                     "top_n": k, "base_gross_PF": pf(b),
                     "top_win": round(float((top > 0).mean()), 3)})
        print(f"  fold{i} {rows[-1]['start']} n={len(te)} iter={rows[-1]['best_iter']}: "
              f"裸毛PF {rows[-1]['raw_gross_PF']} → top20 毛PF {rows[-1]['top_gross_PF']} "
              f"(单特征对照 {rows[-1]['base_gross_PF']})")

    if not rows:
        print("样本不足")
        return 1
    tops = np.array([r["top_mean"] for r in rows])
    raws = np.array([r["raw_mean"] for r in rows])
    print(f"\n各折 top20 毛均值: {[f'{v:+.5f}' for v in tops]}")
    print(f"各折 裸池 毛均值: {[f'{v:+.5f}' for v in raws]}")
    print(f"\n{'成本口径':<24} {'裸池净均值':>12} {'top20净均值':>13} {'top20净PF':>11}")
    by_cost = []
    all_top = np.concatenate([np.array([r["top_mean"]]) for r in rows])
    for name, c in LADDER:
        rn, tn = raws - c, tops - c
        by_cost.append({"cost_name": name, "cost": c,
                        "raw_net_mean": round(float(rn.mean()), 5),
                        "top_net_mean": round(float(tn.mean()), 5),
                        "top_folds_positive": int((tn > 0).sum()), "n_folds": len(tn)})
        print(f"{name:<24} {rn.mean():>+12.5f} {tn.mean():>+13.5f} "
              f"{int((tn > 0).sum())}/{len(tn)} 折为正")

    out = {"pool": str(POOL.relative_to(PROJECT)), "n": len(d), "folds": rows,
           "by_cost": by_cost, "top_frac": TOP_FRAC,
           "note": "gross PF shown because net gains from diluting a fixed cost "
                   "are not selection skill"}
    (PROJECT / "analysis" / "output" / "judgment_v6_short_walkforward.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Classifier or regressor? The judgment layer may be learning the wrong quantity.

On the 25,602-row pool the picture is contradictory only if you assume the target
is right. 18 of 28 features are significant against net return at p<0.01, the
strongest at rho=-0.278, and LightGBM still lands at AUC 0.4962. Bucketing by
atr_pct resolves it:

  ATR quintile      win rate     net per trade
  0.34%              36.2%        +0.0685%
  1.13%              37.7%        +0.3456%

Win rate is flat across a fivefold spread in net. The edge lives in the SIZE of
the outcome, not in the probability of winning -- and a binary classifier on
win/lose is trained on exactly the dimension that carries no information, so
AUC 0.5 is the correct answer to the question it was asked.

Three targets, same pool, same CPCV folds, same features:

  CLS      binary label (today's production target)
  REG      net return, so magnitude is the thing being ranked
  REG_ATR  net per unit of ATR, which removes the volatility scale and asks
           which setups pay best for the risk taken -- the ranking a position
           sizer would actually want

Each is scored the same way: top-decile net per trade against the pool, over CPCV
folds with purging and embargo, with the sample's MDE printed beside it so a lift
inside the noise is reported as undecidable rather than as a win.

Read-only, train pool (<2026-05-04), no holdout, no promote. Whether any of this
becomes ACTIVE is an owner decision (live discipline 10).

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_judgment_target_choice.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.costs import SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.factors.library import FACTORS  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS  # noqa: E402
from scripts.diag_judgment_big_pool import attach_alphas, cpcv_groups  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_owner_side_short_100_6m.csv"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
N_SPLITS, N_TEST = 6, 2
Z = 2.5758 + 0.8416
TOP_Q = 0.90


def train_score(tr, te, feats, target, objective):
    import lightgbm as lgb
    params = {"objective": objective, "learning_rate": 0.05, "num_leaves": 31,
              "min_data_in_leaf": 80, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 1,
              "verbose": -1, "seed": 20260728}
    ds = lgb.Dataset(tr[feats].astype(float), label=tr[target].astype(float))
    b = lgb.train(params, ds, num_boost_round=250)
    return b, b.predict(te[feats].astype(float))


def evaluate(d, feats, target, objective, name):
    lifts, tops, pools, imps = [], [], [], []
    for tr_i, te_i in cpcv_groups(d, N_SPLITS, N_TEST):
        tr, te = d.iloc[tr_i], d.iloc[te_i]
        if tr[target].nunique() < 2:
            continue
        b, s = train_score(tr, te, feats, target, objective)
        thr = np.nanquantile(s, TOP_Q)
        sel = te["net"].to_numpy()[s >= thr]
        if len(sel) < 30:
            continue
        pool_net = float(te["net"].mean())
        lifts.append(float(sel.mean()) - pool_net)
        tops.append(float(sel.mean()))
        pools.append(pool_net)
        imps.append(pd.Series(b.feature_importance("gain"), index=feats))
    if not lifts:
        return None
    sigma = float(d["net"].std())
    n_top = int(len(d) / N_SPLITS * N_TEST * (1 - TOP_Q))
    mde = Z * sigma * math.sqrt(2.0 / max(n_top, 1))
    imp = pd.concat(imps, axis=1).mean(axis=1).sort_values(ascending=False)
    return {"variant": name, "target": target, "objective": objective,
            "n_splits": len(lifts),
            "lift_bp_median": round(float(np.median(lifts)) * 1e4, 2),
            "splits_positive": int(sum(1 for x in lifts if x > 0)),
            "top_net_median": round(float(np.median(tops)), 6),
            "pool_net_median": round(float(np.median(pools)), 6),
            "mde_bp": round(mde * 1e4, 2),
            "resolvable": bool(abs(float(np.median(lifts))) * 1e4 >= mde * 1e4),
            "top_features": [(str(k), round(float(v), 1)) for k, v in imp.head(8).items()]}


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT_START].sort_values("t").reset_index(drop=True)
    d["net"] = d["realized_ret"].astype(float) - SWAP_TAKER
    d["atr_pct"] = pd.to_numeric(d["atr_pct"], errors="coerce")
    d = d[d["atr_pct"].notna() & (d["atr_pct"] > 0) & d["net"].notna()].reset_index(drop=True)
    d["net_atr"] = d["net"] / d["atr_pct"]

    base = [c for c in FEATURE_COLUMNS if c in d.columns]
    print(f"池 {len(d)} 行 / {d['symbol'].nunique()} 币   每笔净 "
          f"{d['net'].mean()*100:+.4f}%   胜率 {(d['net']>0).mean()*100:.1f}%")
    print(f"计算 alpha 因子({len(FACTORS)} 个)…", flush=True)
    d, alpha_cols = attach_alphas(d)
    good = [c for c in alpha_cols if d[c].notna().mean() > 0.8]
    feats = base + good
    print(f"特征 {len(base)} 基础 + {len(good)} alpha = {len(feats)}\n")

    runs = [
        ("分类器 · 学 label(现行)", "label", "binary"),
        ("回归器 · 学 净收益", "net", "regression"),
        ("回归器 · 学 净收益/ATR", "net_atr", "regression"),
    ]
    out = []
    print(f"{'方案':<26} {'切分':>5} {'顶档提升':>10} {'门槛':>8} {'正切分':>8} {'顶档净':>10}")
    for name, target, obj in runs:
        r = evaluate(d, feats, target, obj, name)
        if r is None:
            print(f"{name:<26} 无有效切分")
            continue
        out.append(r)
        mark = "  ✅" if r["resolvable"] and r["lift_bp_median"] > 0 else ""
        print(f"{name:<26} {r['n_splits']:>5} {r['lift_bp_median']:>+9.2f}bp "
              f"{r['mde_bp']:>7.1f}bp {r['splits_positive']:>3}/{r['n_splits']:<4} "
              f"{r['top_net_median']*100:>+9.4f}%{mark}")

    print("\n=== 各方案最看重的特征(CPCV 平均 gain)===")
    for r in out:
        names = ", ".join(f"{k}" for k, _v in r["top_features"][:5])
        print(f"  {r['variant']:<26} {names}")

    winners = [r for r in out if r["resolvable"] and r["lift_bp_median"] > 0]
    if winners:
        b = max(winners, key=lambda r: r["lift_bp_median"])
        verdict = (f"{b['variant']} 胜出:顶档提升 {b['lift_bp_median']:+.2f}bp,"
                   f"超过门槛 {b['mde_bp']:.1f}bp,{b['splits_positive']}/{b['n_splits']} "
                   f"个 CPCV 切分为正,顶档每笔净 {b['top_net_median']*100:+.4f}% "
                   f"vs 全池 {b['pool_net_median']*100:+.4f}%")
    else:
        best = max(out, key=lambda r: r["lift_bp_median"]) if out else None
        verdict = (f"三种目标都没有产生可分辨的提升;最好的是 {best['variant']} "
                   f"({best['lift_bp_median']:+.2f}bp,门槛 {best['mde_bp']:.1f}bp,"
                   f"{best['splits_positive']}/{best['n_splits']} 正)"
                   if best else "无有效结果")
    print(f"\n判读: {verdict}")
    print("注:CPCV 清除+禁运,训练池内,未碰 holdout;不 promote。")

    (PROJECT / "analysis" / "output" / "diag_judgment_target_choice.json").write_text(
        json.dumps({"pool": POOL.name, "n": len(d), "n_features": len(feats),
                    "results": out, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

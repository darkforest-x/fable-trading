"""Redo the judgment question on the 25,602-row pool, with the alpha library added.

Today's power analysis was run on judgment_yolo_short_v6_wide.csv -- 5,802 rows --
and concluded that nothing smaller than ~24bp could be resolved. That conclusion
was right about the arithmetic and wrong about the inputs: the repo already holds
judgment_yolo_owner_side_short_100_6m.csv, 25,602 short candidates across 100
symbols, entirely inside the train window (2025-11-04..2026-05-03, holdout starts
05-04). Four times the rows halves the MDE, so questions that were unanswerable at
5.8k may be answerable here.

Two things get tested, in that order, because the second is pointless if the first
fails:

  POWER    what this pool can actually resolve, one-sample and for a top decile.
           Printed before any model runs, so the result is read against a bar that
           was set beforehand rather than after seeing it.

  FEATURES the 28 production features against those plus src/factors/library's 20
           causal alphas. The library exists and has never been put in front of the
           judgment layer -- factor_ic_screen ran, but screening a factor's IC is
           not the same as asking whether it adds to a model that already has 28
           others.

Evaluated with CPCV (purged combinatorial CV, embargo on label_end) rather than a
single split, because a single 4-fold split is exactly what produced this
project's retracted "judgment layer inverts" claim. Every lift is reported beside
the MDE of the sample it was measured on; a lift under its MDE is written as
undecidable, not as a result.

Read-only, train side only, no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_judgment_big_pool.py
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

POOL = PROJECT / "data" / "judgment_yolo_owner_side_short_100_6m.csv"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
N_SPLITS, N_TEST = 6, 2          # CPCV: C(6,2) = 15 train/test combinations
EMBARGO_BARS = 72                # the label horizon, so test labels cannot leak
Z = 2.5758 + 0.8416              # p<0.01, power 0.80


def mde_one(sigma: float, n: int) -> float:
    return Z * sigma / math.sqrt(max(n, 1))


def mde_two(sigma: float, n_group: int) -> float:
    return Z * sigma * math.sqrt(2.0 / max(n_group, 1))


def attach_alphas(d: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Compute the alpha library at each signal bar. Causal:每个因子只回看。"""
    series = list_series(bar="15m")
    names = sorted(FACTORS)
    cache: dict[str, pd.DataFrame | None] = {}
    cols = {n: np.full(len(d), np.nan) for n in names}
    pos_of: dict[str, list[int]] = {}
    for idx, sym in enumerate(d["symbol"]):
        pos_of.setdefault(sym, []).append(idx)

    for sym, idxs in pos_of.items():
        key = ("okx", sym)
        if key not in series:
            continue
        if sym not in cache:
            try:
                fr = add_indicators(add_mas(load_series(series[key])))
                vals = {}
                for n in names:
                    try:
                        vals[n] = pd.to_numeric(FACTORS[n](fr), errors="coerce").to_numpy(dtype=float)
                    except Exception:  # noqa: BLE001 -- a broken factor must not sink the run
                        vals[n] = np.full(len(fr), np.nan)
                cache[sym] = (pd.to_datetime(fr["open_time"], utc=True), vals)
            except Exception:  # noqa: BLE001
                cache[sym] = None
        e = cache[sym]
        if e is None:
            continue
        times, vals = e
        for idx in idxs:
            i = int(times.searchsorted(d["t"].iloc[idx]))
            if not (0 <= i < len(times)):
                continue
            for n in names:
                cols[n][idx] = vals[n][i]
    out = d.copy()
    for n in names:
        out["af_" + n] = cols[n]
    return out, ["af_" + n for n in names]


def cpcv_groups(d: pd.DataFrame, n_splits: int, n_test: int):
    """Purged combinatorial CV: contiguous time blocks, embargo around each test block."""
    from itertools import combinations
    bounds = np.linspace(0, len(d), n_splits + 1).astype(int)
    blocks = [(bounds[i], bounds[i + 1]) for i in range(n_splits)]
    for combo in combinations(range(n_splits), n_test):
        test_idx = np.concatenate([np.arange(*blocks[b]) for b in combo])
        banned = set()
        for b in combo:
            lo, hi = blocks[b]
            banned.update(range(max(0, lo - EMBARGO_BARS), min(len(d), hi + EMBARGO_BARS)))
        train_idx = np.array([i for i in range(len(d)) if i not in banned])
        if len(train_idx) < 500 or len(test_idx) < 200:
            continue
        yield train_idx, test_idx


def run(d: pd.DataFrame, feats: list[str], ret_col: str, label: str) -> dict:
    import lightgbm as lgb

    params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 31,
              "min_data_in_leaf": 80, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 1,
              "verbose": -1, "seed": 20260728}
    lifts, aucs, tops = [], [], []
    for tr_i, te_i in cpcv_groups(d, N_SPLITS, N_TEST):
        tr, te = d.iloc[tr_i], d.iloc[te_i]
        if tr[label].nunique() < 2:
            continue
        ds = lgb.Dataset(tr[feats].astype(float), label=tr[label].astype(int))
        b = lgb.train(params, ds, num_boost_round=250)
        s = b.predict(te[feats].astype(float))
        thr = np.nanquantile(s, 0.9)
        top = te[ret_col].to_numpy()[s >= thr]
        if len(top) < 30:
            continue
        lifts.append(float(np.mean(top) - te[ret_col].mean()))
        tops.append(float(np.mean(top)))
        y = te[label].to_numpy()
        if len(np.unique(y)) == 2:
            from sklearn.metrics import roc_auc_score
            aucs.append(float(roc_auc_score(y, s)))
    return {"n_splits": len(lifts),
            "lift_bp_median": round(float(np.median(lifts)) * 1e4, 2) if lifts else None,
            "lift_bp_mean": round(float(np.mean(lifts)) * 1e4, 2) if lifts else None,
            "splits_positive": int(sum(1 for x in lifts if x > 0)),
            "top_net_median": round(float(np.median(tops)), 6) if tops else None,
            "auc_median": round(float(np.median(aucs)), 4) if aucs else None}


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT_START].sort_values("t").reset_index(drop=True)
    d["net"] = d["realized_ret"].astype(float) - SWAP_TAKER
    sigma = float(d["net"].std())

    print(f"池 {POOL.name}")
    print(f"  {len(d)} 行 / {d['symbol'].nunique()} 币 / "
          f"{str(d['t'].min())[:10]} ~ {str(d['t'].max())[:10]}(holdout 未碰)")
    print(f"  每笔净 {d['net'].mean()*100:+.4f}%   标准差 {sigma*100:.3f}%   "
          f"正类率 {d['label'].mean():.3f}\n")

    print("=== 先定标尺:这个池子能分辨多大的效应(p<0.01, 功效 80%)===")
    n_top = len(d) // 10
    print(f"  全池 n={len(d):,}        单样本 MDE = {mde_one(sigma, len(d))*1e4:6.2f} bp")
    print(f"  顶十分位 n={n_top:,}      双样本 MDE = {mde_two(sigma, n_top)*1e4:6.2f} bp")
    print(f"  (对照:5802 行的旧池分别是 "
          f"{mde_one(sigma, 5802)*1e4:.1f}bp / {mde_two(sigma, 580)*1e4:.1f}bp)\n")

    base = [c for c in FEATURE_COLUMNS if c in d.columns]
    print(f"基础特征 {len(base)} 个。计算 alpha 因子库({len(FACTORS)} 个)…", flush=True)
    d, alpha_cols = attach_alphas(d)
    good = [c for c in alpha_cols if d[c].notna().mean() > 0.8]
    print(f"  可用 {len(good)}/{len(alpha_cols)} 个(其余缺失率 >20%)\n")

    results = []
    for name, feats in (("基础 28 特征", base),
                        (f"基础 + alpha {len(good)} 个", base + good)):
        r = run(d, feats, "net", "label")
        r["variant"] = name
        r["n_features"] = len(feats)
        results.append(r)
        print(f"=== {name}({len(feats)} 特征)===")
        print(f"  CPCV {r['n_splits']} 个切分   顶档提升 中位 "
              f"{r['lift_bp_median']}bp / 均值 {r['lift_bp_mean']}bp")
        print(f"  胜过全池的切分 {r['splits_positive']}/{r['n_splits']}   "
              f"AUC 中位 {r['auc_median']}")

    mde_top = mde_two(sigma, n_top) * 1e4
    best = max(results, key=lambda r: r["lift_bp_median"] or -1e9)
    lift = best["lift_bp_median"] or 0
    if lift >= mde_top:
        verdict = (f"{best['variant']} 顶档提升 {lift:+.1f}bp,超过该样本量的分辨门槛 "
                   f"{mde_top:.1f}bp,且 {best['splits_positive']}/{best['n_splits']} "
                   f"个 CPCV 切分为正 → 判断层在这个池子上是有排序能力的")
    else:
        verdict = (f"最好的一版顶档提升 {lift:+.1f}bp,仍低于分辨门槛 {mde_top:.1f}bp "
                   f"→ 无法判定;{best['splits_positive']}/{best['n_splits']} 个切分为正"
                   f"可作为方向参考,但不构成结论")
    print(f"\n判读: {verdict}")
    print("注:CPCV 清除+禁运,训练池内,未碰 holdout;不 promote。")

    (PROJECT / "analysis" / "output" / "diag_judgment_big_pool.json").write_text(
        json.dumps({"pool": POOL.name, "n_rows": len(d), "sigma": sigma,
                    "mde_one_bp": round(mde_one(sigma, len(d)) * 1e4, 2),
                    "mde_top_decile_bp": round(mde_top, 2),
                    "n_alpha_usable": len(good), "results": results,
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

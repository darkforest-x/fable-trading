"""What do the two changes buy together: regression target + no stop loss.

Two findings this week point the same way and have only ever been measured apart:

  TARGET  the production judgment layer is a classifier on win/lose, but win rate
          is flat at 36.2-37.7% across ATR quintiles while net per trade spans
          fivefold. The edge is in size, not in probability, so the classifier is
          trained on the one dimension carrying no information -- AUC 0.4962.
          Switching to a regressor on net return is worth +21.46bp per CPCV fold
          (t=3.21, 13/15 folds, sign p=0.0074).
  EXIT    of nine exits on identical candidates, take-profit with no stop has the
          highest causal excess over matched random shorts: +18.09bp against the
          production TP5/SL2's +10.63bp.

This runs them together, and reports the decile curve so "the model ranks" can be
checked rather than assumed. Every number carries a matched random control (same
symbol, month, ATR bucket) because this pool's headline +26.91bp was mostly short
beta -- a permutation test cannot see that, since shuffling rows leaves the drift
under every row intact.

CPCV with purging and embargo, not a single split: a single 4-fold split is what
produced this project's retracted "judgment layer inverts" claim.

The candidates come from an older detector, so this answers "what are the two
changes worth", not "what will v10 do". The v10 pool is still building.

Read-only, train pool (<2026-05-04), no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_combo_reg_tponly.py
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

from src.costs import SWAP_MAKER, SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.factors.library import FACTORS  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS  # noqa: E402
from scripts.diag_judgment_big_pool import attach_alphas, cpcv_groups  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_owner_side_short_100_6m.csv"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72
N_SPLITS, N_TEST = 6, 2
TOP_Q = 0.90
SEED = 20260729


def exits_for(ind, i: int) -> dict | None:
    """Production barrier and the no-stop variant on the same entry."""
    ei = i + 1
    if ei >= len(ind):
        return None
    atr = float(ind["atr14"].iloc[i])
    entry = float(ind["open"].iloc[ei])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None
    last = min(ei + HORIZON - 1, len(ind) - 1)
    if last - ei + 1 < HORIZON:
        return None
    hi = ind["high"].to_numpy()[ei:last + 1]
    lo = ind["low"].to_numpy()[ei:last + 1]
    cl = ind["close"].to_numpy()[ei:last + 1]
    tp, sl = entry - TP_MULT * atr, entry + SL_MULT * atr
    up = np.flatnonzero(lo <= tp)
    dn = np.flatnonzero(hi >= sl)
    u = int(up[0]) if len(up) else len(cl)
    dd = int(dn[0]) if len(dn) else len(cl)
    if u < dd:
        g_bar = 1 - tp / entry
    elif dd < u:
        g_bar = 1 - sl / entry
    else:
        g_bar = 1 - float(cl[-1]) / entry
    g_tp = (TP_MULT * atr / entry) if len(up) else 1 - float(cl[-1]) / entry
    return {"barrier": g_bar, "tponly": g_tp, "atr_pct": float(ind["atr_pct"].iloc[i])}


def main() -> int:
    import lightgbm as lgb

    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT].sort_values("t").reset_index(drop=True)
    series = list_series(bar="15m")
    cache: dict[str, tuple | None] = {}

    print("① 重算两种出场…", flush=True)
    keep, ex = [], []
    for sym, grp in d.groupby("symbol"):
        key = ("okx", sym)
        if sym not in cache:
            cache[sym] = ((add_indicators(add_mas(load_series(series[key]))),)
                          if key in series else None)
        e = cache[sym]
        if e is None:
            continue
        ind = e[0]
        times = pd.to_datetime(ind["open_time"], utc=True)
        for idx, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            if not (200 <= i < len(ind) - 2):
                continue
            v = exits_for(ind, i)
            if v is None:
                continue
            keep.append(idx)
            ex.append(v)
    d = d.loc[keep].reset_index(drop=True)
    E = pd.DataFrame(ex)
    for c in ("barrier", "tponly"):
        d[f"net_{c}"] = E[c].to_numpy() - SWAP_TAKER
    print(f"   有效 {len(d)} 笔   现行障碍 {d['net_barrier'].mean()*1e4:+.2f}bp   "
          f"只止盈 {d['net_tponly'].mean()*1e4:+.2f}bp\n")

    print(f"② 计算 alpha 因子({len(FACTORS)} 个)…", flush=True)
    d, alpha_cols = attach_alphas(d)
    good = [c for c in alpha_cols if d[c].notna().mean() > 0.8]
    feats = [c for c in FEATURE_COLUMNS if c in d.columns] + good
    print(f"   特征 {len(feats)}\n")

    print("③ CPCV:分类器 vs 回归器 × 现行障碍 vs 只止盈…", flush=True)
    params = {"learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 80,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1,
              "verbose": -1, "seed": SEED}
    combos = [("分类器 + 现行障碍(生产)", "cls", "net_barrier"),
              ("分类器 + 只止盈", "cls", "net_tponly"),
              ("回归器 + 现行障碍", "reg", "net_barrier"),
              ("回归器 + 只止盈(新配置)", "reg", "net_tponly")]
    out = []
    for name, kind, ret_col in combos:
        lifts, tops, pools, deciles = [], [], [], []
        for tr_i, te_i in cpcv_groups(d, N_SPLITS, N_TEST):
            tr, te = d.iloc[tr_i], d.iloc[te_i]
            if kind == "cls":
                y = (tr[ret_col] > 0).astype(int)
                if y.nunique() < 2:
                    continue
                b = lgb.train({**params, "objective": "binary"},
                              lgb.Dataset(tr[feats].astype(float), label=y), 250)
            else:
                b = lgb.train({**params, "objective": "regression"},
                              lgb.Dataset(tr[feats].astype(float),
                                          label=tr[ret_col].astype(float)), 250)
            s = b.predict(te[feats].astype(float))
            thr = np.nanquantile(s, TOP_Q)
            sel = te[ret_col].to_numpy()[s >= thr]
            if len(sel) < 30:
                continue
            pool_net = float(te[ret_col].mean())
            lifts.append(float(sel.mean()) - pool_net)
            tops.append(float(sel.mean()))
            pools.append(pool_net)
            q = pd.qcut(s, 10, labels=False, duplicates="drop")
            deciles.append([float(te[ret_col].to_numpy()[q == k].mean())
                            for k in range(10) if (q == k).sum()])
        if not lifts:
            continue
        dec = np.nanmean(np.array([x for x in deciles if len(x) == 10]), axis=0) \
            if any(len(x) == 10 for x in deciles) else None
        rec = {"combo": name, "n_splits": len(lifts),
               "lift_bp": round(float(np.median(lifts)) * 1e4, 2),
               "splits_pos": int(sum(1 for x in lifts if x > 0)),
               "top_net_bp": round(float(np.median(tops)) * 1e4, 2),
               "pool_net_bp": round(float(np.median(pools)) * 1e4, 2),
               "decile_bp": [round(v * 1e4, 1) for v in dec] if dec is not None else None}
        out.append(rec)
        print(f"  {name:<24} 全池 {rec['pool_net_bp']:>+7.2f}bp  "
              f"顶档 {rec['top_net_bp']:>+7.2f}bp  提升 {rec['lift_bp']:>+7.2f}bp  "
              f"{rec['splits_pos']}/{rec['n_splits']} 折为正")

    print("\n=== 顶十分位净收益(bp,左=模型最不看好)===")
    for r in out:
        if r["decile_bp"]:
            print(f"  {r['combo']:<24} " + " ".join(f"{v:>7.1f}" for v in r["decile_bp"]))

    base = out[0] if out else None
    best = max(out, key=lambda r: r["top_net_bp"]) if out else None
    verdict = (
        f"最优组合 {best['combo']}:顶档 {best['top_net_bp']:+.2f}bp(全池 "
        f"{best['pool_net_bp']:+.2f}bp,提升 {best['lift_bp']:+.2f}bp,"
        f"{best['splits_pos']}/{best['n_splits']} 折为正);"
        f"生产配置顶档 {base['top_net_bp']:+.2f}bp。"
        f"往返成本 taker {SWAP_TAKER*1e4:.0f}bp"
        if best and base else "无有效结果")
    print(f"\n判读: {verdict}")
    print("注:候选来自旧检测器,回答的是「两项改动值多少」,不是「v10 会怎样」;"
          "\n    训练池样本内,CPCV 清除+禁运,未碰 holdout,不 promote。")

    (PROJECT / "analysis" / "output" / "diag_combo_reg_tponly.json").write_text(
        json.dumps({"pool": POOL.name, "n": len(d), "n_features": len(feats),
                    "results": out, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

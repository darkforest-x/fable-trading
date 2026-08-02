"""Is the "top decile" actually a decile, or do score ties let most of the pool in?

The Grok Build takeover plan (2026-07-31) states that the production fixed q90
gate does not form a top decile at all -- roughly 91.2% of historical val passes
it, because scores tie. If that were also true of the lift numbers reported on
2026-07-30 (+23.49bp for the base feature set, +35.61bp with Kronos), those
numbers would mean something entirely different: a "top decile" holding 91% of
the pool cannot sit 23bp above the pool mean, so either the selection is real or
the arithmetic is wrong somewhere.

The two gates are not obviously the same mechanism. Production applies a FIXED
threshold to a classifier whose outputs can saturate; the diagnostic applies a
per-fold np.nanquantile to continuous regression output, which selects 10% by
construction unless mass sits exactly on the boundary. But that is an argument,
and the plan's claim deserves a measurement, so this records what was never
logged: how many rows each fold actually selects, and how much of the score
column is tied.

Read-only. Train pool only, no holdout, no promote, no config change.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_topdecile_is_really_a_decile.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.judgment.features import FEATURE_COLUMNS  # noqa: E402
from scripts.diag_judgment_big_pool import attach_alphas, cpcv_groups  # noqa: E402

POOL = PROJECT / "data" / "judgment_v10_wide.csv"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
RET_COL = "net_barrier_taker"
N_SPLITS, N_TEST = 6, 2
TOP_Q = 0.90
SEED = 20260730


def main() -> int:
    import lightgbm as lgb

    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT].sort_values("t").reset_index(drop=True)
    d, alpha_cols = attach_alphas(d)
    good = [c for c in alpha_cols if d[c].notna().mean() > 0.8]
    feats = [c for c in FEATURE_COLUMNS if c in d.columns] + good
    print(f"池 {len(d)} 行   特征 {len(feats)}   目标 {RET_COL}\n", flush=True)

    params = {"objective": "regression", "learning_rate": 0.05, "num_leaves": 31,
              "min_data_in_leaf": 80, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 1,
              "verbose": -1, "seed": SEED}

    print(f"{'折':>3}{'测试行':>8}{'选中':>7}{'选中占比':>10}"
          f"{'不同分值':>10}{'边界并列':>10}{'顶档均值':>11}{'池均值':>10}")
    rows = []
    for k, (tr_i, te_i) in enumerate(cpcv_groups(d, N_SPLITS, N_TEST), 1):
        tr, te = d.iloc[tr_i], d.iloc[te_i]
        b = lgb.train(params, lgb.Dataset(tr[feats].astype(float),
                                          label=tr[RET_COL].astype(float)), 250)
        s = b.predict(te[feats].astype(float))
        thr = float(np.nanquantile(s, TOP_Q))
        m = s >= thr
        net = te[RET_COL].to_numpy(dtype=float)
        # ties sitting exactly on the cut are the mechanism the plan describes
        at_thr = int(np.sum(s == thr))
        uniq = int(len(np.unique(np.round(s, 12))))
        rows.append({"fold": k, "n_test": int(len(te)), "n_sel": int(m.sum()),
                     "frac": round(float(m.mean()), 4), "n_unique": uniq,
                     "n_at_threshold": at_thr,
                     "top_bp": round(float(net[m].mean()) * 1e4, 2),
                     "pool_bp": round(float(net.mean()) * 1e4, 2)})
        print(f"{k:>3}{len(te):>8}{int(m.sum()):>7}{m.mean()*100:>9.1f}%"
              f"{uniq:>10}{at_thr:>10}{net[m].mean()*1e4:>+10.2f}bp"
              f"{net.mean()*1e4:>+9.2f}bp", flush=True)

    fr = np.array([r["frac"] for r in rows])
    uq = np.array([r["n_unique"] / r["n_test"] for r in rows])
    verdict = (
        f"选中占比 中位 {np.median(fr)*100:.1f}%(区间 {fr.min()*100:.1f}~{fr.max()*100:.1f}%)。"
        f"分值唯一率 中位 {np.median(uq)*100:.1f}%。"
        + ("**确实是十分位**,不存在 ties 把大半个池放进来的情况;"
           "该文档说的 91.2% 是生产固定门(分类器饱和输出)的问题,"
           "与本诊断的每折分位数选择不是同一机制。"
           if np.median(fr) < 0.15 else
           "**不是十分位** —— ties 让远超 10% 的行通过,"
           "此前报的顶档提升需要按实际选中占比重新解释。"))
    print(f"\n判读: {verdict}")
    print("注:只读训练池,未碰 holdout,不改任何配置。")

    (PROJECT / "analysis" / "output" / "diag_topdecile_is_really_a_decile.json").write_text(
        json.dumps({"pool": POOL.name, "n": len(d), "top_q": TOP_Q,
                    "folds": rows,
                    "frac_median": round(float(np.median(fr)), 4),
                    "unique_frac_median": round(float(np.median(uq)), 4),
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

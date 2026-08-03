"""Where did 32bp go? A single-variable ladder from 2026-07-30 to P2-L2.

Pre-registered in analysis/prereg_attribution_20260803.md and committed before
this file ran. The bar and the definition of "explained" are fixed there and are
not restated loosely here.

Two runs over nearly the same candidates disagreed by about 32bp and in sign:
+23.49bp top-decile lift on 07-30, -15.91bp foldwise exact-top under P2. Five
things differ at once -- alphas, dataset and feature semantics, split scheme, cost
line, model configuration -- so the only honest reading is one rung at a time.

  S0   47 features (28 + 19 alphas), v10 pool, 250 fixed rounds, 15-fold CPCV
  S0b  drop the alphas                        -> ALPHAS
  S1   swap to the P1 immutable dataset       -> DATA + SEMANTICS
  S2   swap to 5-fold walkforward             -> SPLIT
  S3   add the 5bp slippage pressure          -> COST
  S4   swap to P2's LightGBM parameters       -> MODEL CONFIG

Both metrics are reported at every rung -- lift over the fold's pool mean, and
absolute top-decile net -- so that changing the yardstick is itself visible
rather than smuggled in with something else.

Research only. Pre-holdout both sides, no holdout read, nothing promoted, no
artifact written to models/, P2's rejected verdict untouched.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_attribution_23bp_vs_minus16bp.py
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

V10_POOL = PROJECT / "data" / "judgment_v10_wide.csv"
P1_POOL = PROJECT / "data" / "p1" / "p1_short_l2_preholdout_aade2a334448d644.csv"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
TOP_Q = 0.90
EXTRA_SLIPPAGE = 0.0005          # P2's accepted pressure line, on top of P1's taker
MY_SEED, P2_SEED = 20260730, 42

MY_PARAMS = {
    "objective": "regression", "learning_rate": 0.05, "num_leaves": 31,
    "min_data_in_leaf": 80, "feature_fraction": 0.8, "bagging_fraction": 0.8,
    "bagging_freq": 1, "verbose": -1, "seed": MY_SEED,
}
P2_PARAMS = {
    "objective": "regression", "learning_rate": 0.05, "num_leaves": 15,
    "min_child_samples": 30, "feature_fraction": 0.8, "bagging_fraction": 0.8,
    "bagging_freq": 1, "lambda_l2": 1.0, "verbose": -1, "seed": P2_SEED,
    "deterministic": True, "force_col_wise": True,
}


def walkforward_groups(n: int, folds: int = 5):
    """P2's split: contiguous blocks, each tested on what follows its predecessors."""
    edges = np.linspace(0, n, folds + 2, dtype=int)
    for k in range(1, folds + 1):
        yield np.arange(0, edges[k]), np.arange(edges[k], edges[k + 1])


def score_fold(tr, te, feats, target, params, rounds, early):
    import lightgbm as lgb

    dtr = lgb.Dataset(tr[feats].astype(float), label=tr[target].astype(float))
    if early:
        # P2 held out the tail of train as its early-stopping watcher
        cut = int(len(tr) * 0.8)
        dsub = lgb.Dataset(tr.iloc[:cut][feats].astype(float),
                           label=tr.iloc[:cut][target].astype(float))
        dval = lgb.Dataset(tr.iloc[cut:][feats].astype(float),
                           label=tr.iloc[cut:][target].astype(float))
        b = lgb.train(params, dsub, rounds, valid_sets=[dval],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
        best = b.best_iteration or 1
    else:
        b = lgb.train(params, dtr, rounds)
        best = rounds
    return b.predict(te[feats].astype(float), num_iteration=best), best


def rung(name, what, d, feats, target, splitter, params, rounds, early, extra_cost):
    lifts, tops, sizes, iters = [], [], [], []
    for tr_i, te_i in splitter(d):
        tr, te = d.iloc[tr_i], d.iloc[te_i]
        if len(te) < 60 or len(tr) < 200:
            continue
        s, best = score_fold(tr, te, feats, target, params, rounds, early)
        iters.append(best)
        net = te[target].to_numpy(dtype=float) - extra_cost
        m = s >= np.nanquantile(s, TOP_Q)
        if m.sum() < 20:
            continue
        lifts.append(float(net[m].mean()) - float(net.mean()))
        tops.append(float(net[m].mean()))
        sizes.append(int(m.sum()))
    if not lifts:
        return None
    w = np.array(sizes, dtype=float)
    return {
        "rung": name, "isolates": what, "n_folds": len(lifts),
        "lift_bp": round(float(np.median(lifts)) * 1e4, 2),
        "top_abs_bp": round(float(np.average(tops, weights=w)) * 1e4, 2),
        "median_best_iteration": int(np.median(iters)) if iters else None,
        "mean_selected": int(np.mean(sizes)),
    }


def main() -> int:
    v10 = pd.read_csv(V10_POOL)
    v10["t"] = pd.to_datetime(v10["signal_time"], utc=True)
    v10 = v10[v10["t"] < HOLDOUT].sort_values("t").reset_index(drop=True)
    p1 = pd.read_csv(P1_POOL)
    p1["t"] = pd.to_datetime(p1["signal_time"], utc=True)
    p1 = p1[p1["t"] < HOLDOUT].sort_values("t").reset_index(drop=True)
    print(f"v10 池 {len(v10)} 行   P1 池 {len(p1)} 行", flush=True)
    print(f"目标:v10 net_barrier_taker  ·  P1 net_ret_swap_taker(均已含 10bp taker)\n",
          flush=True)

    v10a, alpha_cols = attach_alphas(v10.copy())
    good = [c for c in alpha_cols if v10a[c].notna().mean() > 0.8]
    f28 = [c for c in FEATURE_COLUMNS if c in v10.columns]
    f47 = f28 + good
    p1_f28 = [c for c in FEATURE_COLUMNS if c in p1.columns]

    cpcv = lambda d: cpcv_groups(d, 6, 2)          # noqa: E731
    wf = lambda d: walkforward_groups(len(d), 5)   # noqa: E731

    rows = []
    print(f"{'级':<5}{'隔离的变量':<22}{'顶档提升':>11}{'顶档绝对':>11}{'折':>4}{'轮':>6}", flush=True)
    for args in (
        ("S0", "— 我 07-30 的配置", v10a, f47, "net_barrier_taker", cpcv, MY_PARAMS, 250, False, 0.0),
        ("S0b", "alpha 因子(19 个)", v10a, f28, "net_barrier_taker", cpcv, MY_PARAMS, 250, False, 0.0),
        ("S1", "数据 + 特征语义", p1, p1_f28, "net_ret_swap_taker", cpcv, MY_PARAMS, 250, False, 0.0),
        ("S2", "切分方案", p1, p1_f28, "net_ret_swap_taker", wf, MY_PARAMS, 250, False, 0.0),
        ("S3", "成本口径 +5bp", p1, p1_f28, "net_ret_swap_taker", wf, MY_PARAMS, 250, False, EXTRA_SLIPPAGE),
        ("S4", "模型配置 + 早停", p1, p1_f28, "net_ret_swap_taker", wf, P2_PARAMS, 600, True, EXTRA_SLIPPAGE),
    ):
        r = rung(*args)
        if r is None:
            print(f"  {args[0]} 无有效折"); continue
        rows.append(r)
        print(f"{r['rung']:<5}{r['isolates']:<22}{r['lift_bp']:>+10.2f}bp"
              f"{r['top_abs_bp']:>+10.2f}bp{r['n_folds']:>4}"
              f"{str(r['median_best_iteration']):>6}", flush=True)

    print(f"\n{'从':<5}{'到':<5}{'变量':<22}{'顶档绝对增量':>14}")
    deltas = []
    for a, b in zip(rows, rows[1:]):
        dd = b["top_abs_bp"] - a["top_abs_bp"]
        deltas.append({"from": a["rung"], "to": b["rung"], "isolates": b["isolates"],
                       "delta_top_abs_bp": round(dd, 2)})
        print(f"{a['rung']:<5}{b['rung']:<5}{b['isolates']:<22}{dd:>+13.2f}bp")

    total = rows[-1]["top_abs_bp"] - rows[0]["top_abs_bp"]
    covered = sum(d["delta_top_abs_bp"] for d in deltas)
    biggest = max(deltas, key=lambda d: abs(d["delta_top_abs_bp"])) if deltas else None
    flips = [d for d, a, b in zip(deltas, rows, rows[1:])
             if np.sign(a["top_abs_bp"]) != np.sign(b["top_abs_bp"])]
    print(f"\nS0→S4 总差 {total:+.2f}bp   四级之和 {covered:+.2f}bp   "
          f"残差 {total - covered:+.2f}bp")
    print(f"P2 报告的逐折 exact-top 为 -15.91bp;本阶梯 S4 顶档绝对 "
          f"{rows[-1]['top_abs_bp']:+.2f}bp")
    if biggest:
        print(f"最大单级:{biggest['isolates']} {biggest['delta_top_abs_bp']:+.2f}bp")
    if flips:
        print(f"符号翻转发生在:{', '.join(f['isolates'] for f in flips)}")
    else:
        print("没有单级导致符号翻转 —— 差是累积的,不是某一步造成的")

    (PROJECT / "analysis" / "output" / "attribution_23bp_vs_minus16bp.json").write_text(
        json.dumps({"rungs": rows, "deltas": deltas,
                    "total_bp": round(total, 2), "covered_bp": round(covered, 2),
                    "residual_bp": round(total - covered, 2),
                    "p2_reported_bp": -15.91,
                    "biggest": biggest,
                    "sign_flips": [f["isolates"] for f in flips]},
                   indent=2, ensure_ascii=False) + "\n")
    print("\n注:pre-holdout 双方,未读 holdout,未 promote,未写 models/,不改 P2 rejected 结论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

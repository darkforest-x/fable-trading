"""Do the Kronos features earn a place? Judged against a bar fixed in advance.

The bar was written into build_kronos_features.py before any of this ran, so it
cannot be relaxed now that there is a number to look at:

    top-decile lift > +17.76bp   (v10's best without Kronos)
    permutation p < 0.01
    reported beside a matched random control

Design keeps everything except the feature set identical. Same pool, same CPCV
folds, same target, same top-decile rule -- the only difference between arms is
whether the six kr_* columns are present. The comparison is PAIRED on folds,
because fold-to-fold variation dominates the noise and cancels when both arms see
the same fold; scoring each arm against the pool separately throws that away.

Three arms, because "Kronos helps" and "Kronos alone works" are different claims:

  BASE    28 production features + 19 causal alphas
  +KR     the same, plus the six Kronos forecast features
  KR only the six alone, which says whether the forecast carries anything at all

The permutation shuffles the target within the training fold and refits, so it
answers "could this lift have come from nothing". The matched control (same
symbol, month and ATR bucket, not selected) answers the different question of
whether the selection is just picking high volatility -- this pool's own headline
was mostly short beta, and a permutation test is blind to that.

Read-only, train pool (<2026-05-04), no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_kronos_feature_value.py
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
from src.judgment.features import FEATURE_COLUMNS  # noqa: E402
from scripts.diag_judgment_big_pool import attach_alphas, cpcv_groups  # noqa: E402

POOL = PROJECT / "data" / "judgment_v10_wide.csv"
KRONOS = PROJECT / "data" / "kronos_feats_v10.csv"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
RET_COL = "net_barrier_taker"
BAR_LIFT_BP = 17.76          # pre-registered: v10's best without Kronos
N_SPLITS, N_TEST = 6, 2
TOP_Q = 0.90
N_PERM = 30
ATR_BUCKETS = 5
SEED = 20260730


def main() -> int:
    import lightgbm as lgb

    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT].reset_index(drop=True)
    kr = pd.read_csv(KRONOS)
    kr["signal_time"] = kr["signal_time"].astype(str)
    d["signal_time"] = d["signal_time"].astype(str)
    before = len(d)
    d = d.merge(kr, on=["symbol", "signal_time"], how="inner")
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d.sort_values("t").reset_index(drop=True)
    print(f"池 {before} 行,并上 Kronos 后 {len(d)} 行"
          f"(丢 {before-len(d)} = {100*(before-len(d))/before:.1f}%)")

    kr_cols = [c for c in d.columns if c.startswith("kr_")]
    print(f"Kronos 特征 {len(kr_cols)}: {kr_cols}")
    print(f"目标 {RET_COL}   池均值 {d[RET_COL].mean()*1e4:+.2f}bp\n")

    print(f"计算 alpha 因子…", flush=True)
    d, alpha_cols = attach_alphas(d)
    good = [c for c in alpha_cols if d[c].notna().mean() > 0.8]
    base_feats = [c for c in FEATURE_COLUMNS if c in d.columns] + good
    arms = {"BASE 28+19": base_feats,
            "+Kronos 6": base_feats + kr_cols,
            "仅 Kronos 6": kr_cols}
    print(f"BASE {len(base_feats)} 特征   +KR {len(base_feats)+len(kr_cols)}\n")

    params = {"objective": "regression", "learning_rate": 0.05, "num_leaves": 31,
              "min_data_in_leaf": 80, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 1,
              "verbose": -1, "seed": SEED}
    rng = np.random.default_rng(SEED)
    folds = list(cpcv_groups(d, N_SPLITS, N_TEST))
    d["bucket"] = pd.qcut(d["atr_pct"], ATR_BUCKETS, labels=False, duplicates="drop")

    def fit_score(tr, te, feats, shuffle=False):
        y = tr[RET_COL].to_numpy(dtype=float)
        if shuffle:
            y = rng.permutation(y)
        b = lgb.train(params, lgb.Dataset(tr[feats].astype(float), label=y), 250)
        return b.predict(te[feats].astype(float))

    per_fold: dict[str, list[float]] = {k: [] for k in arms}
    ctrl_fold: dict[str, list[float]] = {k: [] for k in arms}
    for tr_i, te_i in folds:
        tr, te = d.iloc[tr_i], d.iloc[te_i]
        net = te[RET_COL].to_numpy(dtype=float)
        pool = float(net.mean())
        for name, feats in arms.items():
            s = fit_score(tr, te, feats)
            m = s >= np.nanquantile(s, TOP_Q)
            if m.sum() < 30:
                continue
            per_fold[name].append(float(net[m].mean()) - pool)
            # ATR-matched, not selected: separates "picked winners" from
            # "picked high volatility", which this pool punishes you for missing
            q = te["bucket"].to_numpy()
            want = pd.Series(q[m]).value_counts()
            pick = []
            for b_, n_ in want.items():
                cand = np.flatnonzero((q == b_) & ~m)
                if len(cand):
                    pick += list(rng.choice(cand, size=min(n_, len(cand)), replace=False))
            ctrl_fold[name].append(float(net[pick].mean()) - pool if pick else np.nan)

    print(f"{'方案':<14}{'顶档提升':>11}{'正折数':>9}{'ATR对照':>11}{'超对照':>11}")
    rows = []
    for name in arms:
        L = np.array(per_fold[name])
        C = np.array(ctrl_fold[name], dtype=float)
        if len(L) == 0:
            continue
        med = float(np.median(L)) * 1e4
        cmed = float(np.nanmedian(C)) * 1e4
        rows.append({"arm": name, "n_folds": len(L), "lift_bp": round(med, 2),
                     "folds_pos": int((L > 0).sum()),
                     "ctrl_bp": round(cmed, 2),
                     "vs_ctrl_bp": round(med - cmed, 2)})
        print(f"{name:<14}{med:>+10.2f}bp{int((L>0).sum()):>6}/{len(L):<3}"
              f"{cmed:>+10.2f}bp{med-cmed:>+10.2f}bp")

    # paired: same folds, so common fold variance cancels
    base_L = np.array(per_fold["BASE 28+19"])
    kr_L = np.array(per_fold["+Kronos 6"])
    n = min(len(base_L), len(kr_L))
    diff = kr_L[:n] - base_L[:n]
    se = float(diff.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    t = float(diff.mean() / se) if se and se > 0 else float("nan")
    print(f"\n配对(+Kronos − BASE,同折):平均差 {diff.mean()*1e4:+.2f}bp   "
          f"t={t:+.2f}   {int((diff>0).sum())}/{n} 折为正")

    print(f"\n置换检验({N_PERM} 次,打乱目标重训 +Kronos)…", flush=True)
    perm = []
    for i in range(N_PERM):
        tr_i, te_i = folds[i % len(folds)]
        tr, te = d.iloc[tr_i], d.iloc[te_i]
        net = te[RET_COL].to_numpy(dtype=float)
        s = fit_score(tr, te, arms["+Kronos 6"], shuffle=True)
        m = s >= np.nanquantile(s, TOP_Q)
        if m.sum() >= 30:
            perm.append(float(net[m].mean()) - float(net.mean()))
    perm = np.array(perm)
    real = float(np.median(kr_L))
    p_perm = float((perm >= real).mean()) if len(perm) else float("nan")
    print(f"  真实 {real*1e4:+.2f}bp   打乱中位 {np.median(perm)*1e4:+.2f}bp   "
          f"p90 {np.percentile(perm,90)*1e4:+.2f}bp   p={p_perm:.4f}")

    kr_row = next(r for r in rows if r["arm"] == "+Kronos 6")
    passed = (kr_row["lift_bp"] > BAR_LIFT_BP and p_perm < 0.01
              and kr_row["vs_ctrl_bp"] > 0)
    verdict = (
        f"{'通过' if passed else '未通过'}事先判据。"
        f"+Kronos 顶档提升 {kr_row['lift_bp']:+.2f}bp "
        f"(门槛 >{BAR_LIFT_BP:+.2f}bp {'✓' if kr_row['lift_bp']>BAR_LIFT_BP else '✗'})、"
        f"置换 p={p_perm:.4f}(门槛 <0.01 {'✓' if p_perm<0.01 else '✗'})、"
        f"超 ATR 对照 {kr_row['vs_ctrl_bp']:+.2f}bp"
        f"({'✓' if kr_row['vs_ctrl_bp']>0 else '✗'});"
        f"配对比 BASE {diff.mean()*1e4:+.2f}bp(t={t:+.2f})")
    print(f"\n判读: {verdict}")
    print("注:训练池内,CPCV,未碰 holdout,不 promote;判据在生成特征之前就已写死。")

    (PROJECT / "analysis" / "output" / "diag_kronos_feature_value.json").write_text(
        json.dumps({"pool": POOL.name, "n": len(d), "ret_col": RET_COL,
                    "bar_lift_bp": BAR_LIFT_BP, "arms": rows,
                    "paired_diff_bp": round(float(diff.mean()) * 1e4, 2),
                    "paired_t": round(t, 3),
                    "paired_folds_pos": int((diff > 0).sum()), "paired_n": n,
                    "p_perm": round(p_perm, 5), "passed": bool(passed),
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

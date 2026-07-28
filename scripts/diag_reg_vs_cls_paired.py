"""Regressor against classifier on the SAME folds — the test with the power to decide.

Scoring each target against the pool separately put both inside a 41.6bp
single-fold MDE, so neither was declarable. That comparison also throws away the
structure that matters: both models see identical folds, so the fold-to-fold
variation that dominates the noise is COMMON to them and cancels in a paired
test. Pairing is not a trick to get a smaller p -- it is the correct test for two
treatments applied to the same units.

Three statistics, because each fails differently:

  PAIRED   mean per-fold difference (regressor top-decile minus classifier
           top-decile), with its own standard error. Answers "is one better".
  SIGN     how many folds favour the regressor. Distribution-free, and immune to
           one fold with a huge return driving the mean.
  PERM     shuffle the target within each fold and re-rank, 200 times, to get the
           lift a model with no information would produce on this data. Answers
           "could this have come from nothing", which neither of the others does.

CPCV folds overlap by construction, so they are not independent samples and every
p here is optimistic. That is stated with the numbers rather than after them.

Read-only, train pool, no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_reg_vs_cls_paired.py
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
from src.factors.library import FACTORS  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS  # noqa: E402
from scripts.diag_judgment_big_pool import attach_alphas, cpcv_groups  # noqa: E402
from scripts.diag_judgment_target_choice import train_score  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_owner_side_short_100_6m.csv"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
N_SPLITS, N_TEST = 6, 2
TOP_Q = 0.90
N_PERM = 200
RNG = np.random.default_rng(20260728)


def top_net(scores: np.ndarray, net: np.ndarray) -> float:
    thr = np.nanquantile(scores, TOP_Q)
    sel = net[scores >= thr]
    return float(sel.mean()) if len(sel) >= 30 else float("nan")


def sign_p(k: int, n: int) -> float:
    """Two-sided exact binomial under p=0.5."""
    from math import comb
    tail = sum(comb(n, i) for i in range(k, n + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT_START].sort_values("t").reset_index(drop=True)
    d["net"] = d["realized_ret"].astype(float) - SWAP_TAKER
    d["atr_pct"] = pd.to_numeric(d["atr_pct"], errors="coerce")
    d = d[d["atr_pct"].notna() & (d["atr_pct"] > 0) & d["net"].notna()].reset_index(drop=True)

    base = [c for c in FEATURE_COLUMNS if c in d.columns]
    print(f"池 {len(d)} 行,计算 alpha 因子…", flush=True)
    d, alpha_cols = attach_alphas(d)
    good = [c for c in alpha_cols if d[c].notna().mean() > 0.8]
    feats = base + good
    print(f"特征 {len(feats)}   全池每笔净 {d['net'].mean()*100:+.4f}%\n")

    rows = []
    perm_lifts: list[float] = []
    for fold, (tr_i, te_i) in enumerate(cpcv_groups(d, N_SPLITS, N_TEST), 1):
        tr, te = d.iloc[tr_i], d.iloc[te_i]
        net = te["net"].to_numpy()
        _, s_cls = train_score(tr, te, feats, "label", "binary")
        _, s_reg = train_score(tr, te, feats, "net", "regression")
        c, r = top_net(s_cls, net), top_net(s_reg, net)
        if not (np.isfinite(c) and np.isfinite(r)):
            continue
        rows.append({"fold": fold, "n_test": len(te), "pool": float(net.mean()),
                     "cls": c, "reg": r, "diff": r - c})
        print(f"  折 {fold:>2}  全池 {net.mean()*100:>+8.4f}%  "
              f"分类 {c*100:>+8.4f}%  回归 {r*100:>+8.4f}%  "
              f"差 {(r-c)*100:>+8.4f}%", flush=True)

        # permutation: same features, shuffled target, so any lift is chance
        if fold <= 5:
            tr_p = tr.copy()
            for _ in range(N_PERM // 5):
                tr_p["net"] = RNG.permutation(tr["net"].to_numpy())
                _, s_p = train_score(tr_p, te, feats, "net", "regression")
                v = top_net(s_p, net)
                if np.isfinite(v):
                    perm_lifts.append(v - float(net.mean()))

    f = pd.DataFrame(rows)
    diff = f["diff"].to_numpy()
    n = len(diff)
    mean_d = float(diff.mean())
    se = float(diff.std(ddof=1) / math.sqrt(n))
    t = mean_d / se if se > 0 else float("nan")
    k = int((diff > 0).sum())
    p_sign = sign_p(k, n)

    reg_lift = float((f["reg"] - f["pool"]).mean())
    perm = np.array(perm_lifts) if perm_lifts else np.array([np.nan])
    p_perm = float((perm >= reg_lift).mean()) if np.isfinite(perm).all() else float("nan")

    print(f"\n=== 配对检验({n} 折,同折同数据)===")
    print(f"  回归 − 分类 的平均差   {mean_d*1e4:+.2f} bp   标准误 {se*1e4:.2f} bp")
    print(f"  t = {t:+.2f}                 |t|>2.58 对应 p<0.01")
    print(f"  符号检验 {k}/{n} 折回归更好   双侧 p = {p_sign:.4f}")
    print(f"\n=== 置换检验({len(perm)} 次,打乱目标后重训)===")
    print(f"  真实回归顶档提升 {reg_lift*1e4:+.2f} bp")
    print(f"  打乱后提升 中位 {np.median(perm)*1e4:+.2f} bp  "
          f"p90 {np.percentile(perm,90)*1e4:+.2f} bp")
    print(f"  p(打乱能达到真实值) = {p_perm:.4f}")

    strong = abs(t) > 2.58 and p_sign < 0.01 and p_perm < 0.01
    ok = abs(t) > 1.96 and p_sign < 0.05 and p_perm < 0.05
    if strong:
        level = "达到本项目 p<0.01 的标准"
    elif ok:
        level = "达到 p<0.05,未达本项目 p<0.01 的标准"
    else:
        level = "未达显著性标准"
    verdict = (f"回归目标比分类目标平均高 {mean_d*1e4:+.2f}bp(t={t:+.2f}),"
               f"{k}/{n} 折更优(p={p_sign:.4f}),置换 p={p_perm:.4f} —— {level}。"
               f"注:CPCV 折之间有重叠,以上 p 值偏乐观。")
    print(f"\n判读: {verdict}")
    print("注:训练池内,未碰 holdout;是否改判断层目标属 owner 决策。")

    (PROJECT / "analysis" / "output" / "diag_reg_vs_cls_paired.json").write_text(
        json.dumps({"pool": POOL.name, "n_rows": len(d), "n_folds": n,
                    "folds": rows, "mean_diff_bp": round(mean_d * 1e4, 2),
                    "t_stat": round(t, 3), "sign_k": k, "p_sign": round(p_sign, 5),
                    "reg_lift_bp": round(reg_lift * 1e4, 2),
                    "n_perm": len(perm), "p_perm": round(p_perm, 5),
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

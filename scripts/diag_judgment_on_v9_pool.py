"""Does a judgment layer trained on v9's OWN candidates rank, or invert again?

The frozen v11 layer inverts on the v6 pool: its top decile returns -0.2979%
against the pool's +0.0312%, its bottom decile +0.0975%. The standing explanation
is same-source -- it was trained on v11's candidate distribution and anti-selects
anyone else's. That explanation has been offered twice before, so it deserves a
test rather than another retelling: train on v9's own candidates and see whether
the ranking comes back.

The pool is still being built (297 symbols, ~36k rows expected). Symbols are
shuffled, so the partial file is a valid random sample of the universe and this
can run now -- but at ~4k rows the power analysis says only effects above roughly
26bp are separable from noise. So this answers DIRECTION, not magnitude:

  positive lift  -> same-source was the problem; the full pool is worth training on
  negative lift  -> the inversion survives its own candidates, and the cause is
                    something deeper than which detector produced them

Both labels are scored, since the exit is undecided and a layer that ranks under
one and not the other would say the ranking is about the barrier rather than the
setup.

Split by TIME, not at random (iron rule 2): train on the earlier portion, judge
on the later. Deciles carry Wilson intervals, after this project twice read a
collapse off a sample too small to carry one.

Read-only, train side only (<2026-05-04), no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_judgment_on_v9_pool.py
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

from src.judgment.features import FEATURE_COLUMNS  # noqa: E402

POOL = PROJECT / "data" / "judgment_v9_partial.csv"
TRAIN_FRAC = 0.70
MDE_Z = 2.5758 + 0.8416          # p<0.01, power 0.80


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def evaluate(name: str, tr: pd.DataFrame, te: pd.DataFrame,
             feats: list[str], label: str, ret_col: str) -> dict:
    import lightgbm as lgb

    ds = lgb.Dataset(tr[feats].astype(float), label=tr[label].astype(int))
    params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 15,
              "min_data_in_leaf": 60, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 1,
              "verbose": -1, "seed": 20260728}
    booster = lgb.train(params, ds, num_boost_round=200)
    te = te.copy()
    te["score"] = booster.predict(te[feats].astype(float))

    n_dec = 10 if len(te) >= 400 else 5
    te["dec"] = pd.qcut(te["score"], n_dec, labels=False, duplicates="drop")
    pool_net = float(te[ret_col].mean())
    rows = []
    for d in sorted(te["dec"].dropna().unique()):
        g = te[te["dec"] == d]
        v = g[ret_col].to_numpy()
        k = int((v > 0).sum())
        lo, hi = wilson(k, len(v))
        rows.append({"decile": int(d), "n": len(g),
                     "net": round(float(v.mean()), 6),
                     "win": round(k / len(v), 4),
                     "ci": [round(lo, 4), round(hi, 4)]})
    top = te[te["dec"] == te["dec"].max()]
    lift = float(top[ret_col].mean()) - pool_net
    sigma = float(te[ret_col].std())
    mde = MDE_Z * sigma / math.sqrt(max(len(top), 1))

    print(f"\n=== {name} ===")
    print(f"训练 {len(tr)} 笔 → 测试 {len(te)} 笔   正类率 "
          f"{tr[label].mean():.3f}   全池净 {pool_net*100:+.4f}%")
    print(f"{'十分位':>6} {'笔数':>6} {'净收益':>11} {'胜率':>8} {'95%CI':>16}")
    for r in rows:
        print(f"{r['decile']:>6} {r['n']:>6} {r['net']*100:>+10.4f}% "
              f"{r['win']*100:>7.1f}% [{r['ci'][0]*100:>5.1f},{r['ci'][1]*100:>5.1f}]")
    verdict = ("方向为正" if lift > 0 else "仍为反选")
    print(f"顶档 vs 全池: {lift*1e4:+.2f}bp   ({verdict})")
    print(f"  该样本量下可分辨的最小效应 ≈ {mde*1e4:.1f}bp"
          f" → 本结论{'超过' if abs(lift) >= mde else '低于'}分辨门槛"
          f"{'' if abs(lift) >= mde else ',只能看方向不能看量级'}")
    return {"variant": name, "n_train": len(tr), "n_test": len(te),
            "pool_net": pool_net, "top_lift_bp": round(lift * 1e4, 2),
            "mde_bp": round(mde * 1e4, 2),
            "resolvable": bool(abs(lift) >= mde), "deciles": rows}


def main() -> int:
    if not POOL.exists():
        print(f"池子不存在: {POOL}")
        return 2
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d.sort_values("t").reset_index(drop=True)
    feats = [c for c in FEATURE_COLUMNS if c in d.columns]
    extra = [c for c in ("btc_ret24", "btc_ret96", "btc_above_ema200",
                         "btc_atr_pct") if c in d.columns]
    feats += extra
    cut = int(len(d) * TRAIN_FRAC)
    tr, te = d.iloc[:cut], d.iloc[cut:]

    print(f"v9 候选池(建设中的部分):{len(d)} 行 / {d['symbol'].nunique()} 币")
    print(f"时间 {str(d['t'].min())[:10]} ~ {str(d['t'].max())[:10]}"
          f"   特征 {len(feats)} 个")
    print(f"按时间切:训练 <{str(tr['t'].max())[:10]}  测试 >={str(te['t'].min())[:10]}")

    out = []
    out.append(evaluate("标签=现行障碍 TP5/SL2", tr, te, feats,
                        "label_barrier", "net_barrier_taker"))
    out.append(evaluate("标签=纯持 72 根", tr, te, feats,
                        "label_hold", "net_hold_taker"))

    # A lift smaller than the sample can resolve is not a finding in either
    # direction. Calling one "inversion" would repeat the n=16 misread that this
    # project has already paid for once -- the honest statement is that the test
    # did not have the power to answer, and the deciles below show why: they are
    # unordered, not reversed.
    lifts = [r["top_lift_bp"] for r in out]
    resolvable = [r for r in out if r["resolvable"]]
    if not resolvable:
        mdes = " / ".join(f"{r['mde_bp']:.0f}bp" for r in out)
        verdict = (f"无法判定。顶档差 {lifts[0]:+.1f}bp / {lifts[1]:+.1f}bp,"
                   f"而该样本量的分辨门槛是 {mdes} —— 差值全部落在噪声里,"
                   f"既不能说它排序有效,也不能说它反选。等整池(~36k 行)再判。")
    elif all(r["top_lift_bp"] > 0 for r in resolvable):
        verdict = (f"可分辨的那套标签下顶档优于全池({lifts[0]:+.1f}bp / "
                   f"{lifts[1]:+.1f}bp)→ 同源是 v11 反选的原因,整池值得正式训练")
    else:
        verdict = (f"在可分辨的量级上依然反选({lifts[0]:+.1f}bp / {lifts[1]:+.1f}bp)"
                   f" → 病因不止同源")
    print(f"\n判读: {verdict}")
    print(f"注:部分池({len(d)} 行),训练池内时间切分,未碰 holdout;"
          f"样本量只够看方向,量级需等整池。")

    (PROJECT / "analysis" / "output" / "diag_judgment_on_v9_pool.json").write_text(
        json.dumps({"pool": POOL.name, "n_rows": len(d),
                    "n_symbols": int(d["symbol"].nunique()),
                    "train_frac": TRAIN_FRAC, "n_features": len(feats),
                    "results": out, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

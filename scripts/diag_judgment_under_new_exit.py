"""Does the judgment layer still select, once the exit changes?

Two findings collide. The barrier sweep says holding 72 bars with no TP/SL beats
TP5xATR/SL2xATR by 8.5x on the same candidates (+0.2645% vs +0.0312%). The
judgment layer, meanwhile, was trained on labels computed FROM those barriers --
its target is the TP5/SL2 outcome. So its ranking may be selecting for "reaches a
5xATR target inside 72 bars", which is not the same quantity as "drifts down over
72 bars", and an exit change could silently invalidate it.

That has to be measured before any judgment rebuild, because the rebuild's label
depends on the answer:

  decile ranking by the frozen model's score, scored against BOTH exits. If the
  top decile beats the pool under the current barriers but not under the hold,
  the layer is barrier-specific and the new pool needs new labels. If it beats
  under both, the ranking is picking up something about the setup itself and
  survives the exit decision.

Reported with Wilson intervals on the win rates, because decile counts here are
in the hundreds and this project has twice read a collapse off a sample too small
to carry one.

Read-only, train pool (<2026-05-04), no holdout, no promote, no retraining.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_judgment_under_new_exit.py
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
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_short_v6_wide.csv"
MODEL = PROJECT / "models" / "frozen_tp5_sl2_swap_yolo_v11_reg_20260718.txt"
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def simulate(ind, i: int, barriers: bool) -> dict | None:
    ei = i + 1
    if ei >= len(ind):
        return None
    atr = float(ind["atr14"].iloc[i])
    entry = float(ind["open"].iloc[ei])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None
    last = min(ei + HORIZON - 1, len(ind) - 1)
    hi = ind["high"].to_numpy()[ei:last + 1]
    lo = ind["low"].to_numpy()[ei:last + 1]
    cl = ind["close"].to_numpy()[ei:last + 1]
    if len(cl) < 2:
        return None
    if not barriers:
        return {"ret": 1 - cl[-1] / entry}
    tp, sl = entry - TP_MULT * atr, entry + SL_MULT * atr
    up = int(np.argmax(lo <= tp)) if (lo <= tp).any() else len(cl)
    dn = int(np.argmax(hi >= sl)) if (hi >= sl).any() else len(cl)
    if up < dn:
        return {"ret": 1 - tp / entry}
    if dn < up:
        return {"ret": 1 - sl / entry}
    return {"ret": 1 - cl[-1] / entry}


def main() -> int:
    import lightgbm as lgb

    if not MODEL.exists():
        print(f"判断层模型缺失: {MODEL}")
        return 2
    booster = lgb.Booster(model_file=str(MODEL))
    d = pd.read_csv(POOL)
    feats = [c for c in FEATURE_COLUMNS if c in d.columns]
    missing = [c for c in booster.feature_name() if c not in d.columns]
    if missing:
        print(f"池中缺少模型特征 {len(missing)} 个,取交集 {len(feats)}: {missing[:5]}")
    use = [c for c in booster.feature_name() if c in d.columns]
    d["score"] = booster.predict(d[use].astype(float),
                                 num_iteration=booster.best_iteration)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)

    series = list_series(bar="15m")
    cache: dict[str, object] = {}
    rows = []
    for sym, grp in d.groupby("symbol"):
        key = ("okx", sym)
        if sym not in cache:
            cache[sym] = add_indicators(add_mas(load_series(series[key]))) if key in series else None
        ind = cache[sym]
        if ind is None:
            continue
        times = pd.to_datetime(ind["open_time"], utc=True)
        for _, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            if not (200 <= i < len(ind) - 2):
                continue
            a = simulate(ind, i, True)
            b = simulate(ind, i, False)
            if a is None or b is None:
                continue
            rows.append({"score": float(r["score"]),
                         "barrier": a["ret"] - SWAP_TAKER,
                         "hold": b["ret"] - SWAP_TAKER})
    df = pd.DataFrame(rows)
    print(f"评分候选 {len(df)}   模型 {MODEL.name}\n")

    df["decile"] = pd.qcut(df["score"], 10, labels=False, duplicates="drop")
    print(f"{'十分位':>6} {'笔数':>7} {'现行障碍净':>12} {'胜率':>8} {'95%CI':>16} "
          f"| {'纯持72根净':>12} {'胜率':>8} {'95%CI':>16}")
    out = []
    for dec in sorted(df["decile"].dropna().unique()):
        g = df[df["decile"] == dec]
        rec = {"decile": int(dec), "n": len(g)}
        line = f"{int(dec):>6} {len(g):>7}"
        for col, lab in (("barrier", "现行"), ("hold", "纯持")):
            v = g[col].to_numpy()
            k = int((v > 0).sum())
            lo_, hi_ = wilson(k, len(v))
            rec[f"{col}_net"] = round(float(v.mean()), 6)
            rec[f"{col}_win"] = round(k / len(v), 4)
            rec[f"{col}_ci"] = [round(lo_, 4), round(hi_, 4)]
            line += (f" {v.mean()*100:>+11.4f}% {k/len(v)*100:>7.1f}% "
                     f"[{lo_*100:>5.1f},{hi_*100:>5.1f}]")
        out.append(rec)
        print(line)

    pool_b, pool_h = df["barrier"].mean(), df["hold"].mean()
    top = df[df["decile"] == df["decile"].max()]
    print(f"\n{'全池':>6} {len(df):>7} {pool_b*100:>+11.4f}%{'':>34}"
          f" {pool_h*100:>+11.4f}%")
    lift_b = top["barrier"].mean() - pool_b
    lift_h = top["hold"].mean() - pool_h
    print(f"\n顶十分位相对全池:  现行障碍 {lift_b*1e4:+.2f}bp   纯持72根 {lift_h*1e4:+.2f}bp")

    if lift_b > 0 and lift_h > 0:
        verdict = (f"判断层排序在两种出场下都有效(顶档 {lift_b*1e4:+.2f}bp / "
                   f"{lift_h*1e4:+.2f}bp)→ 它抓的是形态本身,不依赖障碍,"
                   f"换出场不作废")
    elif lift_b > 0:
        verdict = (f"判断层只在现行障碍下有效({lift_b*1e4:+.2f}bp),换成纯持后 "
                   f"{lift_h*1e4:+.2f}bp → 它学的是「够不够到 5xATR」,"
                   f"改出场必须重打标签重训")
    else:
        verdict = (f"判断层顶档在两种出场下都不优于全池({lift_b*1e4:+.2f}bp / "
                   f"{lift_h*1e4:+.2f}bp)→ 该模型对本池没有选择力")
    print(f"\n判读: {verdict}")
    print("注:样本内、训练池;顶档 n 约 580,胜率带 Wilson 区间以免再次误读小样本。")

    (PROJECT / "analysis" / "output" / "diag_judgment_under_new_exit.json").write_text(
        json.dumps({"pool": POOL.name, "model": MODEL.name, "n": len(df),
                    "pool_net": {"barrier": round(float(pool_b), 6),
                                 "hold": round(float(pool_h), 6)},
                    "deciles": out,
                    "top_lift_bp": {"barrier": round(lift_b * 1e4, 2),
                                    "hold": round(lift_h * 1e4, 2)},
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

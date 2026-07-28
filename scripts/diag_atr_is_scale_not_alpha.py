"""Is atr_pct's -0.278 correlation with net a signal, or the barrier's own arithmetic?

On the 25,602-row pool, 18 of 28 features correlate with net return at p<0.01 and
the strongest is atr_pct at rho=-0.278 -- yet LightGBM lands at AUC 0.4962, a coin
flip. Features that carry signal and a model that cannot use it do not go
together, so one of the two readings is wrong.

The suspicion is mechanical. Both barriers scale with ATR (TP 5x, SL 2x), so at a
28% win rate the expected outcome per trade is 0.28*5 - 0.72*2 = -0.04 ATR. Every
trade's return is therefore roughly proportional to its own ATR, and a negative
correlation between atr_pct and net follows from the barrier definition rather
than from any ability to tell good setups from bad. If that is what it is, then
selecting low-ATR candidates shrinks every outcome toward zero instead of
selecting winners -- the mean improves and the strategy still has no edge.

Two tests separate the readings:

  SCALE     express each trade's return in ATR UNITS instead of percent. A
            mechanical relationship disappears under that change of units; a real
            one survives it.
  SELECTION sort by atr_pct and check what the low-ATR bucket actually earns --
            per trade, in percent, and as a total. Selection that only shrinks
            magnitude shows a smaller loss per trade AND a smaller total, never a
            profit that was not there.

The same treatment is applied to the other strong correlates, since if the cause
is barrier scaling they should all behave the same way.

Read-only, train pool, no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_atr_is_scale_not_alpha.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scipy import stats  # noqa: E402

from src.costs import SWAP_TAKER  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_owner_side_short_100_6m.csv"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
PROBES = ["atr_pct", "pre_range48", "pre_range168", "spread_mean24",
          "full_spread", "dense_frac48"]


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT_START].reset_index(drop=True)
    d["net"] = d["realized_ret"].astype(float) - SWAP_TAKER
    d["atr_pct"] = pd.to_numeric(d["atr_pct"], errors="coerce")
    ok = d["atr_pct"].notna() & (d["atr_pct"] > 0) & d["net"].notna()
    d = d[ok].reset_index(drop=True)
    # the same outcome measured in units of its own volatility: cost has to be
    # converted too, or the comparison silently changes what is being charged
    d["net_atr"] = (d["realized_ret"] - SWAP_TAKER) / d["atr_pct"]

    print(f"{len(d)} 笔   每笔净 {d['net'].mean()*100:+.4f}%   "
          f"以 ATR 计 {d['net_atr'].mean():+.4f} ATR\n")

    print("=== 检验一:换成 ATR 单位后,相关性还在吗 ===")
    print(f"{'特征':<16} {'rho(净%)':>11} {'rho(净/ATR)':>13}  判读")
    rows = []
    for c in PROBES:
        if c not in d.columns:
            continue
        v = pd.to_numeric(d[c], errors="coerce")
        m = v.notna()
        r_pct, p_pct = stats.spearmanr(v[m], d["net"][m])
        r_atr, p_atr = stats.spearmanr(v[m], d["net_atr"][m])
        shrink = 1 - abs(r_atr) / max(abs(r_pct), 1e-9)
        note = ("机械(换单位后消失)" if abs(r_atr) < 0.05
                else "部分机械" if shrink > 0.5 else "真实(换单位后仍在)")
        rows.append({"feature": c, "rho_pct": round(float(r_pct), 4),
                     "p_pct": float(p_pct), "rho_atr": round(float(r_atr), 4),
                     "p_atr": float(p_atr), "reading": note})
        print(f"{c:<16} {r_pct:>+10.4f} {r_atr:>+12.4f}  {note}")

    print("\n=== 检验二:按 atr_pct 分五档,低波动档到底赚不赚 ===")
    d["q"] = pd.qcut(d["atr_pct"], 5, labels=False, duplicates="drop")
    print(f"{'档':>3} {'笔数':>7} {'ATR中位':>9} {'每笔净':>10} {'合计净':>11} "
          f"{'胜率':>7} {'以ATR计':>9}")
    buckets = []
    for q in sorted(d["q"].dropna().unique()):
        g = d[d["q"] == q]
        tot = float(g["net"].sum())
        buckets.append({"q": int(q), "n": len(g),
                        "atr_med": float(g["atr_pct"].median()),
                        "net_mean": float(g["net"].mean()),
                        "net_total": tot,
                        "win": float((g["net"] > 0).mean()),
                        "net_atr": float(g["net_atr"].mean())})
        print(f"{int(q):>3} {len(g):>7} {g['atr_pct'].median()*100:>8.2f}% "
              f"{g['net'].mean()*100:>+9.4f}% {tot:>+10.3f} "
              f"{(g['net']>0).mean()*100:>6.1f}% {g['net_atr'].mean():>+8.4f}")

    lo, hi = buckets[0], buckets[-1]
    all_total = float(d["net"].sum())
    print(f"\n{'全池':>3} {len(d):>7} {d['atr_pct'].median()*100:>8.2f}% "
          f"{d['net'].mean()*100:>+9.4f}% {all_total:>+10.3f} "
          f"{(d['net']>0).mean()*100:>6.1f}% {d['net_atr'].mean():>+8.4f}")

    # A selection that only shrinks magnitude keeps roughly the same return per
    # unit of risk while cutting the total. A real selection lifts both.
    # "shrink only" means the low-ATR bucket earns the same per unit of risk while
    # cutting the total. Anything else has to be read off the sign of the gap, not
    # asserted -- the first version of this line called 0.1799 "better than" 0.2202.
    shrink_only = (abs(lo["net_atr"] - d["net_atr"].mean()) < 0.02
                   and lo["net_total"] < all_total)
    low_worse = lo["net_atr"] < d["net_atr"].mean()
    print("\n=== 检验三:低波动档是「选出赢家」还是「把所有结果按比例缩小」===")
    print(f"  低波动档每笔净 {lo['net_mean']*100:+.4f}%  vs 全池 {d['net'].mean()*100:+.4f}%")
    print(f"  低波动档以ATR计 {lo['net_atr']:+.4f}  vs 全池 {d['net_atr'].mean():+.4f}"
          f"   ← 若两者接近,说明只是缩小了尺度")
    print(f"  低波动档合计净 {lo['net_total']:+.3f}  vs 全池 {all_total:+.3f}"
          f"   ← 选择若有效,合计不该同比例掉")

    if shrink_only:
        verdict = (f"atr_pct 是尺度不是 alpha:换成 ATR 单位后 rho 从 "
                   f"{rows[0]['rho_pct']:+.3f} 变成 {rows[0]['rho_atr']:+.3f},"
                   f"低波动档每单位风险的收益({lo['net_atr']:+.4f})与全池"
                   f"({d['net_atr'].mean():+.4f})基本相同 —— 它只是把所有结果"
                   f"按比例缩小,并没有把赢家挑出来。这解释了为什么 18/28 个特征"
                   f"「显著」而模型 AUC 仍是 0.496。")
    elif low_worse:
        verdict = (f"方向与「低波动更好」相反:换成 ATR 单位后 rho 从 "
                   f"{rows[0]['rho_pct']:+.3f} 翻成 {rows[0]['rho_atr']:+.3f},"
                   f"低波动档每单位风险收益 {lo['net_atr']:+.4f} 低于全池 "
                   f"{d['net_atr'].mean():+.4f},高波动档 {hi['net_atr']:+.4f} 更高。"
                   f"边在「幅度」不在「胜率」——五档胜率 "
                   f"{lo['win']*100:.1f}%~{hi['win']*100:.1f}% 几乎持平,"
                   f"而每笔净差 {hi['net_mean']/max(lo['net_mean'],1e-9):.1f} 倍。"
                   f"二分类器学的是胜率,那一维没有信息,AUC≈0.5 是必然的。")
    else:
        verdict = (f"低波动档以 ATR 计 {lo['net_atr']:+.4f} 高于全池 "
                   f"{d['net_atr'].mean():+.4f} → 存在真实选择力")
    print(f"\n判读: {verdict}")
    print("注:训练池样本内,未碰 holdout;障碍参数属 owner 决策,本脚本只测不改。")

    (PROJECT / "analysis" / "output" / "diag_atr_is_scale_not_alpha.json").write_text(
        json.dumps({"pool": POOL.name, "n": len(d),
                    "unit_test": rows, "buckets": buckets,
                    "pool_net_mean": float(d["net"].mean()),
                    "pool_net_atr": float(d["net_atr"].mean()),
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

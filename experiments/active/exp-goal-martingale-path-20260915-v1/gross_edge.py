"""Does a directional edge exist at all, before fees?

If mean gross R is indistinguishable from zero, no staking scheme, no cost
reduction and no position sizing can make the strategy profitable. That test
comes before any martingale design. Long-history cells only (5m is excluded
because its ledger covers 2025-12..2026-04, not the long window).

Source: frozen ledgers from exp-spike-v8-ict-multitf-20260915-v1 (read-only).
Entry sizing columns are signal-bar-or-earlier; gross_r/net_r are outcomes.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("/Users/zhangzc/fable-trading")
SRC = BASE / "experiments/active/exp-spike-v8-ict-multitf-20260915-v1/results"
OUT = BASE / "experiments/active/exp-goal-martingale-path-20260915-v1/results"

RNG = np.random.default_rng(20260915)
LONG_TFS = ["3m", "15m", "30m", "60m", "240m"]   # 5m ledger is short-window, excluded


def boot(vals, months, n=20000):
    uniq = np.unique(months)
    if len(uniq) < 2:
        return float(vals.mean()), np.nan, np.nan, np.nan
    groups = [vals[months == m] for m in uniq]
    k = len(groups)
    means = np.empty(n)
    for b in range(n):
        means[b] = np.concatenate([groups[i] for i in RNG.integers(0, k, k)]).mean()
    # one-sided bootstrap p for H0: mean <= 0
    p = float((means <= 0).mean())
    return float(vals.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)), p


rows = []
for tf in LONG_TFS:
    for sess, zh in (("all", "全天"), ("union", "ICT时段")):
        f = SRC / f"{tf}_available_original_{sess}_trades.csv.gz"
        d = pd.read_csv(f)
        d = d[~d["censored"].astype(bool)]
        months = pd.to_datetime(d["entry_time"], utc=True).dt.strftime("%Y-%m").to_numpy()
        g_mean, g_lo, g_hi, g_p = boot(d["gross_r"].to_numpy(), months)
        n_mean, n_lo, n_hi, n_p = boot(d["net_r"].to_numpy(), months)
        rows.append({
            "周期": tf, "时段": zh, "笔数": len(d),
            "均值毛R": g_mean, "毛R_95%下界": g_lo, "毛R_95%上界": g_hi, "毛R_p(单侧)": g_p,
            "均值净R": n_mean, "净R_95%下界": n_lo, "净R_95%上界": n_hi, "净R_p(单侧)": n_p,
            "毛edge成立": bool(g_lo > 0),
        })

out = pd.DataFrame(rows)
out.to_csv(OUT / "gross_edge.csv", index=False)
pd.set_option("display.width", 220)
print(out.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
print()
print("毛R 95%下界>0 的格子:", out[out["毛edge成立"]]["周期"].tolist() or "无")

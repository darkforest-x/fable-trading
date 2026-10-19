"""Round 2b: is "add after losses" (the martingale thesis) actually supported?

The round-2 gate table showed the LOW tercile of recent strategy performance
carrying a higher forward net R than the HIGH tercile. That is precisely the
mean-reversion assumption a martingale rests on, so it gets a direct test
instead of being read off the complement of another test.

Multiple comparisons: round 2 already tested 5 high-tercile gates. Testing the
5 matching low-tercile gates makes a family of 10, and Holm is applied over all
10 p-values jointly, not over the 5 new ones alone.

Causality and the development window are unchanged from regime_gate.py:
features use only information timestamped before the trade's own
signal_close_time, and entries >= 2026-05-04 are excluded. holdout reads: 0.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("/Users/zhangzc/fable-trading")
SRC = BASE / "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/covered_trade_ledger.csv.gz"
OUT = BASE / "experiments/active/exp-goal-martingale-path-20260915-v1/results"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
RNG = np.random.default_rng(20260915)
N_BOOT = 20000
GATES = ["breadth_3d", "breadth_7d", "breadth_14d", "heat_30", "heat_100"]


def boot(vals, months, n=N_BOOT):
    uniq = np.unique(months)
    if len(uniq) < 2 or len(vals) == 0:
        return (float(np.mean(vals)) if len(vals) else np.nan, np.nan, np.nan, np.nan)
    groups = [vals[months == m] for m in uniq]
    k = len(groups)
    means = np.empty(n)
    for b in range(n):
        means[b] = np.concatenate([groups[i] for i in RNG.integers(0, k, k)]).mean()
    return (float(vals.mean()), float(np.percentile(means, 2.5)),
            float(np.percentile(means, 97.5)), float((means <= 0).mean()))


def holm(p):
    p = np.asarray(p, float)
    adj = np.full_like(p, np.nan)
    idx = np.where(~np.isnan(p))[0]
    order = idx[np.argsort(p[idx])]
    m, run = len(order), 0.0
    for rank, i in enumerate(order):
        run = max(run, (m - rank) * p[i])
        adj[i] = min(1.0, run)
    return adj


d = pd.read_csv(SRC)
d = d[~d["censored"].astype(bool)]
d = d[d["risk_fraction_at_entry"] > 0].copy()
for c, n in (("signal_close_time", "sig_t"), ("exit_time", "exit_t"), ("entry_time", "entry_t")):
    d[n] = pd.to_datetime(d[c], utc=True, format="mixed")
d = d.sort_values("sig_t").reset_index(drop=True)

sig_t = d["sig_t"].dt.tz_localize(None).to_numpy()
exit_t = d["exit_t"].dt.tz_localize(None).to_numpy()
sym, netr = d["symbol"].to_numpy(), d["net_r"].to_numpy()

for days in (3, 7, 14):
    lo_b = sig_t - np.timedelta64(days, "D")
    d[f"breadth_{days}d"] = [len(np.unique(sym[(sig_t >= lo_b[i]) & (sig_t < sig_t[i])]))
                             for i in range(len(d))]
o = np.argsort(exit_t)
es, ns_ = exit_t[o], netr[o]
for K in (30, 100):
    d[f"heat_{K}"] = [ns_[j - K:j].mean() if (j := np.searchsorted(es, sig_t[i], "left")) >= K
                      else np.nan for i in range(len(d))]

dev = d[d["entry_t"] < HOLDOUT_START].copy()
dev["_m"] = dev["entry_t"].dt.strftime("%Y-%m")

rows = []
for g in GATES:
    sub = dev[dev[g].notna()]
    thr = sub[g].quantile(1 / 3)          # BOTTOM tercile
    low = sub[sub[g] <= thr]
    m, l, h, p = boot(low["net_r"].to_numpy(), low["_m"].to_numpy())
    ex = low[low["_m"] != "2026-04"]
    me, le, he, pe = boot(ex["net_r"].to_numpy(), ex["_m"].to_numpy())
    rows.append({"门(下1/3)": g, "笔数": len(low), "净R": m, "CI下界": l, "CI上界": h, "p": p,
                 "去2026-04笔数": len(ex), "去2026-04净R": me,
                 "去2026-04CI下界": le, "去2026-04p": pe})

low_df = pd.DataFrame(rows)

# Holm over the joint family of 10 (5 high-tercile p's from round 2 + 5 here)
prev = pd.read_csv(OUT / "regime_gate.csv")
joint_p = np.concatenate([prev.loc[prev["状态"] == "已检验", "通过p"].to_numpy(),
                          low_df["p"].to_numpy()])
joint_adj = holm(joint_p)
low_df["Holm_p(族=10)"] = joint_adj[5:]

low_df.to_csv(OUT / "mean_reversion.csv", index=False)
pd.set_option("display.width", 260)
print("=== 亏后加码假设：近期表现最差 1/3 之后的前向净R（开发期）===")
print(low_df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
print()
surv = low_df[(low_df["Holm_p(族=10)"] < 0.05) & (low_df["去2026-04p"] < 0.05)]
print("同时满足 Holm<0.05 且去掉2026-04后仍显著的门:",
      surv["门(下1/3)"].tolist() if len(surv) else "无")

json.dump({"family_size": 10, "holdout_reads": 0, "n_dev": len(dev),
           "survivors": surv["门(下1/3)"].tolist()},
          open(OUT / "mean_reversion_meta.json", "w"), indent=2, ensure_ascii=False)

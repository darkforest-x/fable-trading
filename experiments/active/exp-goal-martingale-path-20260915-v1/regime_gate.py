"""Round 2: can the profitable regime be identified BEFORE entry?

Round 1 showed the all-markets V1 pool's entire profit sits in 2 of 25 months.
If that regime is only visible after the fact, no gate and no staking scheme can
exploit it. This round builds strictly-causal market-state features and tests
whether conditioning on them lifts mean net R.

CAUSALITY RULE used throughout: a feature for trade t may only use information
timestamped strictly BEFORE t's own signal_close_time.
  - breadth_Nd : distinct symbols whose signal_close_time falls in
                 [t.signal_close_time - N days, t.signal_close_time)
  - heat_K     : mean net_r of the K most recent trades whose exit_time is
                 strictly < t.signal_close_time  (closed, therefore observable)
Neither peeks at t's own outcome or at any concurrently-open trade.

DEVELOPMENT WINDOW: entries strictly before 2026-05-04 (the holdout boundary).
Later entries are loaded only to be counted and excluded; they are never scored.

Source: exp-spike-v1-twoyear-allmarkets-20260911-v1 (read-only). holdout reads: 0.
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
d["sig_t"] = pd.to_datetime(d["signal_close_time"], utc=True, format="mixed")
d["exit_t"] = pd.to_datetime(d["exit_time"], utc=True, format="mixed")
d["entry_t"] = pd.to_datetime(d["entry_time"], utc=True, format="mixed")
d = d.sort_values("sig_t").reset_index(drop=True)

n_all = len(d)
n_holdout = int((d["entry_t"] >= HOLDOUT_START).sum())

# ---- causal features computed on the FULL timeline (a feature may look back
# across the boundary; it just may never look forward) --------------------
# tz-aware -> naive datetime64[ns] so numpy timedelta arithmetic works
sig_t = d["sig_t"].dt.tz_localize(None).to_numpy()
exit_t = d["exit_t"].dt.tz_localize(None).to_numpy()
sym = d["symbol"].to_numpy()
netr = d["net_r"].to_numpy()

for days in (3, 7, 14):
    lo = sig_t - np.timedelta64(days, "D")
    breadth = np.empty(len(d), dtype=int)
    for i in range(len(d)):
        m = (sig_t >= lo[i]) & (sig_t < sig_t[i])
        breadth[i] = len(np.unique(sym[m]))
    d[f"breadth_{days}d"] = breadth

order_exit = np.argsort(exit_t)
exit_sorted = exit_t[order_exit]
netr_by_exit = netr[order_exit]
for K in (30, 100):
    heat = np.full(len(d), np.nan)
    for i in range(len(d)):
        j = np.searchsorted(exit_sorted, sig_t[i], side="left")  # exits strictly before
        if j >= K:
            heat[i] = netr_by_exit[j - K:j].mean()
    d[f"heat_{K}"] = heat

dev = d[d["entry_t"] < HOLDOUT_START].copy()
dev["_m"] = dev["entry_t"].dt.strftime("%Y-%m")
print(f"账本 {n_all} 笔 | holdout(≥2026-05-04) {n_holdout} 笔已排除 | 开发期 {len(dev)} 笔")
print(f"开发期 {dev['_m'].min()} -> {dev['_m'].max()}")
b, blo, bhi, bp = boot(dev["net_r"].to_numpy(), dev["_m"].to_numpy())
print(f"开发期基线 净R {b:+.4f}  CI[{blo:+.4f},{bhi:+.4f}]  p={bp:.4f}\n")

# ---- gate tests: top-tercile of each causal feature ----------------------
GATES = ["breadth_3d", "breadth_7d", "breadth_14d", "heat_30", "heat_100"]
rows = []
for g in GATES:
    v = dev[g]
    ok = v.notna()
    sub = dev[ok]
    if len(sub) < 60:
        rows.append({"门": g, "状态": "样本不足", "笔数": len(sub)}); continue
    thr = sub[g].quantile(2 / 3)
    hi = sub[sub[g] >= thr]
    lo_ = sub[sub[g] < thr]
    m_hi, lo_hi, hi_hi, p_hi = boot(hi["net_r"].to_numpy(), hi["_m"].to_numpy())
    m_lo = float(lo_["net_r"].mean())
    rows.append({"门": g, "状态": "已检验", "阈值(上1/3)": float(thr),
                 "通过笔数": len(hi), "通过净R": m_hi, "通过CI下界": lo_hi,
                 "通过CI上界": hi_hi, "通过p": p_hi,
                 "未通过笔数": len(lo_), "未通过净R": m_lo, "差值": m_hi - m_lo})

gd = pd.DataFrame(rows)
tested = gd["状态"] == "已检验"
gd.loc[tested, "Holm_p"] = holm(gd.loc[tested, "通过p"].to_numpy())
gd.to_csv(OUT / "regime_gate.csv", index=False)
pd.set_option("display.width", 240)
print("=== 事前市场状态门（开发期，Holm 校正）===")
print(gd.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

# ---- how much of the gate's edge is just 2026-04? -----------------------
print("\n=== 去掉 2026-04 后，同样的门还剩什么 ===")
rows2 = []
for g in GATES:
    sub = dev[dev[g].notna()]
    if len(sub) < 60:
        continue
    thr = sub[g].quantile(2 / 3)
    hi = sub[(sub[g] >= thr) & (sub["_m"] != "2026-04")]
    if len(hi) < 30:
        continue
    m, l, h, p = boot(hi["net_r"].to_numpy(), hi["_m"].to_numpy())
    rows2.append({"门": g, "笔数": len(hi), "净R": m, "CI下界": l, "CI上界": h, "p": p})
g2 = pd.DataFrame(rows2)
if len(g2):
    g2["Holm_p"] = holm(g2["p"].to_numpy())
    g2.to_csv(OUT / "regime_gate_ex202604.csv", index=False)
    print(g2.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

json.dump({"n_all": n_all, "n_holdout_excluded": n_holdout, "n_dev": len(dev),
           "holdout_start": str(HOLDOUT_START), "holdout_reads": 0,
           "gates_tested": GATES, "n_boot": N_BOOT},
          open(OUT / "regime_gate_meta.json", "w"), indent=2, ensure_ascii=False)

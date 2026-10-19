"""Round 3: repo-wide census — does ANY frozen configuration carry positive net expectancy?

Rounds 1-2 tested ETH x V8 and the all-markets V1 pool and found no usable edge.
This round asks the question across everything the repo has already produced.

SELECTION RULE, fixed before looking at any result (this matters: the repo holds
~50k ledger files across ~110 experiments, so picking ledgers after seeing their
numbers would guarantee false positives):
  - one ledger per experiment directory
  - = the LARGEST file whose name matches trade/ledger/fill and which exposes both
    a per-trade net R column and an entry timestamp
  - >= 100 non-censored rows, entries strictly before 2026-05-04

TWO-STAGE DESIGN, also fixed in advance:
  stage 1 screen  : 5000-draw month-block bootstrap over every eligible cell
  stage 2 confirm : 20000 draws re-run on stage-1 survivors only
  Holm is applied over the FULL stage-1 family, not over the survivors.

holdout reads: 0.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("/Users/zhangzc/fable-trading")
ACTIVE = BASE / "experiments/active"
OUT = BASE / "experiments/active/exp-goal-martingale-path-20260915-v1/results"
SELF = "exp-goal-martingale-path-20260915-v1"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
MIN_N = 100
RNG = np.random.default_rng(20260915)

NAME_RE = re.compile(r"(trade|ledger|fill)", re.I)
SKIP_RE = re.compile(r"(control|paired|manifest|receipt|summary|threshold|profile|"
                     r"streak|opportunit|search|coverage|link|config)", re.I)
NETR_COLS = ["net_r", "net_R"]
TIME_COLS = ["entry_time", "entry_t", "signal_close_time", "signal_bar_open"]


def boot(vals, months, n):
    uniq = np.unique(months)
    if len(uniq) < 2:
        return float(vals.mean()), np.nan, np.nan, np.nan
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


# ---- pick one ledger per experiment by the fixed rule --------------------
picks = {}
n_files_seen = 0
for exp in sorted(ACTIVE.iterdir()):
    if not exp.is_dir() or exp.name == SELF:
        continue
    cands = []
    for f in exp.rglob("*"):
        if not f.is_file() or f.suffix not in (".gz", ".csv"):
            continue
        if not NAME_RE.search(f.name) or SKIP_RE.search(f.name):
            continue
        n_files_seen += 1
        cands.append(f)
    if cands:
        picks[exp.name] = max(cands, key=lambda p: p.stat().st_size)

print(f"扫描 {n_files_seen} 个候选账本文件，{len(picks)} 个实验各选 1 个（最大者）", flush=True)

rows = []
skipped = {"no_netr": 0, "no_time": 0, "too_few": 0, "read_error": 0}
for name, f in picks.items():
    try:
        d = pd.read_csv(f, low_memory=False)
    except Exception:
        skipped["read_error"] += 1
        continue
    nc = next((c for c in NETR_COLS if c in d.columns), None)
    tc = next((c for c in TIME_COLS if c in d.columns), None)
    if nc is None:
        skipped["no_netr"] += 1
        continue
    if tc is None:
        skipped["no_time"] += 1
        continue
    if "censored" in d.columns:
        d = d[~d["censored"].astype(bool)]
    d = d[d[nc].notna()]
    t = pd.to_datetime(d[tc], utc=True, format="mixed", errors="coerce")
    d = d[t.notna()]
    t = t[t.notna()]
    d = d[t < HOLDOUT_START]
    t = t[t < HOLDOUT_START]
    if len(d) < MIN_N:
        skipped["too_few"] += 1
        continue
    months = t.dt.strftime("%Y-%m").to_numpy()
    m, lo, hi, p = boot(d[nc].to_numpy(dtype=float), months, 5000)
    rows.append({"实验": name, "账本": f.name, "笔数": len(d),
                 "月数": len(np.unique(months)),
                 "均值净R": m, "CI下界": lo, "CI上界": hi, "p": p})

cen = pd.DataFrame(rows)
cen["Holm_p"] = holm(cen["p"].to_numpy())
cen = cen.sort_values("均值净R", ascending=False)
cen.to_csv(OUT / "census_stage1.csv", index=False)

print(f"可检验格子 {len(cen)} 个；跳过 {skipped}", flush=True)
print(f"\n均值净R > 0 的格子: {int((cen['均值净R'] > 0).sum())} / {len(cen)}")
print(f"CI 下界 > 0 的格子: {int((cen['CI下界'] > 0).sum())}")
print(f"Holm 校正后 p < 0.05 的格子: {int((cen['Holm_p'] < 0.05).sum())}")
print("\n=== 净R 最高的 15 个格子 ===")
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 42)
print(cen.head(15).to_string(index=False, float_format=lambda x: f"{x:.4f}"))

surv = cen[(cen["Holm_p"] < 0.05) & (cen["均值净R"] > 0)]
print(f"\n=== stage-1 幸存者: {len(surv)} ===")
if len(surv):
    print(surv.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

json.dump({"files_scanned": n_files_seen, "experiments_with_ledgers": len(picks),
           "cells_tested": len(cen), "skipped": skipped,
           "positive_mean": int((cen["均值净R"] > 0).sum()),
           "ci_lower_above_zero": int((cen["CI下界"] > 0).sum()),
           "holm_survivors": surv["实验"].tolist(),
           "min_n": MIN_N, "stage1_boot": 5000, "holdout_reads": 0},
          open(OUT / "census_meta.json", "w"), indent=2, ensure_ascii=False)

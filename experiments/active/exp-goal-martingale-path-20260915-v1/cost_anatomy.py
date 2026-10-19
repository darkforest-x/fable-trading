"""Cost anatomy across timeframes for the martingale-path goal study.

Source: frozen ledgers from exp-spike-v8-ict-multitf-20260915-v1 (read-only).
No new backtest, no price reads, no holdout access.

Why: every negative martingale result so far has been reported in net R, but the
per-trade fee burden itself was never broken out. cost_r in those ledgers is the
0.2% round-trip expressed in units of the trade's own initial price risk, i.e.
cost_r ~= 0.002 / initial_risk_frac. Tight stops make cost_r large, so a stopped
trade can lose far more than 1R. This script quantifies that per timeframe.

Columns used: gross_r, net_r, cost_r, initial_risk_frac, entry_time, exit_reason.
All are signal-bar-or-earlier for entry sizing; net_r is the realised outcome.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("/Users/zhangzc/fable-trading")
SRC = BASE / "experiments/active/exp-spike-v8-ict-multitf-20260915-v1/results"
OUT = BASE / "experiments/active/exp-goal-martingale-path-20260915-v1/results"
OUT.mkdir(parents=True, exist_ok=True)

TFS = ["3m", "5m", "15m", "30m", "60m", "240m"]
WINDOWS = ["available", "common"]   # available = 2023-08..2026-05, common = 2026-01..05
SESSIONS = {"all": "全天", "union": "ICT时段"}

# Sensitivity multipliers on the project's 0.2% round-trip assumption.
# NOT a change of assumption: 0.2% stays the baseline (m=1.0). Changing the
# project cost assumption requires owner approval (CLAUDE.md escalation rule).
COST_MULT = {"0.20%(基准)": 1.0, "0.10%": 0.5, "0.05%": 0.25}

RNG = np.random.default_rng(20260915)


def month_block_bootstrap(df, value_col, n_boot=10000):
    """Month-block bootstrap of the mean, resampling calendar months with
    replacement so serial dependence inside a month is preserved."""
    if len(df) == 0:
        return (np.nan, np.nan, np.nan)
    months = df["_month"].to_numpy()
    vals = df[value_col].to_numpy()
    uniq = np.unique(months)
    if len(uniq) < 2:
        return (float(vals.mean()), np.nan, np.nan)
    by_month = [vals[months == m] for m in uniq]
    means = np.empty(n_boot)
    k = len(by_month)
    for b in range(n_boot):
        pick = RNG.integers(0, k, size=k)
        means[b] = np.concatenate([by_month[i] for i in pick]).mean()
    return (float(vals.mean()), float(np.percentile(means, 2.5)),
            float(np.percentile(means, 97.5)))


rows = []
for tf in TFS:
    for win in WINDOWS:
        for sess in SESSIONS:
            f = SRC / f"{tf}_{win}_original_{sess}_trades.csv.gz"
            if not f.exists():
                continue
            d = pd.read_csv(f)
            d = d[~d["censored"].astype(bool)].copy()
            if len(d) == 0:
                continue
            d["_month"] = pd.to_datetime(d["entry_time"], utc=True).dt.to_period("M").astype(str)
            rec = {
                "周期": tf, "窗口": win, "时段": SESSIONS[sess], "笔数": len(d),
                "净胜率": float((d["net_r"] > 0).mean()),
                "毛胜率": float((d["gross_r"] > 0).mean()),
                "中位止损距离%": float(d["initial_risk_frac"].median() * 100),
                "中位cost_R": float(d["cost_r"].median()),
                "均值cost_R": float(d["cost_r"].mean()),
                "cost_R>0.5占比": float((d["cost_r"] > 0.5).mean()),
                "cost_R>1.0占比": float((d["cost_r"] > 1.0).mean()),
                "均值毛R": float(d["gross_r"].mean()),
            }
            for label, m in COST_MULT.items():
                alt = d["gross_r"] - d["cost_r"] * m
                d["_alt"] = alt
                mean, lo, hi = month_block_bootstrap(d, "_alt")
                rec[f"均值净R@{label}"] = mean
                rec[f"净R95%下界@{label}"] = lo
                rec[f"净R95%上界@{label}"] = hi
            rows.append(rec)

out = pd.DataFrame(rows)
out.to_csv(OUT / "cost_anatomy.csv", index=False)

meta = {
    "source_dir": str(SRC),
    "cost_baseline": "0.2% round trip (project assumption, unchanged)",
    "sensitivity_note": "0.10%/0.05% columns are sensitivity only; changing the "
                        "project cost assumption requires owner approval",
    "holdout_reads": 0,
    "bootstrap": "month-block, 10000 draws, seed 20260915",
    "censored_excluded": True,
}
(OUT / "cost_anatomy_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

pd.set_option("display.width", 250)
print(out[["周期", "窗口", "时段", "笔数", "净胜率", "中位止损距离%", "中位cost_R",
           "cost_R>0.5占比", "均值毛R", "均值净R@0.20%(基准)",
           "净R95%下界@0.20%(基准)", "净R95%上界@0.20%(基准)"]].to_string(index=False))

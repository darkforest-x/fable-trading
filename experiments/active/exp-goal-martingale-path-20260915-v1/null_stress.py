"""Round 4: does "martingale is worst" survive when the edge is removed?

Round 3 compared staking schemes on the repo's best cell (mean net R +0.168),
but that number is not significant (p=0.190, CI contains 0). If the ranking only
holds because of an unproven edge, it is worthless. So the single variable
changed here is the EXPECTATION LEVEL, with the distribution shape held fixed by
a uniform shift:

  null      : shifted so mean net R == 0 exactly      (no edge)
  observed  : unshifted, mean +0.168                  (round 3's assumption)
  realistic : shifted to mean -0.620                  (the ETH 3m figure measured
                                                       in round 1, 1453 trades)

Second decomposition, also one variable: WHERE the sequence comes from.
  month-block : whole calendar months resampled -> loss clustering preserved
  iid         : individual trades resampled      -> clustering destroyed
The gap between them is how much of each scheme's outcome is caused by
clustering rather than by the ladder itself.

holdout reads: 0.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("/Users/zhangzc/fable-trading")
OUT = BASE / "experiments/active/exp-goal-martingale-path-20260915-v1/results"
SRC = BASE / "experiments/active/exp-spike-v1-plus-backtest-20260912-v1/post_full_v2/trades.csv.gz"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
RNG = np.random.default_rng(20260915)
N_PATHS, N_TRADES, START = 20000, 300, 1000.0


def load():
    d = pd.read_csv(SRC, low_memory=False)
    if "censored" in d.columns:
        d = d[~d["censored"].astype(bool)]
    t = pd.to_datetime(d["entry_time"], utc=True, format="mixed", errors="coerce")
    k = t.notna() & (t < HOLDOUT_START) & d["net_r"].notna()
    return d.loc[k, "net_r"].to_numpy(float), t[k].dt.strftime("%Y-%m").to_numpy()


def paths_monthblock(vals, months):
    uniq = np.unique(months)
    groups = [vals[months == m] for m in uniq]
    k = len(groups)
    out = np.empty((N_PATHS, N_TRADES))
    for p in range(N_PATHS):
        buf, tot = [], 0
        while tot < N_TRADES:
            g = groups[RNG.integers(0, k)]
            buf.append(g); tot += len(g)
        out[p] = np.concatenate(buf)[:N_TRADES]
    return out


def paths_iid(vals):
    return vals[RNG.integers(0, len(vals), size=(N_PATHS, N_TRADES))]


def run(paths, scheme, base_frac, max_level):
    eq = np.full(paths.shape[0], START)
    level = np.zeros(paths.shape[0], dtype=int)
    alive = np.ones(paths.shape[0], dtype=bool)
    base = START * base_frac
    max_dd = np.zeros(paths.shape[0])
    peak = np.full(paths.shape[0], START)
    for j in range(paths.shape[1]):
        stake = base * (2.0 ** level)
        alive &= ~(alive & (stake > eq))
        if not alive.any():
            break
        r = paths[:, j]
        eq = eq + np.where(alive, stake * r, 0.0)
        dead = alive & (eq <= 0)
        eq[dead] = 0.0
        alive &= ~dead
        peak = np.maximum(peak, eq)
        max_dd = np.maximum(max_dd, (peak - eq) / peak)
        win = r > 0
        if scheme == "martingale":
            level = np.where(alive & ~win, np.minimum(level + 1, max_level),
                             np.where(alive & win, 0, level))
        elif scheme == "anti":
            level = np.where(alive & win, np.minimum(level + 1, max_level),
                             np.where(alive & ~win, 0, level))
    return eq, alive, max_dd


vals, months = load()
LEVELS = {"null(期望=0)": -vals.mean(),
          "observed(+0.168)": 0.0,
          "realistic(-0.620)": -0.620 - vals.mean()}

rows = []
for lvl_name, shift in LEVELS.items():
    v = vals + shift
    for src_name, P in (("month-block(保留聚集)", paths_monthblock(v, months)),
                        ("iid(打散聚集)", paths_iid(v))):
        for scheme, ladders in (("fixed", [0]), ("martingale", [4]), ("anti", [4])):
            for ml in ladders:
                eq, alive, dd = run(P, scheme, 0.01, ml)
                rows.append({"期望档": lvl_name, "序列": src_name, "方案": scheme,
                             "破产率": 1 - alive.mean(),
                             "中位期末U": float(np.median(eq)),
                             "P(>1000)": float((eq > START).mean()),
                             "中位最大回撤": float(np.median(dd))})

res = pd.DataFrame(rows)
res.to_csv(OUT / "null_stress.csv", index=False)
pd.set_option("display.width", 240)
print(f"源: {len(vals)} 笔 / {len(np.unique(months))} 月 | 底注 1% | {N_TRADES} 笔 | {N_PATHS} 路径\n")
for lvl in LEVELS:
    print(f"=== {lvl} ===")
    print(res[res["期望档"] == lvl].drop(columns=["期望档"])
          .to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print()

print("=== 排序是否稳定（破产率，越低越好）===")
piv = res.pivot_table(index=["期望档", "序列"], columns="方案", values="破产率")
piv["倍投最差?"] = (piv["martingale"] >= piv[["fixed", "anti"]].max(axis=1))
print(piv.to_string(float_format=lambda x: f"{x:.4f}"))

json.dump({"levels": {k: float(v) for k, v in LEVELS.items()},
           "base_frac": 0.01, "max_level": 4, "n_paths": N_PATHS,
           "path_len": N_TRADES, "holdout_reads": 0,
           "martingale_worst_in_all_cells": bool(piv["倍投最差?"].all())},
          open(OUT / "null_stress_meta.json", "w"), indent=2, ensure_ascii=False)

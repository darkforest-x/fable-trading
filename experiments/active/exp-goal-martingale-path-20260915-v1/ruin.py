"""Round 3b: ruin probability for martingale vs fixed vs anti-martingale.

Rounds 1-3 found no configuration with demonstrable positive net expectancy.
This turns that into an actionable number: given the BEST cell the repo has
produced (highest mean net R in the census), what happens to 1000 USDT under
each staking scheme, and what per-trade edge would a martingale actually need?

Sequences are month-block bootstrapped so that loss clustering - the property
round 2 measured directly (heat_30 bottom tercile -> forward net R -0.338) - is
preserved. Resampling individual trades would destroy it and flatter martingale.

Ladders (risk as a fraction of the STARTING bankroll, not of current equity):
  fixed      : base every trade
  martingale : base * 2**level, level+1 after a net loss, reset to 0 on a net win,
               capped at max_level (a capped loss is realised, never erased)
  anti       : base * 2**level, level+1 after a net WIN, reset to 0 on a loss

Ruin := equity <= 0, or the next required stake exceeds remaining equity.
holdout reads: 0.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("/Users/zhangzc/fable-trading")
OUT = BASE / "experiments/active/exp-goal-martingale-path-20260915-v1/results"
RNG = np.random.default_rng(20260915)

BEST = BASE / "experiments/active/exp-spike-v1-plus-backtest-20260912-v1/post_full_v2/trades.csv.gz"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
N_PATHS, N_TRADES = 20000, 300
START = 1000.0


def load_best():
    d = pd.read_csv(BEST, low_memory=False)
    if "censored" in d.columns:
        d = d[~d["censored"].astype(bool)]
    t = pd.to_datetime(d["entry_time"], utc=True, format="mixed", errors="coerce")
    keep = t.notna() & (t < HOLDOUT_START) & d["net_r"].notna()
    d, t = d[keep], t[keep]
    return d["net_r"].to_numpy(float), t.dt.strftime("%Y-%m").to_numpy()


def sample_paths(vals, months, n_paths, n_trades):
    """Month-block bootstrap: draw whole months and concatenate until長 enough."""
    uniq = np.unique(months)
    groups = [vals[months == m] for m in uniq]
    k = len(groups)
    out = np.empty((n_paths, n_trades))
    for p in range(n_paths):
        buf = []
        while sum(len(b) for b in buf) < n_trades:
            buf.append(groups[RNG.integers(0, k)])
        out[p] = np.concatenate(buf)[:n_trades]
    return out


def run(paths, scheme, base_frac, max_level):
    n_paths, n_trades = paths.shape
    eq = np.full(n_paths, START)
    level = np.zeros(n_paths, dtype=int)
    alive = np.ones(n_paths, dtype=bool)
    base = START * base_frac
    for j in range(n_trades):
        stake = base * (2.0 ** level)
        broke = alive & (stake > eq)
        alive &= ~broke
        if not alive.any():
            break
        r = paths[:, j]
        pnl = np.where(alive, stake * r, 0.0)
        eq = eq + pnl
        dead = alive & (eq <= 0)
        eq[dead] = 0.0
        alive &= ~dead
        win = r > 0
        if scheme == "fixed":
            pass
        elif scheme == "martingale":
            level = np.where(alive & ~win, np.minimum(level + 1, max_level), np.where(alive & win, 0, level))
        elif scheme == "anti":
            level = np.where(alive & win, np.minimum(level + 1, max_level), np.where(alive & ~win, 0, level))
    return eq, alive


vals, months = load_best()
print(f"最优格子: exp-spike-v1-plus-backtest  {len(vals)} 笔  {len(np.unique(months))} 个月")
print(f"均值净R {vals.mean():+.4f}  中位 {np.median(vals):+.4f}  胜率 {(vals>0).mean():.1%}\n")

paths = sample_paths(vals, months, N_PATHS, N_TRADES)
rows = []
for scheme in ("fixed", "martingale", "anti"):
    for base_frac in (0.01, 0.02, 0.05):
        for max_level in ((0,) if scheme == "fixed" else (3, 4, 5)):
            eq, alive = run(paths, scheme, base_frac, max_level)
            ruin = 1.0 - alive.mean()
            rows.append({"方案": scheme, "底注%": base_frac * 100, "最大层": max_level,
                         "破产率": ruin, "中位期末U": float(np.median(eq)),
                         "P(期末>1000)": float((eq > START).mean()),
                         "P(期末>2000)": float((eq > 2 * START).mean()),
                         "P(期末>5000)": float((eq > 5 * START).mean())})
res = pd.DataFrame(rows)
res.to_csv(OUT / "ruin.csv", index=False)
pd.set_option("display.width", 220)
print(f"=== 1000U 走 {N_TRADES} 笔，{N_PATHS} 条月块 bootstrap 路径 ===")
print(res.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

# ---- what edge would a martingale need? -------------------------------
print("\n=== 倍投要多大的真实净期望才能把破产率压到 5% 以下？===")
shift_rows = []
for shift in (0.0, 0.1, 0.2, 0.3, 0.5, 0.8):
    sp = paths + shift            # uniform lift of per-trade net R
    eq, alive = run(sp, "martingale", 0.02, 4)
    eqf, alivef = run(sp, "fixed", 0.02, 0)
    shift_rows.append({"每笔净R加成": shift, "加成后均值净R": float(vals.mean() + shift),
                       "倍投破产率": 1 - alive.mean(), "倍投中位期末U": float(np.median(eq)),
                       "固定破产率": 1 - alivef.mean(), "固定中位期末U": float(np.median(eqf))})
sh = pd.DataFrame(shift_rows)
sh.to_csv(OUT / "ruin_edge_requirement.csv", index=False)
print(sh.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
print(f"\n参考：普查里最优格子的净R 95%CI 上界 = +0.5195，即使真实 edge 落在 CI 最乐观端，")
print(f"      对应的加成也只有 +{0.5195 - vals.mean():.3f}。")

json.dump({"best_cell": "exp-spike-v1-plus-backtest-20260912-v1",
           "n_trades_source": len(vals), "n_months": int(len(np.unique(months))),
           "mean_net_r": float(vals.mean()), "n_paths": N_PATHS,
           "path_length": N_TRADES, "holdout_reads": 0},
          open(OUT / "ruin_meta.json", "w"), indent=2, ensure_ascii=False)

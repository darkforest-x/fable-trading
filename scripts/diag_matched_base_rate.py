"""ATR-matched control: is the top-ATR quintile's excess detector skill, or just
"high-vol bars drift down more"? Compares detector fires against random bars in
the SAME month AND the SAME atr_pct bucket, so volatility cannot leak in."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/zhangzc/fable-trading")
from src.data.loader import list_series, load_series  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import HORIZON_BARS, label_short_candidate  # noqa: E402

OUTDIR = "analysis/output/"
LO = pd.Timestamp("2025-11-04", tz="UTC")
HI = pd.Timestamp("2026-05-04", tz="UTC")
RNG = np.random.default_rng(7)

pool = pd.read_csv("/Users/zhangzc/fable-trading/data/judgment_yolo_owner_side_short_100_6m.csv")
pool["t"] = pd.to_datetime(pool["signal_time"], utc=True)
symbols = set(pool["symbol"].unique())

# Rebuild the random baseline, this time recording atr_pct so it can be matched.
series = {}
for (src, sym), paths in list_series().items():
    if sym in symbols:
        series.setdefault(sym, paths)

rows = []
for k, sym in enumerate(sorted(symbols), 1):
    if sym not in series:
        continue
    enr = add_indicators(load_series(series[sym]))
    ts = pd.to_datetime(enr["open_time"], utc=True)
    ok = np.where((ts >= LO) & (ts < HI))[0]
    ok = ok[ok + 1 + HORIZON_BARS < len(enr)]
    if len(ok) == 0:
        continue
    pick = RNG.choice(ok, size=min(400, len(ok)), replace=False)
    ap = enr["atr_pct"].to_numpy()
    for i in pick:
        o = label_short_candidate(enr, int(i), tp_mult=5.0, sl_mult=2.0)
        if o is None:
            continue
        rows.append({"symbol": sym, "t": ts.iloc[int(i)], "realized_ret": o.realized_ret,
                     "atr_pct": ap[int(i)]})
    if k % 25 == 0:
        print(f"  [{k}/{len(symbols)}] {len(rows)}", flush=True)

base = pd.DataFrame(rows)
base.to_csv(OUTDIR + "base_rate_random_short_atr.csv", index=False)
print(f"baseline rows={len(base)}\n")

# Shared ATR bucket edges, taken from the detector pool so buckets are comparable.
edges = np.quantile(pool.atr_pct, [0, 0.2, 0.4, 0.6, 0.8, 1.0])
edges[0], edges[-1] = -np.inf, np.inf
for d in (pool, base):
    d["m"] = d["t"].dt.strftime("%Y-%m")
    d["aq"] = pd.cut(d.atr_pct, edges, labels=False, include_lowest=True)
    d["cell"] = d["m"] + "|q" + d["aq"].astype(str)

b = base.groupby("cell")["realized_ret"].agg(rnd_n="count", rnd_m="mean")
p = pool.merge(b, left_on="cell", right_index=True, how="inner")
p = p[p.rnd_n >= 30]
p["excess"] = p.realized_ret - p.rnd_m

print("=== ATR-matched, month-matched detector excess ===")
print(f"n = {len(p)}")
m, se = p.excess.mean(), p.excess.std() / np.sqrt(len(p))
print(f"excess = {m*10000:+.2f} bp   se={se*10000:.2f}   t={m/se:+.2f}   "
      f"net after 10bp cost = {m*10000-10:+.2f} bp\n")

g = p.groupby("aq").apply(
    lambda x: pd.Series({
        "n": len(x),
        "det_bp": x.realized_ret.mean() * 10000,
        "rand_bp": x.rnd_m.mean() * 10000,
        "excess_bp": x.excess.mean() * 10000,
        "t": x.excess.mean() / (x.excess.std() / np.sqrt(len(x))),
        "net_bp": x.excess.mean() * 10000 - 10,
    }),
    include_groups=False,
)
print("--- by atr_pct quintile (random bars matched on the same quintile) ---")
print(g.round(2).to_string())

print("\n--- same, by month, top quintile only ---")
top = p[p.aq == 4]
gm = top.groupby("m").apply(
    lambda x: pd.Series({
        "n": len(x),
        "excess_bp": x.excess.mean() * 10000,
        "net_bp": x.excess.mean() * 10000 - 10,
    }),
    include_groups=False,
)
print(gm.round(2).to_string())

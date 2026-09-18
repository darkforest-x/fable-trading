"""Summary of the V11.2 management backtest (add on break / leave if no break).

Source: per-symbol outputs of `spike_v112_manage_study`. Checks that the plain V9
base reproduces the six-timeframe v9_long ledger, then reports each arm against
the plain V9 exits with a UTC event-month block bootstrap (seed 91509, 2,000).
Read-only.
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study

ORIGINAL = Path("experiments/active/exp-spike-v10-4-joint-multitf-20260918-v1/results/run_v1/streams")
ARMS = ["v9_add", "v9_exit3", "v9_exit6", "v9_exit12", "v9_exit24", "v9_exit48", "v9_exit6_add"]


def load(run: Path) -> pd.DataFrame:
    t = pd.concat([pd.read_csv(p) for p in sorted(glob.glob(str(run / "streams" / "*" / "trades.csv.gz")))],
                  ignore_index=True)
    t["signal_bar_open"] = pd.to_datetime(t.signal_bar_open, utc=True, format="mixed")
    t["exit_time"] = pd.to_datetime(t.exit_time, utc=True, format="mixed")
    t["signal_close"] = t.signal_bar_open + pd.to_timedelta(t.timeframe.map({"15m": 15, "1h": 60}), unit="min")
    t["period"] = np.where(t.signal_close < study.SPLIT, "earlier", "later")
    t["month"] = t.signal_close.dt.strftime("%Y-%m")
    return t


def drawdown(x: pd.DataFrame) -> float:
    path = x.sort_values("exit_time").net_r.cumsum().to_numpy()
    return float((np.maximum.accumulate(np.r_[0, path])[1:] - path).max()) if len(path) else 0.0


def boot(a: pd.DataFrame, c: pd.DataFrame) -> tuple[float, float]:
    months = sorted(set(a.month) | set(c.month))
    A = a.groupby("month").net_r.agg(["size", "sum"]).reindex(months, fill_value=0)
    C = c.groupby("month").net_r.agg(["size", "sum"]).reindex(months, fill_value=0)
    rng = np.random.default_rng(91509)
    vals = []
    for row in rng.integers(0, len(months), size=(2000, len(months))):
        na, nc = A["size"].values[row].sum(), C["size"].values[row].sum()
        if na and nc:
            vals.append(C["sum"].values[row].sum() / nc - A["sum"].values[row].sum() / na)
    lo, hi = np.quantile(vals, [.025, .975])
    return float(lo), float(hi)


def main(run: Path, out: Path) -> None:
    t = load(run)
    original = pd.concat([pd.read_csv(p) for p in sorted(glob.glob(str(ORIGINAL / "*" / "trades.csv.gz")))],
                         ignore_index=True)
    repro, rows, adds = [], [], []
    for tf in ("15m", "1h"):
        a = original.loc[(original.timeframe == tf) & (original.arm == "v9_long")]
        b = t.loc[(t.timeframe == tf) & (t.arm == "v9_add")]
        m = a[["symbol", "signal_i", "exit_i", "net_r"]].merge(b[["symbol", "signal_i", "exit_i", "net_r"]],
                                                              on=["symbol", "signal_i"], how="outer", indicator=True)
        both = m.loc[m._merge == "both"]
        repro.append({"timeframe": tf, "original": len(a), "rerun": len(b), "unmatched": int((m._merge != "both").sum()),
                      "exit_diff": int((both.exit_i_x != both.exit_i_y).sum()),
                      "r_diff": int((~np.isclose(both.net_r_x, both.net_r_y, equal_nan=True)).sum())})
        base = t.loc[(t.timeframe == tf) & (t.arm == "v9_add") & (t.status == "closed")]
        for arm in ARMS:
            x = t.loc[(t.timeframe == tf) & (t.arm == arm) & (t.status == "closed")]
            for period in ("full", "later"):
                xp = x if period == "full" else x.loc[x.period == "later"]
                bp = base if period == "full" else base.loc[base.period == "later"]
                lo, hi = boot(bp, xp) if arm != "v9_add" else (np.nan, np.nan)
                r = xp.net_r
                rows.append({"tf": tf, "arm": arm, "period": period, "n": len(xp), "win": r.gt(0).mean(),
                             "mean_r": r.mean(), "pf": r[r > 0].sum() / -r[r < 0].sum(), "total": r.sum(),
                             "dd": drawdown(xp), "diff": r.mean() - bp.net_r.mean(), "lo": lo, "hi": hi,
                             "no_break_exits": int((xp.exit_reason == "no_break_exit").sum())})
                if arm in ("v9_add", "v9_exit6_add"):
                    ad = xp.loc[xp.add_break_i.notna()]
                    combined = xp.net_r.fillna(0) + xp.add_net_r.fillna(0)
                    adds.append({"tf": tf, "arm": arm, "period": period, "adds": len(ad), "trades": len(xp),
                                 "add_mean_r": ad.add_net_r.mean(), "add_win": ad.add_net_r.gt(0).mean(),
                                 "add_total_r": ad.add_net_r.sum(), "add_mean_bp": ad.add_net_return.mean() * 1e4,
                                 "add_price_in_r_median": ad.add_price_in_r.median(),
                                 "combined_mean_r": combined.mean(), "base_mean_r": xp.net_r.mean()})
    out.mkdir(parents=True, exist_ok=True)
    for name, frame in (("reproduction", pd.DataFrame(repro)), ("summary", pd.DataFrame(rows)), ("adds", pd.DataFrame(adds))):
        frame.to_csv(out / f"{name}.csv", index=False)
        print(f"\n== {name} ==\n" + frame.to_string(index=False, float_format=lambda v: f"{v:.4f}"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    main(args.run, args.out)

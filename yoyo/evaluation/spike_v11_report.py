"""Statistics for the SPIKE V11 multi-timeframe backtest (15m<-1h, 1h<-4h).

Source: per-symbol outputs of `spike_v11_study` (committed runner) plus the
original V10.4 run's ledgers for the v10_4/v9_long reproduction check. Only
reads; nothing re-simulated. Per-arm metrics and the month-block difference
estimator are the ones used in the V10.4 1h audit
(`spike_v10_4_increment_report.arm_metrics` / same resampling scheme).
"""
from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation.spike_v10_4_increment_report import arm_metrics

ARMS = ("v9_long", "v10_4", "v11_htf_only", "v11_both")
LABEL = {"v9_long": "V9 多头", "v10_4": "V10.4 突破+spike（本周期线）",
         "v11_htf_only": "突破+spike（上级突破）单独", "v11_both": "V11 突破+spike（两种合并）"}
ORIGINAL = Path("experiments/active/exp-spike-v10-4-joint-multitf-20260918-v1/results/run_v1/streams")
PARITY = ["signal_i", "entry_i", "entry_time", "entry_price", "initial_stop", "exit_i", "exit_time", "exit_price",
          "exit_reason", "net_r", "censored"]
SEED, REPS = 91509, 2000


def cat(pattern: str) -> pd.DataFrame:
    frames = [pd.read_csv(p) for p in sorted(glob.glob(pattern)) if os.path.getsize(p)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load(run: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    t = cat(str(run / "streams" / "*" / "trades.csv.gz"))
    c = cat(str(run / "streams" / "*" / "controls.csv.gz"))
    s = cat(str(run / "streams" / "*" / "statuses.csv.gz"))
    for column in ("signal_bar_open", "entry_time", "exit_time"):
        t[column] = pd.to_datetime(t[column], utc=True, format="mixed")
    minutes = t.timeframe.map({"15m": 15, "1h": 60})
    t["signal_close"] = t.signal_bar_open + pd.to_timedelta(minutes, unit="min")
    t["period"] = np.where(t.signal_close < study.SPLIT, "earlier", "later")
    t["month"] = t.signal_close.dt.strftime("%Y-%m")
    t["hold_hours"] = (t.exit_time - t.entry_time).dt.total_seconds() / 3600
    t = t.merge(c[["trade_key", "matched", "control_net_r", "control_net_return"]], on="trade_key", how="left",
                validate="one_to_one")
    s["signal_bar_open"] = pd.to_datetime(s.signal_bar_open, utc=True, format="mixed")
    s["period"] = np.where(s.signal_bar_open + pd.to_timedelta(s.timeframe.map({"15m": 15, "1h": 60}), unit="min")
                           < study.SPLIT, "earlier", "later")
    return t, s


def reproduction(t: pd.DataFrame) -> pd.DataFrame:
    original = cat(str(ORIGINAL / "*" / "trades.csv.gz"))
    rows = []
    for tf in ("15m", "1h"):
        for arm, key in (("v10_4", "joint"), ("v9_long", "v9_long")):
            o = original.loc[(original.timeframe == tf) & (original.arm == key)]
            n = t.loc[(t.timeframe == tf) & (t.arm == arm)]
            m = o[["trade_key", *PARITY]].merge(n[["trade_key", *PARITY]], on="trade_key", how="outer",
                                                suffixes=("_o", "_n"), indicator=True)
            both = m.loc[m._merge == "both"]
            diff = 0
            for col in PARITY:
                a, b = both[f"{col}_o"], both[f"{col}_n"]
                if a.dtype.kind in "fc":
                    diff += int((~np.isclose(a.astype(float), b.astype(float), rtol=1e-10, atol=1e-10, equal_nan=True)).sum())
                else:
                    diff += int((a.astype(str).to_numpy() != b.astype(str).to_numpy()).sum())
            rows.append({"timeframe": tf, "arm": arm, "original": len(o), "rerun": len(n),
                         "only_original": int((m._merge == "left_only").sum()),
                         "only_rerun": int((m._merge == "right_only").sum()), "field_differences": diff})
    return pd.DataFrame(rows)


def main_table(t: pd.DataFrame, s: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tf in ("15m", "1h"):
        for arm in ARMS:
            tt, ss = t.loc[(t.timeframe == tf) & (t.arm == arm)], s.loc[(s.timeframe == tf) & (s.arm == arm)]
            for period in ("full", "earlier", "later"):
                tp = tt if period == "full" else tt.loc[tt.period == period]
                sp = ss if period == "full" else ss.loc[ss.period == period]
                rows.append({"timeframe": tf, "arm": arm, "label": LABEL[arm], "period": period, **arm_metrics(tp, sp)})
    return pd.DataFrame(rows)


def diff(t: pd.DataFrame, tf: str, arm_a: str, arm_c: str, period: str) -> dict:
    x = t.loc[(t.timeframe == tf) & (t.status == "closed")]
    if period != "full":
        x = x.loc[x.period == period]
    a, c = x.loc[x.arm == arm_a], x.loc[x.arm == arm_c]
    months = sorted(set(x.month))
    agg = {k: g.groupby("month").agg(n=("net_r", "size"), r=("net_r", "sum")).reindex(months, fill_value=0)
           for k, g in (("a", a), ("c", c))}
    nA, rA = agg["a"].n.to_numpy(float), agg["a"].r.to_numpy(float)
    nC, rC = agg["c"].n.to_numpy(float), agg["c"].r.to_numpy(float)
    rng = np.random.default_rng(SEED)
    vals = []
    for row in rng.integers(0, len(months), size=(REPS, len(months))):
        na, nc = nA[row].sum(), nC[row].sum()
        if na and nc:
            vals.append(rC[row].sum() / nc - rA[row].sum() / na)
    lo, hi = np.quantile(vals, [.025, .975]) if vals else (np.nan, np.nan)
    return {"timeframe": tf, "compare": f"{arm_c} − {arm_a}", "period": period, "months": len(months),
            "valid_reps": len(vals), "a_closed": len(a), "c_closed": len(c), "a_mean_r": a.net_r.mean(),
            "c_mean_r": c.net_r.mean(), "diff_mean_r": c.net_r.mean() - a.net_r.mean(), "ci95_low": lo, "ci95_high": hi,
            "a_total_r": a.net_r.sum(), "c_total_r": c.net_r.sum()}


def verdict(table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tf in ("15m", "1h"):
        for arm in ("v11_both", "v11_htf_only"):
            full = table.query("timeframe == @tf and arm == @arm and period == 'full'").iloc[0]
            later = table.query("timeframe == @tf and arm == @arm and period == 'later'").iloc[0]
            checks = {"full_mean_positive": full.mean_net_r > 0, "later_mean_positive": later.mean_net_r > 0,
                      "excess_positive": full.excess_vs_random_r > 0, "p_below_0_01": full.p_vs_random < 0.01}
            rows.append({"timeframe": tf, "arm": arm, **checks, "passed": all(bool(v) for v in checks.values())})
    return pd.DataFrame(rows)


def by_source(t: pd.DataFrame) -> pd.DataFrame:
    x = t.loc[(t.arm.isin(["v11_both", "v11_htf_only"])) & (t.status == "closed")]
    return (x.groupby(["timeframe", "arm", "source", "joint_order", "period"]).net_r
            .agg(closed="size", mean_net_r="mean", total_net_r="sum").reset_index())


def main(run: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    t, s = load(run)
    table = main_table(t, s)
    diffs = pd.DataFrame([diff(t, tf, a, c, p) for tf in ("15m", "1h")
                          for a, c in (("v10_4", "v11_both"), ("v9_long", "v11_both"), ("v9_long", "v11_htf_only"))
                          for p in ("full", "earlier", "later")])
    tables = {"reproduction": reproduction(t), "main_table": table, "verdict": verdict(table), "differences": diffs,
              "by_source": by_source(t)}
    summaries = []
    for p in sorted((run / "streams").glob("*/completion.json")):
        import json
        for sm in json.loads(p.read_text())["summaries"]:
            summaries.append({k: v for k, v in sm.items() if not isinstance(v, dict)}
                             | {f"n_{k}": v for k, v in sm.get("counts", {}).items()}
                             | {f"both_{k}": v for k, v in sm.get("both_sources", {}).items()})
    cov = pd.DataFrame(summaries)
    tables["coverage"] = cov.groupby("timeframe")[[c for c in cov.columns if c.startswith(("n_", "both_", "htf_breaks"))
                                                  or c == "v10_4_joint_events_differ_in_both"]].sum().reset_index()
    for name, frame in tables.items():
        frame.to_csv(out / f"{name}.csv", index=False)
    t.to_csv(out / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 40)
    for name, frame in tables.items():
        print(f"\n== {name} ==")
        print(frame.to_string(index=False, float_format=lambda v: f"{v:.4f}"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    main(args.run, args.out)

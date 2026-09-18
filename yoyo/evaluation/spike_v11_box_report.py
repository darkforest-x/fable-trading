"""Statistics for the V11.1 box-rule backtest (break while the V9 long box is open).

Source: per-symbol outputs of `spike_v11_box_study`; the loading, reproduction
check, per-arm metrics and month-block difference are the V11 report's
(`spike_v11_report`), applied to this run's arms. Read-only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v11_report as base
from yoyo.evaluation.spike_v10_4_increment_report import arm_metrics

ARMS = ("v9_long", "v10_4", "box_htf", "box_chart", "box_any")
LABEL = {"v9_long": "V9 多头", "v10_4": "V10.4 旧规则突破+spike", "box_htf": "框内+上级突破",
         "box_chart": "框内+本周期突破", "box_any": "框内+任一突破（主）"}


def main_table(t, s):
    rows = []
    for tf in ("15m", "1h"):
        for arm in ARMS:
            tt, ss = t.loc[(t.timeframe == tf) & (t.arm == arm)], s.loc[(s.timeframe == tf) & (s.arm == arm)]
            for period in ("full", "earlier", "later"):
                tp = tt if period == "full" else tt.loc[tt.period == period]
                sp = ss if period == "full" else ss.loc[ss.period == period]
                rows.append({"timeframe": tf, "arm": arm, "label": LABEL[arm], "period": period, **arm_metrics(tp, sp)})
    return pd.DataFrame(rows)


def verdict(table):
    rows = []
    for tf in ("15m", "1h"):
        for arm in ("box_any", "box_htf"):
            full = table.query("timeframe == @tf and arm == @arm and period == 'full'").iloc[0]
            later = table.query("timeframe == @tf and arm == @arm and period == 'later'").iloc[0]
            checks = {"full_mean_positive": full.mean_net_r > 0, "later_mean_positive": later.mean_net_r > 0,
                      "excess_positive": full.excess_vs_random_r > 0, "p_below_0_01": full.p_vs_random < 0.01}
            rows.append({"timeframe": tf, "arm": arm, **checks, "passed": all(bool(v) for v in checks.values())})
    return pd.DataFrame(rows)


def delay(t):
    x = t.loc[t.arm.str.startswith("box") & (t.status == "closed")].copy()
    x["delay_bucket"] = pd.cut(x.bars_after_v9, [-1, 0, 3, 6, 12, 24, 48, 10_000],
                               labels=["同根", "1–3", "4–6", "7–12", "13–24", "25–48", ">48"])
    return (x.groupby(["timeframe", "arm", "delay_bucket", "period"], observed=True).net_r
            .agg(closed="size", mean_net_r="mean", total_net_r="sum").reset_index())


def main(run: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    t, s = base.load(run)
    table = main_table(t, s)
    diffs = pd.DataFrame([base.diff(t, tf, a, c, p) for tf in ("15m", "1h")
                          for a, c in (("v9_long", "box_any"), ("v10_4", "box_any"), ("v9_long", "box_htf"))
                          for p in ("full", "earlier", "later")])
    by_source = (t.loc[(t.arm == "box_any") & (t.status == "closed")].groupby(["timeframe", "source", "period"]).net_r
                 .agg(closed="size", mean_net_r="mean", total_net_r="sum").reset_index())
    rows = []
    for p in sorted((run / "streams").glob("*/completion.json")):
        for sm in json.loads(p.read_text())["summaries"]:
            if "counts" in sm:
                rows.append({"timeframe": sm["timeframe"], "boxes_in_window": sm["boxes_in_window"],
                             "htf_breaks": sm["htf_breaks_visible_in_window"], "chart_breaks": sm["chart_breaks_in_window"],
                             **{f"n_{k}": v for k, v in sm["counts"].items()}})
    coverage = pd.DataFrame(rows).groupby("timeframe").sum().reset_index()
    tables = {"reproduction": base.reproduction(t), "main_table": table, "verdict": verdict(table),
              "differences": diffs, "by_source": by_source, "delay": delay(t), "coverage": coverage}
    for name, frame in tables.items():
        frame.to_csv(out / f"{name}.csv", index=False)
    t.to_csv(out / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 40)
    for name, frame in tables.items():
        print(f"\n== {name} ==")
        cols = [c for c in frame.columns if c not in ("censored_boundary", "censored_gap", "risk_invalid", "no_next_bar",
                                                      "mean_gross_r", "median_hold_h", "concurrent_median", "label")]
        print(frame[cols].to_string(index=False, float_format=lambda v: f"{v:.4f}"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    main(args.run, args.out)

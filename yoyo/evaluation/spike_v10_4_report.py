"""Statistics for the SPIKE V10.4 six-timeframe joint-signal backtest.

Source: the per-symbol outputs of `spike_v10_4_study` (committed runner, run
identity in the output directory). Every number here is a read of those files;
nothing is re-simulated. Periods are assigned by the signal bar's close:
earlier [2024-09-10, 2025-09-10), later [2025-09-10, 2026-05-01).

Matched controls pair each closed trade with one random long from the same
stream, month, fold and ATR/close bucket (drawn by the runner); excess is
target net R minus control net R, tested by whole-calendar-month sign flips
(`spike_v9_full_report.block_statistics`, the V9/V10 statistic).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v9_full_report import block_statistics
from yoyo.evaluation.spike_v10_4_study import ARMS, SPLIT, START, DATA_END, TIMEFRAMES

PERIODS = ("full", "earlier", "later")


def load(run: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    trades, controls, joints, summaries = [], [], [], []
    for folder in sorted((run / "streams").iterdir()):
        if not (folder / "completion.json").is_file():
            continue
        receipt = json.loads((folder / "completion.json").read_text())
        summaries.extend(receipt["summaries"])
        for name, bucket in (("trades", trades), ("controls", controls), ("joints", joints)):
            path = folder / f"{name}.csv.gz"
            if path.is_file():
                bucket.append(pd.read_csv(path))
    t = pd.concat(trades, ignore_index=True)
    c = pd.concat(controls, ignore_index=True)
    j = pd.concat(joints, ignore_index=True) if joints else pd.DataFrame()
    for column in ("signal_bar_open", "entry_time", "exit_time"):
        t[column] = pd.to_datetime(t[column], utc=True, format="mixed")
    t["signal_close"] = t.signal_bar_open + pd.to_timedelta(t.timeframe_min, unit="min")
    t["period"] = np.where(t.signal_close < SPLIT, "earlier", "later")
    t["month"] = t.signal_close.dt.strftime("%Y-%m")
    t["censored"] = t.censored.astype(bool)
    if (t.signal_close < START).any() or (t.signal_close >= DATA_END).any():
        raise ValueError("trade outside the registered window")
    return t, c, j, pd.DataFrame(summaries)


def drawdown(closed: pd.DataFrame) -> float:
    path = closed.sort_values("exit_time").net_r.cumsum().to_numpy()
    if not len(path):
        return 0.0
    peak = np.maximum.accumulate(np.r_[0.0, path])[1:]
    return float((peak - path).max())


def metrics(part: pd.DataFrame, controls: pd.DataFrame) -> dict:
    closed = part.loc[~part.censored]
    r = closed.net_r
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    row = {"signals_taken": len(part), "closed": len(closed), "censored": int(part.censored.sum()),
           "win_rate": float(r.gt(0).mean()) if len(r) else np.nan,
           "mean_net_r": float(r.mean()) if len(r) else np.nan,
           "median_net_r": float(r.median()) if len(r) else np.nan,
           "mean_gross_r": float(closed.gross_r.mean()) if len(r) else np.nan,
           "pf": float(wins / losses) if losses > 0 else np.nan,
           "total_net_r": float(r.sum()),
           "mean_net_bp": float(closed.net_return.mean() * 1e4) if len(r) else np.nan,
           "cost_r_median": float((0.002 / closed.initial_risk_frac).median()) if len(r) else np.nan,
           "ge_10r": int(r.ge(10).sum()), "event_drawdown_r": drawdown(closed)}
    pair = closed[["trade_key", "net_r", "net_return", "month"]].merge(
        controls.loc[controls.matched, ["trade_key", "control_net_r", "control_net_return"]],
        on="trade_key", how="inner", validate="one_to_one")
    delta = pair.net_r - pair.control_net_r
    stats = block_statistics(delta, pair.month) if len(pair) else {"p_month_signflip": np.nan, "blocks": 0}
    row.update({"matched_pairs": len(pair),
                "control_mean_r": float(pair.control_net_r.mean()) if len(pair) else np.nan,
                "paired_excess_r": float(delta.mean()) if len(pair) else np.nan,
                "paired_excess_bp": float((pair.net_return - pair.control_net_return).mean() * 1e4) if len(pair) else np.nan,
                "excess_ci_low": stats.get("mean_ci_low", np.nan), "excess_ci_high": stats.get("mean_ci_high", np.nan),
                "p_month_signflip": stats["p_month_signflip"], "month_blocks": stats["blocks"]})
    return row


def summary(trades: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for timeframe in TIMEFRAMES:
        for arm in ARMS:
            base = trades.loc[(trades.timeframe == timeframe) & (trades.arm == arm)]
            for period in PERIODS:
                part = base if period == "full" else base.loc[base.period == period]
                rows.append({"timeframe": timeframe, "arm": arm, "period": period, **metrics(part, controls)})
    return pd.DataFrame(rows)


def verdict(table: pd.DataFrame) -> pd.DataFrame:
    """The pre-registered bar, per timeframe, on the joint arm only."""
    rows = []
    for timeframe in TIMEFRAMES:
        full = table.query("timeframe == @timeframe and arm == 'joint' and period == 'full'").iloc[0]
        later = table.query("timeframe == @timeframe and arm == 'joint' and period == 'later'").iloc[0]
        checks = {"full_mean_positive": full.mean_net_r > 0, "later_mean_positive": later.mean_net_r > 0,
                  "excess_positive": full.paired_excess_r > 0, "p_below_0_01": full.p_month_signflip < 0.01}
        rows.append({"timeframe": timeframe, **checks, "passed": all(bool(v) for v in checks.values())})
    return pd.DataFrame(rows)


def by_order(trades: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    rows = []
    joint = trades.loc[trades.arm == "joint"]
    for (timeframe, order), part in joint.groupby(["timeframe", "joint_order"]):
        m = metrics(part, controls)
        rows.append({"timeframe": timeframe, "joint_order": order,
                     **{k: m[k] for k in ("closed", "win_rate", "mean_net_r", "pf", "total_net_r",
                                          "paired_excess_r", "p_month_signflip")}})
    return pd.DataFrame(rows)


def by_half(trades: pd.DataFrame) -> pd.DataFrame:
    closed = trades.loc[~trades.censored].copy()
    closed["half"] = closed.signal_close.dt.year.astype(str) + np.where(closed.signal_close.dt.month <= 6, "H1", "H2")
    return (closed.groupby(["timeframe", "arm", "half"]).net_r
            .agg(closed="size", mean_net_r="mean", total_net_r="sum").reset_index())


def overlap(trades: pd.DataFrame) -> pd.DataFrame:
    """How many joint entries are also V9 long signal bars, and how many V9 bars the joint kept."""
    rows = []
    for timeframe in TIMEFRAMES:
        joint = trades.loc[(trades.timeframe == timeframe) & (trades.arm == "joint")]
        v9 = trades.loc[(trades.timeframe == timeframe) & (trades.arm == "v9_long")]
        rows.append({"timeframe": timeframe, "joint_taken": len(joint), "v9_long_taken": len(v9),
                     "joint_on_a_v9_bar": int(joint.also_v9_long.astype(bool).sum()) if len(joint) else 0,
                     "joint_per_v9_long": len(joint) / len(v9) if len(v9) else np.nan})
    return pd.DataFrame(rows)


def coverage(summaries: pd.DataFrame) -> pd.DataFrame:
    skipped_flag = summaries["skipped"].notna() if "skipped" in summaries else pd.Series(False, index=summaries.index)
    ok = summaries.loc[~skipped_flag]
    rows = []
    for timeframe in TIMEFRAMES:
        part = ok.loc[ok.timeframe == timeframe]
        rows.append({"timeframe": timeframe, "streams": len(part),
                     "skipped": int((skipped_flag & summaries.timeframe.eq(timeframe)).sum()),
                     "bars_in_window": int(part.bars_in_window.sum()),
                     "streams_ready_in_window": int((part.ready_in_window > 0).sum()),
                     "v9_long_signals": int(part.v9_long_in_window.sum()),
                     "lines_born": int(part.lines_born_in_window.sum()),
                     "line_breaks": int(part.breaks_in_window.sum()),
                     "joint_signals": int(part.joint_in_window.sum()),
                     "streams_with_joint": int((part.joint_in_window > 0).sum()),
                     "pivot_ties": int(part.pivot_ties.sum()),
                     "partial_buckets": int(part.partial_buckets.fillna(0).sum()) if "partial_buckets" in part else 0,
                     "gaps": int(part.gaps.sum())})
    return pd.DataFrame(rows)


def main(run: Path, out: Path) -> None:
    trades, controls, joints, summaries = load(run)
    out.mkdir(parents=True, exist_ok=True)
    table = summary(trades, controls)
    tables = {"summary": table, "verdict": verdict(table), "by_order": by_order(trades, controls),
              "by_half": by_half(trades), "overlap": overlap(trades), "coverage": coverage(summaries)}
    for name, frame in tables.items():
        frame.to_csv(out / f"{name}.csv", index=False)
    trades.to_csv(out / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    controls.to_csv(out / "controls.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    if len(joints):
        joints.to_csv(out / "joints.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    pd.set_option("display.width", 250)
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

"""Describe concentration and time persistence of the frozen symbol rankings.

Owner 2026-10-17 asks what the existing statistics imply. This is a descriptive
reanalysis of previously observed data, not a new strategy or unseen evaluation.
No features or trading parameters change. Full-history removals are sensitivity
checks, never causal admission rules. For a chronological diagnostic, rank the
first period's symbols by net-R sum on trades entered AND exited before 2025;
inspect their original later entries (>=2025) without rerunning price paths.
Only common symbols with at least one closed trade in each period enter rank
correlations. Preserve the original prepared matched controls and require their
entries to be later too for the later comparison. No significance claim is made.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess

import pandas as pd

from yoyo.evaluation.spike_v131_symbol_summary import CONFIG, load, metrics, paired_metrics


def describe(frame, pairs):
    if frame.empty:
        return {"trades": 0, "matched": 0, "sum_net_r": 0.0}
    paired = pairs[pairs.trade_key.isin(frame.trade_key)]
    return metrics(frame) | paired_metrics(paired, len(frame))


def run():
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", __file__], check=True)
    subprocess.run(["git", "ls-files", "--error-unmatch", __file__], check=True, stdout=subprocess.DEVNULL)
    cfg = json.loads(CONFIG.read_text())
    output = Path(cfg["output"]) / "ranking_diagnostics_20261017.json"
    if output.exists():
        raise ValueError("refusing to overwrite existing diagnostic")
    trades, pairs, audit, _ = load(cfg)
    for col in ["entry_time", "exit_time"]:
        trades[col] = pd.to_datetime(trades[col], utc=True)
    control_times = []
    for minutes, root, filename in [(15, cfg["source_15m"], "matched_controls.csv.gz"),
                                     (60, cfg["source_1h"], "trades.csv.gz")]:
        fields = ["timeframe_min", "trade_key", "entry_time"]
        if minutes == 60:
            fields += ["cohort", "version", "policy"]
        frame = pd.read_csv(Path(root) / filename, usecols=fields)
        if minutes == 60:
            frame = frame[frame.cohort.eq("control") & frame.version.eq("retest") &
                          frame.timeframe_min.eq(60) & frame.policy.eq("trail2")]
        control_times.append(frame[["timeframe_min", "trade_key", "entry_time"]])
    times = pd.concat(control_times).rename(columns={"entry_time": "control_entry_time"})
    pairs = pairs.merge(times, on=["timeframe_min", "trade_key"], validate="one_to_one")
    pairs["control_entry_time"] = pd.to_datetime(pairs.control_entry_time, utc=True)
    cut = pd.Timestamp("2025-01-01T00:00:00Z")
    groups = []
    for (minutes, side), part in trades.groupby(["timeframe_min", "side"]):
        paired = pairs[pairs.timeframe_min.eq(minutes) & pairs.side.eq(side)]
        by = part.groupby("symbol").net_r.agg(["count", "sum", "mean"])
        top10 = by.reset_index().sort_values(["sum", "symbol"], ascending=[False, True]).head(10).symbol.tolist()
        ordered = part.sort_values(["net_r", "trade_key"], ascending=[False, True])
        top_count = math.ceil(len(part) * .01)
        early = part[(part.entry_time < cut) & (part.exit_time < cut)]
        later = part[part.entry_time >= cut]
        cross = part[(part.entry_time < cut) & (part.exit_time >= cut)]
        before = early.groupby("symbol").net_r.agg(["count", "sum", "mean"])
        after = later.groupby("symbol").net_r.agg(["count", "sum", "mean"])
        common = before.join(after, how="inner", lsuffix="_early", rsuffix="_later")
        early_top10 = before.reset_index().sort_values(["sum", "symbol"], ascending=[False, True]).head(10).symbol.tolist()
        later_pairs = paired[paired.control_entry_time >= cut]
        selected_later = later[later.symbol.isin(early_top10)]
        wins = part.loc[part.net_r > 0, "net_r"]
        losses = part.loc[part.net_r < 0, "net_r"]
        row = {"timeframe": "15m" if minutes == 15 else "1H", "side": int(side),
               "all": describe(part, paired), "symbols": len(by),
               "profitable_symbols": int((by["sum"] > 0).sum()),
               "median_symbol_net_r": float(by["sum"].median()),
               "median_trades_per_symbol": float(by["count"].median()),
               "symbols_under_5_trades": int((by["count"] < 5).sum()),
               "symbols_under_10_trades": int((by["count"] < 10).sum()),
               "median_trade_net_r": float(part.net_r.median()),
               "average_winning_net_r": float(wins.mean()),
               "average_losing_net_r": float(losses.mean()),
               "winning_to_losing_mean_ratio": float(wins.mean() / -losses.mean()),
               "best_trade": {k: str(ordered.iloc[0][k]) for k in ["symbol", "entry_time", "net_r"]},
               "without_best_trade": describe(ordered.iloc[1:], paired),
               "top_1pct_trade_count": top_count,
               "top_1pct_positive_r_share": float(ordered.head(top_count).net_r.sum() / wins.sum()),
               "without_top_1pct_trades": describe(ordered.iloc[top_count:], paired),
               "full_history_top10_symbols": top10,
               "full_history_top10_sum_net_r": float(by.loc[top10, "sum"].sum()),
               "without_full_history_top10_symbols": describe(part[~part.symbol.isin(top10)], paired),
               "chronology": {"early_closed": len(early), "later_closed": len(later), "cross_closed": len(cross),
                   "common_symbols": len(common),
                   "spearman_mean_net_r": float(common.mean_early.rank().corr(common.mean_later.rank())),
                   "spearman_sum_net_r": float(common.sum_early.rank().corr(common.sum_later.rank())),
                   "early_top10_symbols": early_top10,
                   "early_top10_symbols_with_later_trades": int(selected_later.symbol.nunique()),
                   "early_top10_later": describe(selected_later, later_pairs),
                   "all_later": describe(later, later_pairs),
                   "nonselected_later": describe(later[~later.symbol.isin(early_top10)], later_pairs)}}
        assert len(early) + len(later) + len(cross) == len(part)
        groups.append(row)
    result = {"scope": "Descriptive concentration and chronology on reused history; not blind validation.",
              "split": str(cut), "audit": audit,
              "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "groups": groups}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()

"""Pre-registered 'best parameters' read for exp-htf-ma-reversion-20261009-v4 - v3's rule unchanged.

v4 copies v3's analyze.py verbatim except the paths: --run eth reads results_eth (split 2025-10-09),
--run market reads results_market (split 2025-01-01). The ranking rule below is v3's, fixed before
v4 results existed (config_eth.json 'selection').

Owner 2026-10-09: 你用 ethusdt.p 近两年的数据回测一下 找到最优参数. A best cell chosen on the same data
it is judged on is in-sample by construction, so the rule is fixed here before any result exists:

  1. select year = 2024-10-09 .. 2025-10-09, check year = 2025-10-09 .. 2026-10-09 (config split);
  2. among cells with >= 20 select-year trades, rank by select-year total net % (sum of per-trade
     net after the 0.2% round trip, 1x notional per trade, no compounding);
  3. the top cell is 'the best parameters'; its check-year net, win rate, excess over the 20
     matched random entries per trade and month-block sign-flip p are the verdict;
  4. also reported: the select top 10 with their check numbers, the Spearman rank correlation of
     select vs check total net over all eligible cells, and the full-two-year in-sample optimum
     (a contrast only - it has seen the check year).

Writes best_params.csv, top10.csv and best_params.json next to the results.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-htf-ma-reversion-20261009-v4/analyze.py --run eth
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import htf_ma_reversion_v1 as hr

EXP = Path("experiments/active/exp-htf-ma-reversion-20261009-v4")
MIN_SELECT_TRADES = 20
RESULTS = "results_eth"
KEEP = ["trades", "win_rate", "target_rate", "mean_net_pct", "mean_net_r", "control_net_r", "excess_r", "p_excess",
        "net_r_ex_best5_days", "median_hold_h"]


def main() -> None:
    global RESULTS
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", choices=["eth", "market"], default="eth")
    RESULTS = f"results_{parser.parse_args().run}"
    res = EXP / RESULTS
    summary = pd.read_csv(res / "summary.csv")
    cell = hr.cell_keys(summary)
    summary["total_net_pct"] = summary.mean_net_pct * summary.trades
    sel = summary.loc[summary.period.eq("select")].set_index(cell)
    chk = summary.loc[summary.period.eq("check")].set_index(cell)
    eligible = sel.loc[sel.trades >= MIN_SELECT_TRADES].sort_values("total_net_pct", ascending=False)
    joined = eligible[KEEP + ["total_net_pct"]].add_prefix("select_").join(
        chk[KEEP + ["total_net_pct"]].add_prefix("check_"), how="left")
    top = joined.head(10).reset_index()
    top.to_csv(res / "top10.csv", index=False)
    both = joined.dropna(subset=["check_total_net_pct"])
    rho = float(both.select_total_net_pct.rank().corr(both.check_total_net_pct.rank()))
    trades = pd.read_csv(res / "trades.csv.gz")
    trades = trades.loc[np.isfinite(trades.net_r) & np.isfinite(trades.control_net_r)]
    full = trades.groupby(cell).agg(trades=("net_ret", "size"), total_net_pct=("net_ret", lambda s: 100 * s.sum()))
    full = full.loc[full.trades >= 2 * MIN_SELECT_TRADES].sort_values("total_net_pct", ascending=False)
    best = top.iloc[0]
    out = {"rule": "select-year total net %, >= 20 trades", "eligible_cells": int(len(eligible)),
           "cells_with_check_trades": int(len(both)), "spearman_select_vs_check": rho,
           "best": {k: (v.item() if hasattr(v, "item") else v) for k, v in best.items()},
           "check_positive_share_of_select_top10": float((top.check_total_net_pct > 0).mean()),
           "in_sample_two_year_best": {**dict(zip(cell, full.index[0])), **full.iloc[0].to_dict()} if len(full) else None}
    (res / "best_params.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + "\n")
    top.head(1).to_csv(res / "best_params.csv", index=False)
    pd.set_option("display.width", 250)
    cols = cell + ["select_trades", "select_total_net_pct", "select_win_rate", "check_trades", "check_total_net_pct",
                   "check_mean_net_pct", "check_win_rate", "check_excess_r", "check_p_excess"]
    print(top[cols].round(3).to_string(index=False))
    print(json.dumps({k: out[k] for k in ("eligible_cells", "cells_with_check_trades", "spearman_select_vs_check",
                                          "check_positive_share_of_select_top10")}, indent=1))
    print("in-sample two-year best (contrast):", out["in_sample_two_year_best"])


if __name__ == "__main__":
    main()

"""Pre-registered read of exp-htf-ma-reversion-20261010-v5 (daily trend filter on v4), committed before results.

  1. arms: per run and period, over all cells - mean net R per trade (cell average), share of cells net
     positive, mean excess over the trend-matched controls; 'with' vs 'against' vs v4 (no filter);
     cells net positive in both periods per arm
  2. selection: the builder's pre-registered selection rows for the 'with' arm and their check-period
     numbers against the pass bar (net > 0 and excess > 0 with month-block p < 0.01; p < 0.05 =
     forward observation only)
  3. ETH: v3's best-parameter rule (>= 20 select-year trades, top select-year total net %) on 'with'
     cells, judged on the second year

Writes arms.csv, with_selection.csv, eth_best.json under the experiment.
Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-htf-ma-reversion-20261010-v5/analyze.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from yoyo.evaluation import htf_ma_reversion_v1 as hr

EXP = Path("experiments/active/exp-htf-ma-reversion-20261010-v5")
V4 = Path("experiments/active/exp-htf-ma-reversion-20261009-v4")
COLS = ["minutes", "side", "measure", "level", "target_r", "reentry", "conf", "select_trades", "select_mean_net_pct",
        "check_trades", "check_mean_net_pct", "check_mean_net_r", "check_excess_r", "check_p_excess"]


def arm_table(summary: pd.DataFrame, label: str) -> list[dict]:
    rows = []
    cell = [k for k in hr.cell_keys(summary) if k != "trend"]
    groups = summary.groupby("trend") if "trend" in summary else [(label, summary)]
    for arm, g in groups:
        w = g.pivot_table(index=cell, columns="period", values="mean_net_r")
        for period, s in g.groupby("period"):
            rows.append({"arm": arm, "period": period, "cells": len(s), "mean_net_r": s.mean_net_r.mean(),
                         "share_net_pos": (s.mean_net_r > 0).mean(), "mean_excess_r": s.excess_r.mean(),
                         "trades": int(s.trades.sum()),
                         "cells_pos_both": int(((w.select > 0) & (w.check > 0)).sum())})
    return rows


def main() -> None:
    rows, sels = [], []
    for run in ("market", "eth"):
        summary = pd.read_csv(EXP / f"results_{run}" / "summary.csv")
        base = pd.read_csv(V4 / f"results_{run}" / "summary.csv")
        rows += [{"run": run, **r} for r in arm_table(summary, "") + arm_table(base, "v4_all")]
        sel = pd.read_csv(EXP / f"results_{run}" / "selection.csv")
        sel = sel.loc[sel.trend.eq("with")].assign(run=run)
        sel["passes"] = (sel.check_mean_net_r > 0) & (sel.check_excess_r > 0) & (sel.check_p_excess < 0.01)
        sel["forward_watch"] = (sel.check_mean_net_r > 0) & (sel.check_excess_r > 0) & (sel.check_p_excess < 0.05)
        sels.append(sel)
    arms = pd.DataFrame(rows)
    arms.to_csv(EXP / "arms.csv", index=False)
    sel = pd.concat(sels, ignore_index=True)
    sel.to_csv(EXP / "with_selection.csv", index=False)
    s = pd.read_csv(EXP / "results_eth" / "summary.csv")
    s = s.loc[s.trend.eq("with")].assign(total=lambda x: x.mean_net_pct * x.trades)
    cell = hr.cell_keys(s)
    sel_y1 = s.loc[s.period.eq("select") & (s.trades >= 20)].sort_values("total", ascending=False)
    chk = s.loc[s.period.eq("check")].set_index(cell)
    best = sel_y1.iloc[0] if len(sel_y1) else None
    eth = {"eligible_cells": int(len(sel_y1))}
    if best is not None:
        key = tuple(best[k] for k in cell)
        c = chk.loc[key] if key in chk.index else None
        eth.update({"best": {k: best[k] for k in cell}, "select_trades": int(best.trades),
                    "select_total_net_pct": float(best.total),
                    "check_trades": None if c is None else int(c.trades),
                    "check_total_net_pct": None if c is None else float(c.mean_net_pct * c.trades),
                    "check_excess_r": None if c is None else float(c.excess_r),
                    "check_p_excess": None if c is None else float(c.p_excess)})
        top = sel_y1.head(10).set_index(cell)
        both = top.join(chk[["mean_net_pct", "trades"]], rsuffix="_check", how="left")
        eth["top10_check_positive"] = int(((both.mean_net_pct_check * both.trades_check) > 0).sum())
    (EXP / "eth_best.json").write_text(json.dumps(eth, ensure_ascii=False, indent=1, default=str) + "\n")
    pd.set_option("display.width", 250)
    print(arms.round(3).to_string(index=False))
    print(sel[["run"] + COLS + ["passes", "forward_watch"]].round(3).to_string(index=False))
    print(json.dumps(eth, ensure_ascii=False, default=str, indent=1))


if __name__ == "__main__":
    main()

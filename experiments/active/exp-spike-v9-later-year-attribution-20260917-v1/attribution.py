"""Why SPIKE V9 loses in the later year.

Reads the published V9 per-trade ledger and its frozen matched random-entry
controls -- no replay, no configuration change, no new holdout scoring -- and
splits the earlier-to-later change in net R per trade into the parts that can be
told apart with what was already recorded:

  net R per trade = gross R per trade - cost R per trade

so a fall in net has three candidate causes, and the ledger can separate them:

  * gross edge decay, further split by the matched control into the part that hit
    random entries too (market) and the part specific to the signal (excess);
  * the fixed 0.2% nominal cost becoming more expensive in R units when stop
    distances tighten;
  * a change in where the trades are -- timeframe, side, month -- rather than in
    any single trade's quality.

Periods follow the published report exactly: earlier = entry and exit both before
2025-09-10, later = entry at or after it, and trades straddling the cut are held
out of both. The untrimmed totals must reproduce statistics/full_v1/summary.csv
or the run raises before writing anything.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_be05_report import metrics, periods, SPLIT

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = ROOT / "experiments/active/exp-spike-v9-full-backtest-20260915-v1/statistics/full_v1"
NOMINAL_COST = 0.002


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def shape(frame: pd.DataFrame) -> dict:
    """Payoff shape: a 4ATR trail that only arms at 2R needs trades to reach 2R."""
    win = frame.net_r > 0
    return {"trades": len(frame),
            "net_r": float(frame.net_r.sum()), "net_r_per_trade": float(frame.net_r.mean()),
            "gross_r": float(frame.gross_r.sum()), "gross_r_per_trade": float(frame.gross_r.mean()),
            "cost_r_per_trade": float(frame.cost_r.mean()),
            "win_rate": float(win.mean()),
            "avg_win_r": float(frame.net_r[win].mean()), "avg_loss_r": float(frame.net_r[~win].mean()),
            "share_net_ge_2r": float((frame.net_r >= 2).mean()),
            "realized_ge_10r": int((frame.net_r >= 10).sum()),
            "share_mfe_ge_2r": float((frame.mfe_r >= 2).mean()),
            "mean_mfe_r": float(frame.mfe_r.mean()),
            "median_initial_risk_frac": float(frame.initial_risk_frac.median()),
            "breakeven_roundtrip_bp": float(1e4 * NOMINAL_COST * frame.gross_r.mean() / frame.cost_r.mean())}


def verify_parity(trades: pd.DataFrame) -> None:
    published = pd.read_csv(SOURCE / "summary.csv")
    published = published[(published.arm == "v9") & (published.group == "all")].set_index("period")
    for period, part in periods(trades):
        if abs(metrics(part)["total_r"] - published.loc[period].total_r) > 1e-6:
            raise ValueError(f"{period} does not reproduce the published summary")


def main() -> None:
    trades = pd.read_csv(SOURCE / "trades.csv.gz", parse_dates=["entry_time", "exit_time"])
    trades = trades[trades.arm == "v9"]
    verify_parity(trades)

    closed = trades[~trades.censored.astype(bool)].copy()
    closed["cost_r"] = closed.gross_r - closed.net_r
    closed["period"] = np.where(closed.entry_time >= SPLIT, "later",
                                np.where(closed.exit_time < SPLIT, "earlier", "straddles_cut"))
    closed["entry_month"] = closed.entry_time.dt.strftime("%Y-%m")
    scored = closed[closed.period != "straddles_cut"]

    rows = [{"split": "period", "key": period, **shape(part)} for period, part in scored.groupby("period")]
    for (period, timeframe), part in scored.groupby(["period", "timeframe_min"]):
        rows.append({"split": "period_x_timeframe", "key": f"{period}_{timeframe}m", **shape(part)})
    for (period, side), part in scored.groupby(["period", "side"]):
        rows.append({"split": "period_x_side", "key": f"{period}_{'long' if side == 1 else 'short'}", **shape(part)})
    for (period, timeframe, side), part in scored.groupby(["period", "timeframe_min", "side"]):
        rows.append({"split": "period_x_timeframe_x_side",
                     "key": f"{period}_{timeframe}m_{'long' if side == 1 else 'short'}", **shape(part)})
    decomposition = pd.DataFrame(rows)
    decomposition["share_of_period_trades"] = decomposition.trades / decomposition.apply(
        lambda r: decomposition.loc[(decomposition.split == "period")
                                    & (decomposition.key == r.key.split("_")[0]), "trades"].iloc[0]
        if r.split != "period" else r.trades, axis=1)
    decomposition.to_csv(HERE / "results/decomposition.csv", index=False)

    monthly = closed.groupby(["entry_month", "timeframe_min"]).agg(
        trades=("net_r", "size"), net_r=("net_r", "sum"), gross_r=("gross_r", "sum"),
        gross_r_per_trade=("gross_r", "mean"), net_r_per_trade=("net_r", "mean")).reset_index()
    monthly.to_csv(HERE / "results/monthly_by_timeframe.csv", index=False)

    controls = pd.read_csv(SOURCE / "controls.csv.gz",
                           parse_dates=["entry_time", "control_entry_time", "control_exit_time"])
    controls = controls[(controls.arm == "v9") & controls.matched]
    early = controls[(controls.entry_time < SPLIT) & (controls.control_entry_time < SPLIT)
                     & (controls.control_exit_time < SPLIT)]
    late = controls[(controls.entry_time >= SPLIT) & (controls.control_entry_time >= SPLIT)]
    control_rows = [{"period": label, "pairs": len(part),
                     "strategy_mean_r": float(part.target_net_r.mean()),
                     "random_mean_r": float(part.control_net_r.mean()),
                     "excess_mean_r": float((part.target_net_r - part.control_net_r).mean())}
                    for label, part in [("earlier", early), ("later", late)]]
    control = pd.DataFrame(control_rows).set_index("period")
    change = {"strategy_change": control.loc["later"].strategy_mean_r - control.loc["earlier"].strategy_mean_r,
              "random_change": control.loc["later"].random_mean_r - control.loc["earlier"].random_mean_r,
              "excess_change": control.loc["later"].excess_mean_r - control.loc["earlier"].excess_mean_r}
    change["random_share_of_strategy_change"] = change["random_change"] / change["strategy_change"]
    change["excess_share_of_strategy_change"] = change["excess_change"] / change["strategy_change"]
    control.reset_index().assign(**{k: v for k, v in change.items()}).to_csv(
        HERE / "results/control_decomposition.csv", index=False)

    receipt = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "builder_commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                         capture_output=True, text=True, check=True).stdout.strip(),
        "builder_sha256": digest(Path(__file__)),
        "inputs": {str(p.relative_to(ROOT)): digest(p) for p in
                   [SOURCE / "trades.csv.gz", SOURCE / "controls.csv.gz", SOURCE / "summary.csv"]},
        "nominal_round_trip_cost": NOMINAL_COST,
        "straddling_trades_excluded": int((closed.period == "straddles_cut").sum()),
        "replayed_prices": False, "configuration_changed": False, "holdout_consumed": 0,
    }
    (HERE / "results/receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(decomposition[decomposition.split.isin(["period", "period_x_timeframe"])].to_string(index=False))
    print(control.to_string(), "\n", json.dumps(change, indent=2))


if __name__ == "__main__":
    main()

"""Summarize the dense-launch BTC/ETH backtest: both arms, per timeframe, vs random.

Reads one completed ``ma_dense_launch_backtest`` run. Cells are arm x timeframe
x scope; the Grade-A arm is the research rule, ``hard_only`` is what Pine shows
with the Grade-A switch off. Inference uses the pre-registered contract: ISO
week blocks for the one-sided sign-flip p on the paired excess over matched
random entries, UTC month blocks for the bootstrap interval. Cells with fewer
than twenty closed trades report descriptive numbers only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

MIN_TRADES_FOR_INFERENCE = 20


def block_inference(values: pd.Series, blocks: pd.Series, rng, reps: int, flips: int):
    frame = pd.DataFrame({"x": values.to_numpy(float), "b": blocks.to_numpy()}).dropna()
    if frame.empty:
        return np.nan, np.nan, np.nan
    grouped = frame.groupby("b").x
    sums, counts = grouped.sum().to_numpy(), grouped.size().to_numpy()
    if len(sums) < 2:
        return np.nan, np.nan, np.nan
    draws = rng.integers(0, len(sums), size=(reps, len(sums)))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    signs = rng.choice([-1.0, 1.0], size=(flips, len(sums)))
    p = float(((signs * sums).sum(axis=1) >= sums.sum()).mean())
    return float(np.quantile(means, .025)), float(np.quantile(means, .975)), p


def summarize(run: Path, cfg: dict) -> pd.DataFrame:
    trades = pd.read_csv(run / "trades.csv", parse_dates=["signal_close", "entry_time", "exit_time"])
    controls = pd.read_csv(run / "controls.csv")
    merged = trades.merge(controls[["trade_key", "matched", "control_net_r", "control_net_bp"]],
                          on="trade_key", how="left")
    merged["week"] = merged.entry_time.dt.strftime("%G-W%V")
    merged["month"] = merged.entry_time.dt.strftime("%Y-%m")
    rng = np.random.default_rng(int(cfg["control_seed"]))
    rows = []
    for arm in ("grade_a", "hard_only"):
        pool = merged[merged.grade_a] if arm == "grade_a" else merged
        for timeframe in sorted(set(merged.timeframe_min)) + ["all"]:
            scoped_tf = pool if timeframe == "all" else pool[pool.timeframe_min == timeframe]
            for scope in ("all", "BTC_USDT_SWAP", "ETH_USDT_SWAP"):
                g = scoped_tf if scope == "all" else scoped_tf[scoped_tf.symbol == scope]
                if g.empty:
                    continue
                net_r, net_bp = g.net_r.astype(float), g.net_bp.astype(float)
                pairs = g[g.matched.fillna(False).astype(bool)]
                row = {"arm": arm, "timeframe_min": timeframe, "scope": scope, "trades": len(g),
                       "tp": int((g.result == "TP").sum()), "sl": int((g.result == "SL").sum()),
                       "timeout": int((g.result == "TIMEOUT").sum()),
                       "win_rate": float((net_r > 0).mean()), "mean_net_r": float(net_r.mean()),
                       "mean_net_bp": float(net_bp.mean()), "sum_net_r": float(net_r.sum()),
                       "mean_gross_bp": float(g.gross_bp.mean()),
                       "median_risk_bp": float(g.risk_frac.median() * 1e4),
                       "pairs": len(pairs)}
                if len(pairs):
                    excess_r = pairs.net_r.astype(float) - pairs.control_net_r.astype(float)
                    excess_bp = pairs.net_bp.astype(float) - pairs.control_net_bp.astype(float)
                    row.update(control_net_r=float(pairs.control_net_r.mean()),
                               control_net_bp=float(pairs.control_net_bp.mean()),
                               excess_r=float(excess_r.mean()), excess_bp=float(excess_bp.mean()))
                    if len(g) >= MIN_TRADES_FOR_INFERENCE:
                        lo, hi, p = block_inference(excess_bp, pairs.week, rng, int(cfg["bootstrap"]), int(cfg["flips"]))
                        row.update(excess_bp_ci_low=lo, excess_bp_ci_high=hi, excess_bp_p=p)
                        lo_r, hi_r, p_r = block_inference(excess_r, pairs.week, rng, int(cfg["bootstrap"]), int(cfg["flips"]))
                        row.update(excess_r_ci_low=lo_r, excess_r_ci_high=hi_r, excess_r_p=p_r)
                row["verdict"] = ("insufficient" if len(g) < MIN_TRADES_FOR_INFERENCE else
                                  "effective" if row["mean_net_bp"] > 0 and row.get("excess_bp_p", 1.0) < float(cfg["p_threshold"])
                                  else "not_effective")
                rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads((args.run / "receipt.json").read_text())["config"]
    table = summarize(args.run, cfg)
    args.output.mkdir(parents=True, exist_ok=False)
    table.to_csv(args.output / "summary.csv", index=False)
    show = table[(table.scope == "all")][["arm", "timeframe_min", "trades", "tp", "win_rate", "mean_net_r",
                                          "mean_net_bp", "mean_gross_bp", "excess_bp", "excess_bp_p", "verdict"]]
    print(show.round(3).to_string(index=False))


if __name__ == "__main__":
    main()

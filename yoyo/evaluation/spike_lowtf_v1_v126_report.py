"""Summary statistics for the SPIKE V1 vs V12.6 low-timeframe replay.

Reads a completed ``spike_lowtf_v1_v126`` run (receipts re-verified) and writes
one row per arm x timeframe x scope x period.  Periods are by entry time:
full, earlier (< split), later (>= split) and recent (>= recent_start, a
subset of later).  Censored trades never count as wins or losses.

Matched-random inference (v1_common, v126 only): per matched pair the excess
is target net R minus control net R.  95% intervals come from a UTC-month block
bootstrap; the one-sided p flips the sign of each month's summed excess.  Both
follow the plan; no statistic is chosen after seeing results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_lowtf_v1_v126 as build

SEED = 922114
FLIPS = 10000


def load(run: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    manifest = json.loads((run / "manifest.json").read_text())
    if not manifest["complete"]:
        raise ValueError("run incomplete")
    trades, controls = [], []
    for name in manifest["streams"]:
        d = run / "streams" / name
        receipt = json.loads((d / "receipt.json").read_text())
        for file, sha in receipt["files"].items():
            if hashlib.sha256((d / file).read_bytes()).hexdigest() != sha:
                raise ValueError(f"artifact changed: {d / file}")
        t = pd.read_csv(d / "trades.csv.gz", parse_dates=["entry_time", "exit_time"])
        c = pd.read_csv(d / "controls.csv.gz")
        trades.append(t); controls.append(c)
    return pd.concat(trades, ignore_index=True), pd.concat(controls, ignore_index=True)


def _pf(r: pd.Series) -> float:
    loss = -r[r < 0].sum()
    return float(r[r > 0].sum() / loss) if loss > 0 else np.nan


def month_inference(excess: pd.Series, months: pd.Series, rng: np.random.Generator, reps: int) -> tuple[float, float, float]:
    """UTC-month block bootstrap 95% CI of the mean, and one-sided sign-flip p."""
    frame = pd.DataFrame({"x": excess.to_numpy(float), "m": months.to_numpy()})
    sums = frame.groupby("m").x.sum().to_numpy()
    counts = frame.groupby("m").x.size().to_numpy()
    if len(sums) < 2:
        return np.nan, np.nan, np.nan
    draws = rng.integers(0, len(sums), size=(reps, len(sums)))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    observed = sums.sum()
    signs = rng.choice([-1.0, 1.0], size=(FLIPS, len(sums)))
    p = float(((signs * sums).sum(axis=1) >= observed).mean())
    return float(np.quantile(means, .025)), float(np.quantile(means, .975)), p


def summarize(trades: pd.DataFrame, controls: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    start, split, recent, end = build.window(cfg)
    rng = np.random.default_rng(SEED)
    trades = trades.copy()
    trades["entry_time"] = pd.to_datetime(trades.entry_time, utc=True)
    trades["month"] = trades.entry_time.dt.strftime("%Y-%m")
    merged = trades.merge(controls[["trade_key", "matched", "control_net_r"]], on="trade_key", how="left")
    periods = {"full": (start, end), "earlier": (start, split), "later": (split, end), "recent": (recent, end)}
    rows = []
    for (arm, minutes), group in merged.groupby(["arm", "timeframe_min"]):
        for scope in ("all", "ETH_USDT_SWAP", "BTC_USDT_SWAP"):
            scoped = group if scope == "all" else group[group.symbol == scope]
            for period, (lo, hi) in periods.items():
                g = scoped[(scoped.entry_time >= lo) & (scoped.entry_time < hi)]
                closed = g[~g.censored.astype(bool)]
                r = closed.net_r.astype(float)
                days = (hi - lo).total_seconds() / 86400 * (1 if scope != "all" else 2)
                row = {"arm": arm, "timeframe_min": minutes, "scope": scope, "period": period,
                       "entries": len(g), "closed": len(closed), "censored": int(g.censored.astype(bool).sum()),
                       "win_rate": float((r > 0).mean()) if len(r) else np.nan,
                       "mean_net_r": float(r.mean()) if len(r) else np.nan,
                       "mean_net_bp": float(closed.net_return.mean() * 1e4) if len(r) else np.nan,
                       "sum_net_r": float(r.sum()), "pf_r": _pf(r) if len(r) else np.nan,
                       "gt5r": int((r > 5).sum()), "gt10r": int((r > 10).sum()),
                       "median_risk_frac": float(closed.initial_risk_frac.median()) if len(r) else np.nan,
                       "trades_per_symbol_day": len(g) / days if days > 0 else np.nan}
                row["median_cost_r"] = .002 / row["median_risk_frac"] if row["median_risk_frac"] else np.nan
                pairs = closed[closed.matched.fillna(False).astype(bool)]
                if arm != "v1_native" and len(pairs):
                    excess = pairs.net_r.astype(float) - pairs.control_net_r.astype(float)
                    lo_ci, hi_ci, p = month_inference(excess, pairs.month, rng, int(cfg["bootstrap"]))
                    row.update(matched=len(pairs), control_mean_r=float(pairs.control_net_r.mean()),
                               excess_r=float(excess.mean()), excess_ci_low=lo_ci, excess_ci_high=hi_ci,
                               excess_p_one_sided=p, months=int(pairs.month.nunique()))
                rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads((args.run / "identity.json").read_text())["config"]
    trades, controls = load(args.run)
    table = summarize(trades, controls, cfg)
    args.output.mkdir(parents=True, exist_ok=False)
    table.to_csv(args.output / "summary.csv", index=False)
    top = trades[~trades.censored.astype(bool)].nlargest(20, "net_r")[
        ["arm", "symbol", "timeframe_min", "entry_time", "exit_time", "net_r", "exit_reason"]]
    top.to_csv(args.output / "top_trades.csv", index=False)
    receipt = {"run": str(args.run), "trades": len(trades), "controls": len(controls), "seed": SEED,
               "files": {n: hashlib.sha256((args.output / n).read_bytes()).hexdigest()
                         for n in ("summary.csv", "top_trades.csv")}}
    build.dump(args.output / "receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Describe returns of the fixed owner-selected 29-symbol V11.2 inventory.

Reads the SHA-bound 570-entry inventory; no prices are fetched or strategies
rerun. Future exits are outcomes only. Same 20bp cost, strict temporal split,
and original matched random controls; no parameter or symbol optimization.
Reports original-risk R and equal-notional return separately because their
economic weightings differ. Open boundary trades never enter closed metrics.
"""
from pathlib import Path
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v112_1h_diagnostics import metrics, sha, SPLIT
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-v112-selected-returns-20260919-v1")
SOURCE = Path("experiments/active/exp-spike-v112-selected-counts-20260919-v1")


def main():
    declared = (Path(__file__), EXP / "PROJECT_PLAN.md",
                Path("yoyo/evaluation/spike_v112_1h_diagnostics.py"),
                Path("yoyo/evaluation/spike_v9_full_report.py"))
    assert _committed(declared), "Commit the builder and plan first"
    receipt = json.loads((SOURCE / "completion.json").read_text())
    for path, digest in receipt["files"].items():
        assert sha(path) == digest, path
    trades = pd.read_csv(SOURCE / "selected_trades.csv")
    small = trades[trades.timeframe.isin(["15m", "1h"])].copy()
    large = trades[trades.timeframe.eq("4h")].copy()
    controls = pd.concat([pd.read_csv(p) for p in (SOURCE / "results").glob("*/controls.csv")], ignore_index=True)
    controls = controls[controls.arm.eq("box_any")].drop(columns="arm")
    duplicate_cols = list((set(large.columns) & set(controls.columns)) - {"trade_key"})
    large = large.drop(columns=duplicate_cols).merge(controls, on="trade_key", how="left", validate="one_to_one", indicator=True)
    assert large._merge.eq("both").all()
    trades = pd.concat([small, large.drop(columns="_merge")], ignore_index=True)
    trades["signal_close"] = pd.to_datetime(trades.signal_bar_open, utc=True) + pd.to_timedelta(trades.timeframe.map({"15m": 15, "1h": 60, "4h": 240}), unit="min")
    for column in ("exit_time", "entry_time", "control_exit_time"):
        trades[column] = pd.to_datetime(trades[column], utc=True)
    trades["month"] = trades.signal_close.dt.strftime("%Y-%m")
    trades["matched"] = trades.matched.fillna(False).astype(bool)
    trades["net_bp"], trades["gross_bp"] = trades.net_return * 1e4, trades.gross_return * 1e4
    trades["hold_h"] = (trades.exit_time - trades.entry_time).dt.total_seconds() / 3600
    trades["cohort"] = np.where(trades.signal_close.ge(SPLIT), "later", np.where(trades.exit_time.lt(SPLIT), "earlier", "cross_split"))
    closed = trades[trades.status.eq("closed")].copy()
    assert len(trades) == 570 and len(closed) == 568
    assert not trades.duplicated(["symbol", "timeframe", "trade_key"]).any()
    np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
    np.testing.assert_allclose(closed.net_return / closed.initial_risk_frac, closed.net_r, rtol=1e-9)
    summary, per_coin, tails = [], [], []
    for tf, group in closed.groupby("timeframe"):
        for period in ("full", "earlier", "later", "cross_split"):
            q = group if period == "full" else group[group.cohort.eq(period)]
            if len(q): summary.append({"timeframe": tf, "period": period, **metrics(q, period == "earlier")})
        for symbol, q in group.groupby("symbol"):
            per_coin.append({"timeframe": tf, "symbol": symbol, **metrics(q)})
        for rank in ("net_r", "net_return"):
            order = group.sort_values(rank, ascending=False)
            for count in (0, 1, 2):
                q = order.iloc[count:]
                tails.append({"timeframe": tf, "rank": rank, "removed": count, **metrics(q),
                              "removed_keys": "|".join(order.iloc[:count].trade_key)})
    pd.DataFrame(summary).to_csv(EXP / "summary.csv", index=False)
    pd.DataFrame(per_coin).to_csv(EXP / "per_coin.csv", index=False)
    pd.DataFrame(tails).to_csv(EXP / "tail_sensitivity.csv", index=False)
    trades.to_csv(EXP / "trades_with_controls.csv", index=False)
    closed[closed.timeframe.eq("4h")].sort_values("net_r", ascending=False).to_csv(EXP / "four_hour_closed.csv", index=False)
    identity = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "declared": {str(p): sha(p) for p in declared},
                "source_receipt_sha256": sha(SOURCE / "completion.json"),
                "files": {str(p): sha(p) for p in sorted(EXP.glob("*.csv"))},
                "training_eligible": False, "production_eligible": False}
    (EXP / "receipt.json").write_text(json.dumps(identity, indent=2) + "\n")
    print(pd.DataFrame(summary)[["timeframe", "period", "n", "win_pct", "sum_r", "mean_r", "mean_bp", "pf_r", "matched_n", "random_r", "excess_r", "excess_mean_ci_low", "excess_mean_ci_high"]].to_string(index=False))


if __name__ == "__main__":
    main()

"""Stage-3 event-driven backtest over judgment-layer signals.

Design decisions (owner-approved 2026-07-08, recorded in HANDOFF.md):
- costs: spot taker 0.15%/side -> 0.30% round trip base case, swept over
  {0.20%, 0.30%, 0.40%}; no funding (spot);
- full-period simulation, but acceptance is judged ONLY on the window
  >= 2026-05-04; that window was consumed once by the 2b holdout eval,
  which is disclosed in the report (pre-window results are in-sample for
  the model and labelled as such);
- equal notional per trade (1 unit), one open position per symbol, at most
  MAX_CONCURRENT positions account-wide (ties broken by model score);
  account capital = MAX_CONCURRENT units, so drawdown is on the account;
- signal threshold = SCORE_QUANTILE of val-set scores, fixed ex ante from
  train/val only -- never tuned on the acceptance window.

Faithfulness: trades replay the triple-barrier outcomes already computed by
src.judgment.labeling (entry next-bar open, TP/SL intrabar, SL-first on
ambiguous bars, timeout at horizon), so the simulator adds only portfolio
constraints and costs on top of label-identical fills. Known simplification:
equity is marked at trade exits, not per-bar; intratrade excursion is
bounded by the SL barrier (~2 x ATR) per slot.

Usage:
    python3 -m src.backtest.run                     # expanded v2 dataset
    python3 -m src.backtest.run --data <csv> --tag p3
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.judgment.frozen import (
    DEFAULT_FROZEN_CONFIG,
    latest_artifact,
    read_dataset_before,
    score_with_artifact,
)
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import HOLDOUT_START, load_splits, train_model

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA = DEFAULT_FROZEN_CONFIG.dataset_path
OUTPUT_DIR = PROJECT_DIR / "analysis" / "output"

BAR = pd.Timedelta(minutes=15)
COST_SWEEP = (0.002, 0.003, 0.004)  # round-trip cost on notional
BASE_COST = 0.003
MAX_CONCURRENT = 10
SCORE_QUANTILE = 0.90


def build_signals(data_path: Path) -> tuple[pd.DataFrame, float]:
    """Score pre-holdout candidates and fix the threshold from val only."""
    artifact = latest_artifact(DEFAULT_FROZEN_CONFIG)
    if artifact is not None and data_path.resolve() == artifact.dataset_path.resolve():
        return score_with_artifact(artifact, end_before=HOLDOUT_START)

    train, val, _ = load_splits(data_path)
    model = train_model(train, val)
    val_scores = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    threshold = float(np.quantile(val_scores, SCORE_QUANTILE))

    full = read_dataset_before(data_path, end_before=HOLDOUT_START)
    full["score"] = model.predict(full[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    full["entry_time"] = full["signal_time"] + BAR              # next-bar open
    full["exit_time"] = full["entry_time"] + full["exit_offset"] * BAR
    return full.sort_values(["entry_time", "score"], ascending=[True, False]), threshold


def validation_start(data_path: Path) -> pd.Timestamp:
    artifact = latest_artifact(DEFAULT_FROZEN_CONFIG)
    if artifact is not None and data_path.resolve() == artifact.dataset_path.resolve():
        if artifact.val_start is None:
            raise ValueError(f"frozen metadata has no validation range: {artifact.metadata_path}")
        return artifact.val_start
    _, val, _ = load_splits(data_path)
    return pd.Timestamp(val["signal_time"].min())


def simulate(signals: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Event loop: per-symbol single position, global MAX_CONCURRENT cap,
    higher score wins when slots are contested at the same entry bar."""
    taken: list[dict] = []
    open_positions: list[tuple[pd.Timestamp, str]] = []  # (exit_time, symbol)
    eligible = signals[signals["score"] >= threshold]
    for entry_time, group in eligible.groupby("entry_time", sort=True):
        open_positions = [p for p in open_positions if p[0] > entry_time]
        held = {sym for _, sym in open_positions}
        for row in group.itertuples():
            if len(open_positions) >= MAX_CONCURRENT:
                break
            key = f"{row.source}:{row.symbol}"
            if key in held:
                continue
            open_positions.append((row.exit_time, key))
            held.add(key)
            taken.append({
                "source": row.source, "symbol": row.symbol,
                "entry_time": entry_time, "exit_time": row.exit_time,
                "score": row.score, "outcome": row.outcome,
                "gross_ret": row.realized_ret,
            })
    return pd.DataFrame(taken)


def window_metrics(trades: pd.DataFrame, cost_rt: float) -> dict:
    """PF / drawdown / totals for one set of trades at one round-trip cost.
    Equity marked at exits on account capital of MAX_CONCURRENT units."""
    if trades.empty:
        return {"n_trades": 0}
    t = trades.sort_values("exit_time")
    net = t["gross_ret"].to_numpy() - cost_rt
    equity = MAX_CONCURRENT + np.cumsum(net)
    peak = np.maximum.accumulate(np.concatenate([[MAX_CONCURRENT], equity]))[1:]
    wins, losses = net[net > 0].sum(), net[net < 0].sum()
    return {
        "n_trades": int(len(t)),
        "net_total_units": round(float(net.sum()), 4),
        "net_return_on_capital": round(float(net.sum() / MAX_CONCURRENT), 4),
        "mean_net_per_trade": round(float(net.mean()), 5),
        "win_rate": round(float((net > 0).mean()), 4),
        "profit_factor": round(float(wins / -losses), 3) if losses < 0 else float("inf"),
        "max_drawdown_pct": round(float(((peak - equity) / peak).max()), 4),
        "outcome_counts": t["outcome"].value_counts().to_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--tag", default="p3")
    args = parser.parse_args()

    signals, threshold = build_signals(args.data)
    trades = simulate(signals, threshold)
    val_start = validation_start(args.data)
    validation = trades[trades["entry_time"] >= val_start]
    insample = trades[trades["entry_time"] < val_start]

    results = {
        "dataset": str(args.data),
        "score_threshold_val_q90": round(threshold, 5),
        "n_candidates": int(len(signals)),
        "n_eligible": int((signals["score"] >= threshold).sum()),
        "config": {
            "max_concurrent": MAX_CONCURRENT, "base_cost_round_trip": BASE_COST,
            "validation_window_start": str(val_start),
            "score_scope": f"signal_time < {HOLDOUT_START}; holdout not scored",
        },
        "cost_sweep_validation_window": {
            f"{c:.3f}": window_metrics(validation, c) for c in COST_SWEEP
        },
        "insample_pre_window_base_cost": window_metrics(insample, BASE_COST),
        "full_period_base_cost": window_metrics(trades, BASE_COST),
    }
    base = results["cost_sweep_validation_window"][f"{BASE_COST:.3f}"]
    results["discovery_check_base_cost"] = {
        "net_positive": base.get("net_total_units", 0) > 0,
        "profit_factor_ge_1.3": base.get("profit_factor", 0) >= 1.3,
        "max_drawdown_le_20pct": base.get("max_drawdown_pct", 1) <= 0.20,
        "n_trades_ge_100": base.get("n_trades", 0) >= 100,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    trades.to_csv(OUTPUT_DIR / f"{args.tag}_trades.csv", index=False)
    (OUTPUT_DIR / f"{args.tag}_backtest.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

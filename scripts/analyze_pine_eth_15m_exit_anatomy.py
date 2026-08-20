#!/usr/bin/env python3
"""Diagnose frozen V9 exits without selecting new barrier parameters.

The audit reads only the already-consumed 2025-01 through 2026-02 V9 ledger
and the bounded OKX ETH-USDT-SWAP 15m prefix.  It classifies the existing
initial-protective, break-even, reverse, and period-end exits and measures the
favorable/adverse excursion completed *before* the exit bar.  The exit bar is
excluded from excursion statistics because 15m OHLC cannot order its path.

This is descriptive stop anatomy, not a TP/SL optimization.  The frozen
4x-ATR/3% initial stop, 1.5% break-even trigger, 0.1% lock, and 20 bp cost are
not changed.  Columns used are OHLC and the frozen trade ledger; no feature or
label looks beyond the trade's recorded exit.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.layers.l3_backtest.pine_allin_v7 import load_development_frame


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
OUTPUT_CSV = RESULTS / "exit_anatomy.csv"
OUTPUT_JSON = RESULTS / "exit_anatomy.json"
SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")


def _excursions_before_exit(
    frame: pd.DataFrame,
    *,
    entry_i: int,
    exit_i: int,
    entry_price: float,
    direction: str,
) -> tuple[float, float, int]:
    """Return MFE/MAE in bp using completed bars before the exit bar only."""

    if exit_i <= entry_i:
        return 0.0, 0.0, 0
    path = frame.iloc[entry_i:exit_i]
    if direction == "long":
        mfe = float(path["high"].max() / entry_price - 1.0)
        mae = float(1.0 - path["low"].min() / entry_price)
    elif direction == "short":
        mfe = float(1.0 - path["low"].min() / entry_price)
        mae = float(path["high"].max() / entry_price - 1.0)
    else:
        raise ValueError(f"unsupported direction: {direction}")
    return max(mfe, 0.0) * 10_000.0, max(mae, 0.0) * 10_000.0, len(path)


def _stop_subtype(row: pd.Series) -> str:
    if row["exit_reason"] != "stop":
        return str(row["exit_reason"])
    gross = float(row["gross_return"])
    if gross > 0.0005:
        return "break_even_locked_stop"
    return "initial_protective_stop"


def _aggregate(group: pd.DataFrame) -> dict[str, Any]:
    return {
        "trades": int(len(group)),
        "positive_trades": int(group["project_net_return"].gt(0.0).sum()),
        "net_bp_per_trade": float(group["project_net_return"].mean() * 10_000.0),
        "total_net_bp": float(group["project_net_return"].sum() * 10_000.0),
        "median_holding_bars": float(group["holding_bars"].median()),
        "median_mfe_before_exit_bp": float(group["mfe_before_exit_bp"].median()),
        "median_mae_before_exit_bp": float(group["mae_before_exit_bp"].median()),
    }


def main() -> None:
    config = json.loads((EXPERIMENT / "config.json").read_text(encoding="utf-8"))
    market = load_development_frame(
        PROJECT / config["instrument"]["data_path"],
        safe_end=SAFE_END,
        holdout_start=HOLDOUT_START,
    )
    market_times = pd.to_datetime(market["open_time"], utc=True)
    if market_times.ge(HOLDOUT_START).any() or market_times.max() >= SAFE_END:
        raise RuntimeError("exit anatomy market loader crossed a safety boundary")

    all_trades = pd.read_csv(
        RESULTS / "trades.csv",
        parse_dates=["signal_time", "entry_time", "exit_time"],
    )
    all_v9 = all_trades.loc[all_trades["variant"].eq("v9_locked")].copy()
    all_v9["stop_subtype"] = all_v9.apply(_stop_subtype, axis=1)
    trades = all_trades.loc[
        all_trades["variant"].eq("v9_locked")
        & all_trades["split"].eq("final_preholdout_2025_202602")
    ].copy()
    if len(trades) != 110:
        raise RuntimeError(f"expected 110 canonical V9 trades, found {len(trades)}")

    rows = []
    for row in trades.itertuples(index=False):
        mfe, mae, observed_bars = _excursions_before_exit(
            market,
            entry_i=int(row.entry_i),
            exit_i=int(row.exit_i),
            entry_price=float(row.entry_price),
            direction=str(row.direction),
        )
        rows.append(
            {
                "trade_id": row.trade_id,
                "direction": row.direction,
                "entry_time": row.entry_time,
                "exit_time": row.exit_time,
                "exit_reason": row.exit_reason,
                "stop_subtype": _stop_subtype(pd.Series(row._asdict())),
                "holding_bars": int(row.holding_bars),
                "observed_pre_exit_bars": observed_bars,
                "initial_stop_distance_bp": float(
                    row.initial_stop_distance / row.entry_price * 10_000.0
                ),
                "mfe_before_exit_bp": mfe,
                "mae_before_exit_bp": mae,
                "gross_return_bp": float(row.gross_return * 10_000.0),
                "project_net_return": float(row.project_net_return),
                "project_net_return_bp": float(row.project_net_return * 10_000.0),
            }
        )
    anatomy = pd.DataFrame(rows)
    anatomy.to_csv(OUTPUT_CSV, index=False)

    by_subtype = {
        str(name): _aggregate(group)
        for name, group in anatomy.groupby("stop_subtype", sort=True)
    }
    protective = anatomy.loc[anatomy["stop_subtype"].eq("initial_protective_stop")]
    reverse = anatomy.loc[anatomy["stop_subtype"].eq("reverse")]
    static_cost_aware = anatomy["project_net_return"].copy()
    static_cost_aware.loc[
        anatomy["stop_subtype"].eq("break_even_locked_stop")
    ] = 0.0
    static_sorted = static_cost_aware.sort_values(ascending=False)
    cross_period_be = []
    for split, group in all_v9.loc[
        all_v9["stop_subtype"].eq("break_even_locked_stop")
    ].groupby("split", sort=True):
        cross_period_be.append(
            {
                "split": str(split),
                "trades": int(len(group)),
                "mean_gross_bp": float(group["gross_return"].mean() * 10_000.0),
                "mean_project_net_bp": float(
                    group["project_net_return"].mean() * 10_000.0
                ),
            }
        )
    payload = {
        "audit": "frozen V9 exit anatomy",
        "period": ["2025-01-01T00:00:00Z", SAFE_END.isoformat()],
        "trades": int(len(anatomy)),
        "holdout_rows_read": int(market_times.ge(HOLDOUT_START).sum()),
        "final_preholdout_rows_read": int(market_times.ge(SAFE_END).sum()),
        "bar_path_semantics": (
            "MFE/MAE use entry through the bar before exit; exit-bar OHLC is excluded "
            "because its intrabar ordering is unknown"
        ),
        "barrier_parameters_changed": False,
        "barrier_search_performed": False,
        "break_even_cost_semantics": {
            "configured_lock_bp": 10.0,
            "frozen_round_trip_cost_bp": 20.0,
            "locked_stop_project_net_bp": -10.0,
            "cross_period_evidence": cross_period_be,
            "semantic_finding": (
                "The configured +10 bp lock is below the frozen 20 bp round-trip cost, "
                "so the so-called break-even stop is a guaranteed -10 bp project-net exit "
                "before slippage. Changing it is a barrier decision and was not attempted."
            ),
            "static_same_exit_accounting_only": {
                "current_net_bp_per_trade": float(
                    anatomy["project_net_return"].mean() * 10_000.0
                ),
                "if_all_locked_stops_were_exactly_zero_net_bp_per_trade": float(
                    static_cost_aware.mean() * 10_000.0
                ),
                "increment_bp_per_trade": float(
                    (static_cost_aware.mean() - anatomy["project_net_return"].mean())
                    * 10_000.0
                ),
                "mean_without_top1_bp": float(static_sorted.iloc[1:].mean() * 10_000.0),
                "warning": (
                    "This only replaces -10 bp ledger values with zero while keeping the "
                    "same exit times. It is not a replay and is an upper-bound accounting "
                    "illustration, because changing the stop can change later strategy state."
                ),
            },
        },
        "by_exit_subtype": by_subtype,
        "initial_protective_stop_diagnostics": {
            "trades": int(len(protective)),
            "fraction_never_reached_50bp_before_exit": float(
                protective["mfe_before_exit_bp"].lt(50.0).mean()
            ),
            "fraction_never_reached_100bp_before_exit": float(
                protective["mfe_before_exit_bp"].lt(100.0).mean()
            ),
            "fraction_reached_existing_be_trigger_before_exit": float(
                protective["mfe_before_exit_bp"].ge(150.0).mean()
            ),
            "median_initial_stop_distance_bp": float(
                protective["initial_stop_distance_bp"].median()
            ),
            "median_mfe_before_exit_bp": float(protective["mfe_before_exit_bp"].median()),
            "median_mae_before_exit_bp": float(protective["mae_before_exit_bp"].median()),
        },
        "reverse_exit_diagnostics": {
            "trades": int(len(reverse)),
            "positive_trades": int(reverse["project_net_return"].gt(0.0).sum()),
            "total_net_bp": float(reverse["project_net_return"].sum() * 10_000.0),
            "share_of_all_positive_net_bp": float(
                reverse["project_net_return"].clip(lower=0.0).sum()
                / anatomy["project_net_return"].clip(lower=0.0).sum()
            ),
        },
        "interpretation": (
            "The audit distinguishes entry-quality failures from the frozen stop mechanics. "
            "It does not authorize a wider stop, take-profit, or new break-even trigger."
        ),
    }
    if payload["holdout_rows_read"] or payload["final_preholdout_rows_read"]:
        raise RuntimeError("exit anatomy used data outside the bounded prefix")
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()

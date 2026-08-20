#!/usr/bin/env python3
"""Reconcile a normalized TradingView V9 trade export fail-closed.

The user must choose the exact venue, compile the canonical Pine on a 15m
chart, and normalize the Strategy Tester export according to the experiment
template.  This tool compares the export to the bounded canonical Python
ledger.  It never reads market data, never evaluates holdout, and cannot alter
production or forward eligibility.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
DEFAULT_OUTPUT = RESULTS / "tradingview_reconciliation.json"
SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
REQUIRED_COLUMNS = {
    "direction",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "commission_total",
    "net_profit",
}


def load_canonical() -> pd.DataFrame:
    trades = pd.read_csv(
        RESULTS / "trades.csv",
        parse_dates=["entry_time", "exit_time"],
    )
    selected = trades.loc[
        trades["variant"].eq("v9_locked")
        & trades["split"].eq("final_preholdout_2025_202602")
    ].copy()
    if len(selected) != 110:
        raise RuntimeError(f"canonical V9 ledger must contain 110 trades, found {len(selected)}")
    return selected


def load_normalized(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"normalized TradingView export missing columns: {missing}")
    out = frame[list(sorted(REQUIRED_COLUMNS))].copy()
    out["direction"] = out["direction"].astype(str).str.strip().str.lower()
    if not out["direction"].isin(["long", "short"]).all():
        raise ValueError("direction must contain only long/short")
    for column in ("entry_time", "exit_time"):
        out[column] = pd.to_datetime(out[column], utc=True, errors="raise")
    for column in ("entry_price", "exit_price", "commission_total", "net_profit"):
        out[column] = pd.to_numeric(out[column], errors="raise")
        if not np.isfinite(out[column].to_numpy(dtype=float)).all():
            raise ValueError(f"non-finite values in {column}")
    if out["entry_time"].ge(HOLDOUT_START).any() or out["exit_time"].ge(HOLDOUT_START).any():
        raise RuntimeError("normalized TradingView export reaches repository holdout")
    if out["entry_time"].ge(SAFE_END).any() or out["exit_time"].ge(SAFE_END).any():
        raise RuntimeError("normalized TradingView export exceeds the canonical period")
    return out


def reconcile_frames(
    canonical: pd.DataFrame,
    tradingview: pd.DataFrame,
    *,
    tick_tolerance: float = 0.01,
) -> dict[str, Any]:
    left = canonical.copy()
    right = tradingview.copy()
    for frame in (left, right):
        frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
        frame["direction"] = frame["direction"].astype(str).str.lower()
    joined = left.merge(
        right,
        on=["entry_time", "direction"],
        how="outer",
        suffixes=("_python", "_tradingview"),
        indicator=True,
    )
    common = joined.loc[joined["_merge"].eq("both")].copy()
    exit_matches = common["exit_time_python"].eq(common["exit_time_tradingview"])
    entry_error = (
        common["entry_price_python"] - common["entry_price_tradingview"]
    ).abs()
    exit_error = (
        common["exit_price_python"] - common["exit_price_tradingview"]
    ).abs()
    passed = bool(
        len(left) == len(right) == 110
        and len(common) == 110
        and exit_matches.all()
        and entry_error.le(tick_tolerance + 1e-12).all()
        and exit_error.le(tick_tolerance + 1e-12).all()
    )
    return {
        "status": "pass" if passed else "fail",
        "canonical_trades": int(len(left)),
        "tradingview_trades": int(len(right)),
        "entry_time_direction_matches": int(len(common)),
        "python_only_entries": int(joined["_merge"].eq("left_only").sum()),
        "tradingview_only_entries": int(joined["_merge"].eq("right_only").sum()),
        "exit_time_matches": int(exit_matches.sum()),
        "maximum_entry_price_error": float(entry_error.max()) if len(common) else None,
        "maximum_exit_price_error": float(exit_error.max()) if len(common) else None,
        "tick_tolerance": tick_tolerance,
        "fee_and_net_profit_columns_present": True,
        "fee_accounting_manually_reviewed": False,
        "tradingview_parity_passed": passed,
        "production_eligible": False,
        "forward_eligible": False,
        "holdout_rows_read": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    canonical = load_canonical()
    tradingview = load_normalized(args.input)
    payload = reconcile_frames(canonical, tradingview)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

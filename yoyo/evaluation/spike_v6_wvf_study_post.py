"""Canonical post-processing for committed SPIKE V6/WVF raw ledgers.

This preserves the runner's raw files and applies the documented half-open fold
clock to signal denominators.  It does not recreate indicators or oracle state.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


FOLD_END = {"development": pd.Timestamp("2025-09-10T00:00:00Z"),
            "validation": pd.Timestamp("2026-09-10T00:00:00Z")}


def _parts(path: Path) -> tuple[str, int, str, str]:
    symbol, minutes, fold, variant, _ = path.name.replace(".csv.gz", "").split("_")
    return symbol, int(minutes.removesuffix("m")), fold, variant


def canonicalize(raw: Path, output: Path) -> None:
    """Write strict [start,end) signals and side-separated closed-trade stats."""
    output.mkdir(parents=True, exist_ok=False)
    signals: list[pd.DataFrame] = []
    trades: list[pd.DataFrame] = []
    for path in raw.glob("*_signals.csv.gz"):
        symbol, minutes, fold, variant = _parts(path)
        table = pd.read_csv(path, parse_dates=["signal_confirm_time"])
        table = table.loc[table.signal_confirm_time < FOLD_END[fold]].copy()
        table["symbol"], table["timeframe_min"], table["fold"], table["variant"] = symbol, minutes, fold, variant
        signals.append(table)
    for path in raw.glob("*_trades.csv.gz"):
        symbol, minutes, fold, variant = _parts(path)
        table = pd.read_csv(path)
        if {"side", "censored", "net_r", "net_return"}.issubset(table):
            table = table.loc[~table.censored.astype(bool)].copy()
            table["symbol"], table["timeframe_min"], table["fold"], table["variant"] = symbol, minutes, fold, variant
            trades.append(table)
    signal = pd.concat(signals, ignore_index=True)
    trade = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
    signal.to_csv(output / "canonical_signals.csv.gz", index=False, compression="gzip")
    trade.to_csv(output / "canonical_closed_trades.csv.gz", index=False, compression="gzip")
    signal_summary = signal.groupby(["fold", "variant", "side"], dropna=False).agg(
        raw_signals=("side", "size"), admitted=("admitted_for_entry", "sum")).reset_index()
    trade_summary = (trade.groupby(["fold", "variant", "side"], dropna=False).agg(
        executed_trades=("side", "size"), net_r=("net_r", "sum"), net_return=("net_return", "sum"),
        win_rate=("net_return", lambda x: x.gt(0).mean())) .reset_index() if len(trade) else pd.DataFrame())
    signal_summary.to_csv(output / "canonical_signal_summary.csv", index=False)
    trade_summary.to_csv(output / "canonical_trade_summary_by_side.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    canonicalize(args.raw, args.output)

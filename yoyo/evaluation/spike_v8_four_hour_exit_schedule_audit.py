"""Derive auditable four-hour exit schedules from a completed serial ledger.

This is a CSV-only post-run audit, not the engine's original event log.  It
does not read candles or alter the completed full replay.  A scheduled exit can
be superseded at its next open by the preserved stop, gap, or raw V6 reverse.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

POLICY = "four_hour_mfe_lt1_close_nonpositive_next_open"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(source: Path, output: Path) -> dict[str, object]:
    """Materialize only triggered schedule rows from a receipt-bound ledger."""
    receipt_path, trades_path = source / "receipt.json", source / "serial_trades.csv.gz"
    source_receipt = json.loads(receipt_path.read_text())
    if source_receipt.get("status") != "complete" or source_receipt.get("streams") != 3531:
        raise ValueError("source full replay is incomplete")
    expected = source_receipt.get("files", {}).get(trades_path.name)
    if not expected or sha256(trades_path) != expected:
        raise ValueError("source serial ledger SHA drift")
    if output.exists() and any(output.iterdir()):
        raise ValueError("audit output exists; preserve it and select a new path")
    output.mkdir(parents=True, exist_ok=True)
    use = ["trade_id", "stream_key", "venue", "symbol", "asset", "timeframe_min", "signal_i", "entry_i", "side",
           "entry_time", "exit_time", "exit_reason", "censored", "net_r", "four_hour_checked", "four_hour_triggered",
           "four_hour_check_bar_open", "four_hour_mfe_r", "four_hour_close_gross_r", "policy"]
    all_trades = pd.read_csv(trades_path, usecols=use)
    trades = all_trades.loc[all_trades.policy.eq(POLICY)].copy()
    trades["four_hour_checked"] = trades.four_hour_checked.astype(str).str.lower().map({"true": True, "false": False})
    trades["four_hour_triggered"] = trades.four_hour_triggered.astype(str).str.lower().map({"true": True, "false": False})
    if trades[["four_hour_checked", "four_hour_triggered"]].isna().any().any():
        raise ValueError("unknown persisted four-hour boolean")
    triggered = trades.loc[trades.four_hour_triggered].copy()
    if not triggered.four_hour_checked.all() or triggered.four_hour_check_bar_open.isna().any() or triggered.trade_id.duplicated().any():
        raise ValueError("trigger schedule identity/timestamp contract failed")
    for name in ("entry_time", "exit_time", "four_hour_check_bar_open"):
        triggered[name] = pd.to_datetime(triggered[name], utc=True)
    triggered["bar_close_available_at"] = triggered.four_hour_check_bar_open + pd.to_timedelta(triggered.timeframe_min, unit="m")
    triggered["scheduled_next_open"] = triggered.bar_close_available_at
    triggered["schedule_executed"] = triggered.exit_reason.eq(POLICY)
    triggered["superseded_before_scheduled_exit"] = ~triggered.schedule_executed
    triggered["superseding_exit_reason"] = triggered.exit_reason.where(triggered.superseded_before_scheduled_exit, pd.NA)
    triggered["derivation"] = "CSV-only from serial trade state; not original engine event log"
    ordered = ["trade_id", "stream_key", "venue", "symbol", "asset", "timeframe_min", "signal_i", "entry_i", "side",
               "entry_time", "four_hour_check_bar_open", "bar_close_available_at", "scheduled_next_open", "four_hour_mfe_r",
               "four_hour_close_gross_r", "exit_time", "exit_reason", "censored", "net_r", "schedule_executed",
               "superseded_before_scheduled_exit", "superseding_exit_reason", "derivation"]
    events = triggered.loc[:, ordered].sort_values(["scheduled_next_open", "trade_id"], kind="mergesort")
    events.to_csv(output / "schedule_events.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    rec = {"status": "complete", "kind": "csv_only_derived_schedule_events_not_engine_log", "source_directory": str(source),
           "source_serial_trades_sha256": expected, "source_receipt_sha256": sha256(receipt_path), "triggered_events": len(events),
           "executed_at_scheduled_open": int(events.schedule_executed.sum()), "superseded_before_scheduled_exit": int(events.superseded_before_scheduled_exit.sum()),
           "superseding_exit_reasons": events.superseding_exit_reason.value_counts(dropna=True).to_dict(),
           "files": {"schedule_events.csv.gz": sha256(output / "schedule_events.csv.gz")}}
    (output / "receipt.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return rec


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); print(json.dumps(run(args.source, args.output), ensure_ascii=False, indent=2))

"""Describe the frozen, coverage-limited SPIKE V1 executable ledger.

This reader changes no signal, entry, exit, or cost assumption. It separates
realized rows from right-censored observations and reports equal-event returns
only as descriptive partial-coverage statistics, never as account equity.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v1_twoyear_allmarkets import METHOD_VERSION, PINE_SHA, RESULTS, SOURCE_SHA256

V1_TIMEFRAME_MINUTES = (30, 60, 240)


def _v1_only(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep the frozen V1 30m/1H/4H contract out of legacy 1D ledger rows."""
    return frame.loc[frame["timeframe_min"].isin(V1_TIMEFRAME_MINUTES)].copy()


def _as_censored(values: pd.Series) -> pd.Series:
    return values.map(lambda value: bool(value) if isinstance(value, (bool, np.bool_))
                      else isinstance(value, str) and value.strip().lower() == "true")


def _event_summary(frame: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    rows = []
    for key, part in frame.groupby(groups, dropna=False):
        censored = _as_censored(part["censored"])
        realized = part.loc[~censored].sort_values(["exit_time", "entry_time", "event_id"], kind="mergesort")
        values = realized["net_return"].astype(float)
        wins, losses = values[values > 0].sum(), values[values < 0].sum()
        sequence = np.r_[0.0, values.cumsum().to_numpy(float)]
        drawdown = float(np.min(sequence - np.maximum.accumulate(sequence))) if len(values) else np.nan
        label = key if isinstance(key, tuple) else (key,)
        rows.append(dict(zip(groups, label), signal_rows=len(part), realized_rows=len(realized),
                         censored_rows=int(censored.sum()), positive_realized=int((values > 0).sum()),
                         positive_realized_rate=float((values > 0).mean()) if len(values) else np.nan,
                         realized_net_return_sum=float(values.sum()) if len(values) else np.nan,
                         realized_mean_net_return=float(values.mean()) if len(values) else np.nan,
                         realized_profit_factor=float(wins / abs(losses)) if losses else np.nan,
                         event_sequence_drawdown=drawdown))
    return pd.DataFrame(rows)


def build() -> dict:
    ledger_path = RESULTS / "covered_trade_ledger.csv.gz"
    coverage_path = RESULTS / "coverage_limited.csv"
    manifest_path = RESULTS / "coverage_progress.json"
    ledger, coverage = _v1_only(pd.read_csv(ledger_path)), _v1_only(pd.read_csv(coverage_path))
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("method_version") != METHOD_VERSION:
        raise ValueError("coverage ledger method version is not the executable-risk build")
    if manifest.get("source_sha256") != SOURCE_SHA256 or manifest.get("pine_sha256") != PINE_SHA:
        raise ValueError("coverage ledger source identity mismatch")
    for column in ("entry_time", "exit_time"):
        ledger[column] = pd.to_datetime(ledger[column], utc=True)
    ledger["entry_year"] = ledger["entry_time"].dt.year
    by_timeframe = _event_summary(ledger, ["venue", "timeframe_min"])
    by_year = _event_summary(ledger, ["entry_year", "venue", "timeframe_min"])
    censored = _as_censored(ledger["censored"])
    realized = ledger.loc[~censored].copy()
    exits = (realized.groupby(["exit_reason"], dropna=False)
             .agg(realized_rows=("event_id", "size"), positive_realized=("net_return", lambda x: int((x > 0).sum())),
                  net_return_sum=("net_return", "sum"), mean_net_return=("net_return", "mean"),
                  median_mfe_return=("mfe_return", "median"), median_mae_return=("mae_return", "median"))
             .reset_index())
    outputs = {
        "by_timeframe": RESULTS / "coverage_diagnostics_by_timeframe.csv",
        "by_year": RESULTS / "coverage_diagnostics_by_entry_year.csv",
        "exit_reasons": RESULTS / "coverage_diagnostics_by_exit_reason.csv",
    }
    by_timeframe.to_csv(outputs["by_timeframe"], index=False)
    by_year.to_csv(outputs["by_year"], index=False)
    exits.to_csv(outputs["exit_reasons"], index=False)
    receipt = {
        "method_version": METHOD_VERSION, "source_sha256": SOURCE_SHA256, "pine_sha256": PINE_SHA,
        "coverage_statuses": coverage.status.value_counts().to_dict(), "signal_rows": len(ledger),
        "realized_rows": int((~censored).sum()), "censored_rows": int(censored.sum()),
        "outputs": {name: str(path) for name, path in outputs.items()},
        "interpretation": "Coverage-limited equal-event descriptions; not account returns, full-market results, or a strategy verdict.",
    }
    (RESULTS / "coverage_diagnostics_manifest.json").write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return receipt


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=True))

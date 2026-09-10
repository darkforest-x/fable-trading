"""Describe the frozen, coverage-limited V1 ledger without replaying or fetching data.

Inputs are the existing ``evaluate-covered`` outputs.  The summaries separate
realized exits from right-censored rows and label whether a joined evaluated
cell's frozen source reaches the fixed two-year start.  They are descriptive
receipts, not account performance, a matched-control comparison, or a signal
selection step.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v1_twoyear_allmarkets import (
    METHOD_VERSION,
    RESULTS,
    SOURCE_SHA256,
    START,
    _is_censored,
    stamp,
)

TIMEFRAMES = (30, 60, 240)
TOP_ROWS = 20


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_scope(detail: Any) -> str:
    """Classify an evaluated receipt by its frozen source-window start only."""
    text = str(detail or "")
    first = text.split("..", 1)[0]
    try:
        first_bar = pd.Timestamp(first)
        if first_bar.tzinfo is None:
            first_bar = first_bar.tz_localize("UTC")
        else:
            first_bar = first_bar.tz_convert("UTC")
    except (TypeError, ValueError):
        return "unknown_source_window"
    return "full_to_frozen_start" if first_bar <= START else "partial_after_frozen_start"


def _summary(frame: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    """Summarize independent signal exits, excluding censored outcomes from PnL fields."""
    rows = []
    for key, part in frame.groupby(groups, dropna=False, sort=True):
        realized = part.loc[~part["censored"]].copy()
        returns = realized["net_return"].astype(float)
        positives, negatives = returns[returns > 0], returns[returns < 0]
        label = key if isinstance(key, tuple) else (key,)
        row = dict(zip(groups, label))
        row.update(
            signal_rows=int(len(part)),
            realized_rows=int(len(realized)),
            censored_rows=int(part["censored"].sum()),
            positive_realized=int((returns > 0).sum()),
            nonpositive_realized=int((returns <= 0).sum()),
            realized_win_rate=float((returns > 0).mean()) if len(returns) else np.nan,
            realized_net_return=float(returns.sum()) if len(returns) else np.nan,
            realized_net_r=float(realized["net_r"].astype(float).sum(min_count=1)) if len(realized) else np.nan,
            realized_profit_factor=float(positives.sum() / abs(negatives.sum())) if len(negatives) else np.nan,
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _event_view(frame: pd.DataFrame) -> pd.DataFrame:
    fields = [
        "event_id", "venue", "symbol", "timeframe_min", "source_scope",
        "signal_bar_open", "entry_time", "exit_time", "exit_reason",
        "entry_price", "exit_price", "net_return", "net_r",
    ]
    return frame.loc[:, [field for field in fields if field in frame]].copy()


def build(results: Path = RESULTS) -> dict[str, Any]:
    """Write a reproducible descriptive snapshot from already frozen artifacts."""
    ledger_path = results / "covered_trade_ledger.csv.gz"
    coverage_path = results / "coverage_limited.csv"
    progress_path = results / "coverage_progress.json"
    if not all(path.exists() for path in (ledger_path, coverage_path, progress_path)):
        raise FileNotFoundError("run evaluate-covered before coverage summary")

    ledger = pd.read_csv(ledger_path)
    coverage = pd.read_csv(coverage_path)
    ledger = ledger.loc[ledger["timeframe_min"].isin(TIMEFRAMES)].copy()
    coverage = coverage.loc[coverage["timeframe_min"].isin(TIMEFRAMES)].copy()
    coverage = coverage.loc[coverage["status"].eq("evaluated"), ["venue", "symbol", "timeframe_min", "detail"]].copy()
    if coverage.duplicated(["venue", "symbol", "timeframe_min"]).any():
        raise ValueError("duplicate evaluated coverage identity")
    coverage["source_scope"] = coverage["detail"].map(_source_scope)
    merged = ledger.merge(coverage.drop(columns="detail"), on=["venue", "symbol", "timeframe_min"], how="left", validate="many_to_one")
    if merged["source_scope"].isna().any():
        raise ValueError("ledger row lacks evaluated coverage identity")
    merged["censored"] = merged["censored"].map(_is_censored)

    venue_timeframe = _summary(merged, ["venue", "timeframe_min"])
    scope_timeframe = _summary(merged, ["source_scope", "timeframe_min"])
    reasons = (merged.loc[~merged["censored"]].groupby(["exit_reason"], dropna=False).size()
               .rename("realized_rows").reset_index().sort_values("realized_rows", ascending=False))
    realized = merged.loc[~merged["censored"]].copy()
    winners = _event_view(realized.nlargest(TOP_ROWS, "net_return"))
    losses = _event_view(realized.nsmallest(TOP_ROWS, "net_return"))

    outputs = {
        "venue_timeframe": results / "coverage_descriptive_by_venue_timeframe.csv",
        "scope_timeframe": results / "coverage_descriptive_by_source_scope_timeframe.csv",
        "exit_reasons": results / "coverage_descriptive_exit_reasons.csv",
        "top_winners": results / "coverage_descriptive_top_realized_winners.csv",
        "top_losses": results / "coverage_descriptive_top_realized_losses.csv",
    }
    venue_timeframe.to_csv(outputs["venue_timeframe"], index=False)
    scope_timeframe.to_csv(outputs["scope_timeframe"], index=False)
    reasons.to_csv(outputs["exit_reasons"], index=False)
    winners.to_csv(outputs["top_winners"], index=False)
    losses.to_csv(outputs["top_losses"], index=False)

    progress = json.loads(progress_path.read_text())
    receipt = {
        "generated_at": stamp(),
        "method_version": METHOD_VERSION,
        "source_sha256": SOURCE_SHA256,
        "source_artifacts": {str(path): _digest(path) for path in (ledger_path, coverage_path, progress_path)},
        "timeframes": list(TIMEFRAMES),
        "signal_rows": int(len(merged)),
        "realized_rows": int((~merged["censored"]).sum()),
        "censored_rows": int(merged["censored"].sum()),
        "coverage_progress_generated_at": progress.get("generated_at"),
        "coverage_statuses": progress.get("statuses"),
        "interpretation": "Coverage-limited independent-event descriptions only; no account return, matched control, or strategy-edge claim.",
        "outputs": {name: str(path) for name, path in outputs.items()},
    }
    receipt_path = results / "coverage_descriptive_snapshot_receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return receipt


def main() -> None:
    print(json.dumps(build(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

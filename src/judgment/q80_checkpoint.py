"""Build a fixed-time q80 shadow checkpoint without selecting a threshold."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd


class Q80CheckpointError(RuntimeError):
    """Raised when a shadow checkpoint cannot be reconciled safely."""


def _economics(frame: pd.DataFrame, *, cost: float) -> dict[str, int | float | None]:
    gross = pd.to_numeric(frame["realized_ret"], errors="coerce").dropna().to_numpy(dtype=float)
    net = gross - cost
    gains = float(net[net > 0].sum())
    losses = float(-net[net < 0].sum())
    return {
        "trades": int(len(gross)),
        "tp": int((frame["outcome"] == "tp").sum()),
        "sl": int((frame["outcome"] == "sl").sum()),
        "gross_mean_per_trade": float(gross.mean()) if len(gross) else None,
        "net_mean_per_trade": float(net.mean()) if len(net) else None,
        "profit_factor": gains / losses if losses > 0 else None,
    }


def build_q80_checkpoint(
    latest: Mapping[str, Any],
    ledger: pd.DataFrame,
    *,
    minimum_hours: float = 24.0,
    round_trip_cost: float = 0.002,
) -> dict[str, Any]:
    """Freeze one same-window funnel and closed-trade diagnostic at the time gate."""
    funnel = latest.get("funnel")
    shadow = latest.get("q80_shadow")
    if not isinstance(funnel, Mapping) or not isinstance(shadow, Mapping):
        raise Q80CheckpointError("latest q80 payload requires funnel and q80_shadow mappings")
    start = pd.Timestamp(funnel.get("start_time"))
    latest_bar = pd.Timestamp(funnel.get("latest_bar_time"))
    if start.tzinfo is None or latest_bar.tzinfo is None:
        raise Q80CheckpointError("q80 checkpoint timestamps must be timezone-aware")
    elapsed_hours = (latest_bar - start).total_seconds() / 3600.0
    if elapsed_hours < 0:
        raise Q80CheckpointError("latest q80 bar precedes the forward start")

    required = {"source", "symbol", "signal_time", "status", "score", "outcome", "realized_ret"}
    missing = sorted(required - set(ledger.columns))
    if missing:
        raise Q80CheckpointError(f"q80 ledger missing columns: {missing}")
    duplicate_rows = int(ledger.duplicated(["source", "symbol", "signal_time"]).sum())
    if duplicate_rows:
        raise Q80CheckpointError(f"q80 ledger contains {duplicate_rows} duplicate signal rows")

    q90_threshold = float(funnel["q90_threshold"])
    q80_threshold = float(funnel["q80_threshold"])
    scores = pd.to_numeric(ledger["score"], errors="coerce")
    closed = ledger[(ledger["status"] == "closed") & ledger["realized_ret"].notna()].copy()
    closed_scores = pd.to_numeric(closed["score"], errors="coerce")
    q90 = closed[closed_scores >= q90_threshold]
    q80_only = closed[(closed_scores >= q80_threshold) & (closed_scores < q90_threshold)]
    score_summary = funnel.get("score_summary")
    if not isinstance(score_summary, Mapping):
        raise Q80CheckpointError("latest q80 payload requires a score_summary mapping")
    return {
        "status": "ready" if elapsed_hours >= minimum_hours else "not_ready",
        "holdout_used": False,
        "mainline_changed": False,
        "start_time": str(start),
        "latest_bar_time": str(latest_bar),
        "elapsed_hours": elapsed_hours,
        "minimum_hours": minimum_hours,
        "round_trip_cost": round_trip_cost,
        "funnel": dict(score_summary),
        "ledger": {
            "total_rows": int(len(ledger)),
            "closed_rows": int((ledger["status"] == "closed").sum()),
            "open_rows": int((ledger["status"] != "closed").sum()),
            "finite_scores": int(np.isfinite(scores).sum()),
            "duplicate_rows": 0,
        },
        "closed_economics": {
            "q90_score_range": _economics(q90, cost=round_trip_cost),
            "q80_only": _economics(q80_only, cost=round_trip_cost),
            "all_q80": _economics(closed, cost=round_trip_cost),
        },
        "warning": "This short forward diagnostic is not a profitability or threshold-selection result.",
    }

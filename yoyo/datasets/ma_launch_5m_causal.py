"""Causal timing contract for the 5-minute MA-launch diagnostic dataset.

Inputs use only OHLC rows through ``decision_i``.  The decision is made after
that bar closes, entry uses its close, and TP/SL resolution starts at
``decision_i + 1``.  Labels may inspect the following 144 bars; rendered model
inputs may not.  This module deliberately contains no model or registry logic
so label generation and economic evaluation can import the same semantics.

Required market columns are ``open_time``, ``open``, ``high``, ``low`` and
``close``.  ATR14 uses the current and previous rows only, with a 14-row warmup.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from yoyo.contracts.outcomes import BarrierResolution, resolve_barrier_after_close

BAR_MINUTES = 5
ENTRY_LAG_BARS = 2
HORIZON_BARS = 144
PRE_CORE_BARS = 12
SPLIT_CUTOFF = pd.Timestamp("2025-12-01T00:00:00Z")
PURGE_BARS = 450
PURGE = pd.Timedelta(minutes=BAR_MINUTES * PURGE_BARS)
TP_ATR = 5.0
SL_ATR = 2.0
ROUND_TRIP_COST = 0.002
ATR_PCT_FLOOR = 1e-4
CONTRACT_VERSION = "ma_launch_5m_close_entry_next_bar_v2"


@dataclass(frozen=True)
class CausalTiming:
    """Indices that must remain identical across render, label and evaluation."""

    core_end_i: int
    decision_i: int
    visible_end_i: int
    outcome_start_i: int


def timing_from_core_end(core_end_i: int) -> CausalTiming:
    """Derive the fixed close-entry timing from one source-frame core end."""
    core = int(core_end_i)
    decision = core + ENTRY_LAG_BARS
    return CausalTiming(
        core_end_i=core,
        decision_i=decision,
        visible_end_i=decision,
        outcome_start_i=decision + 1,
    )


def split_from_decision_at(value: object) -> str | None:
    """Return the frozen time split, or ``None`` inside the purge band."""
    decision_at = pd.Timestamp(value)
    if decision_at.tzinfo is None:
        raise ValueError("decision_at must be timezone-aware")
    if abs(decision_at - SPLIT_CUTOFF) < PURGE:
        return None
    return "train" if decision_at < SPLIT_CUTOFF else "val"


def atr_series(frame: pd.DataFrame) -> pd.Series:
    """Return the frozen causal ATR14 used by the 5-minute diagnostic."""
    previous_close = pd.to_numeric(frame["close"], errors="coerce").shift(1)
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    true_range.iloc[0] = np.nan
    atr = true_range.ewm(alpha=1.0 / 14, adjust=False, ignore_na=True).mean()
    atr.iloc[:14] = np.nan
    return atr


def resolve_causal_trade(
    frame: pd.DataFrame,
    *,
    decision_i: int,
    side: str,
    horizon_bars: int = HORIZON_BARS,
) -> BarrierResolution:
    """Resolve one trade under the shared close-entry/next-bar contract."""
    index = int(decision_i)
    atr = float(frame["atr14"].iloc[index])
    normalized_side = str(side).lower()
    return resolve_barrier_after_close(
        frame,
        side=normalized_side,
        decision_i=index,
        atr=atr,
        tp_atr_mult=TP_ATR,
        sl_atr_mult=SL_ATR,
        horizon_bars=int(horizon_bars),
        same_bar_policy="conservative_sl",
        gap_policy="barrier_price",
        return_convention="linear_long" if normalized_side == "long" else "linear_short",
        allow_partial=False,
        bar_duration=pd.Timedelta(minutes=BAR_MINUTES),
    )


def net_atr_from_resolution(
    resolution: BarrierResolution,
    *,
    entry_atr: float,
) -> float:
    """Convert the canonical gross return to ATR units after frozen costs."""
    if resolution.gross_ret is None:
        raise ValueError("a non-closed resolution has no net ATR return")
    unit = float(entry_atr) / float(resolution.entry_price)
    if not np.isfinite(unit) or unit < ATR_PCT_FLOOR:
        raise ValueError("entry ATR/price is below the tradeable floor")
    return float(resolution.gross_ret) / unit - ROUND_TRIP_COST / unit


def assert_manifest_timing(row: dict[str, object]) -> None:
    """Fail closed when a persisted row drifts from the causal contract."""
    core_end = int(row["core_end_i"])
    expected = timing_from_core_end(core_end)
    observed = {
        "decision_i": int(row["decision_i"]),
        "visible_end_i": int(row["visible_end_i"]),
        "outcome_start_i": int(row["outcome_start_i"]),
        "window_end_i": int(row["window_end_i"]),
    }
    required = {
        "decision_i": expected.decision_i,
        "visible_end_i": expected.visible_end_i,
        "outcome_start_i": expected.outcome_start_i,
        "window_end_i": expected.visible_end_i,
    }
    if observed != required:
        raise ValueError(f"causal timing mismatch: expected {required}, got {observed}")
    if row.get("entry_price_source") != "decision_close":
        raise ValueError("entry_price_source must be decision_close")
    if row.get("outcome_contract") != CONTRACT_VERSION:
        raise ValueError(f"outcome_contract must be {CONTRACT_VERSION}")

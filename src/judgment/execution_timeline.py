"""Causal signal/decision/fill adapters for paper and broker evidence.

The source columns used here are ``open_time`` and ``open`` for an explicit
paper convention, plus ``fill_at`` and ``fill_px`` from a broker ledger event.
The paper fill is the first bar whose open is strictly after ``decision_at``;
the historical next bar after the signal is deliberately irrelevant.  Broker
fills never fall back to a mark, signal close, requested price, or order time.

Barrier resolution uses the signal bar's ``atr14`` and starts at the paper fill
bar (an open print) or the first complete bar at/after a broker fill.  A partial
OHLC bar containing a mid-bar broker fill is excluded because its high/low may
have occurred before the fill.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from src.judgment.outcomes import BarrierResolution, OutcomeContractError, resolve_barrier_outcome
from src.judgment.protocol import StrategyProtocol


class FillEvidenceError(ValueError):
    """A purported fill is missing causal, source-specific evidence."""


@dataclass(frozen=True)
class EntryFill:
    source: str
    fill_at: str
    fill_px: float
    bar_i: int | None = None


def paper_fill_after_decision(frame: pd.DataFrame, decision_at: object) -> EntryFill | None:
    """Return the first future bar open, strictly after the decision timestamp."""
    decision = _utc_timestamp(decision_at, "decision_at")
    times = pd.to_datetime(frame["open_time"], errors="coerce", utc=True)
    eligible = np.flatnonzero((times > decision).to_numpy())
    if len(eligible) == 0:
        return None
    bar_i = int(eligible[0])
    price = _positive_price(frame["open"].iloc[bar_i], "paper fill open")
    return EntryFill("paper_after_decision", str(times.iloc[bar_i]), price, bar_i)


def broker_fill_from_ledger(event: Mapping[str, object]) -> EntryFill:
    """Build fill evidence only from explicit ledger reconciliation fields."""
    if str(event.get("fill_source", "")).strip() != "broker_ledger":
        raise FillEvidenceError("broker fill requires fill_source=broker_ledger")
    fill_at = _utc_timestamp(event.get("fill_at"), "fill_at")
    fill_px = _positive_price(event.get("fill_px"), "fill_px")
    return EntryFill("broker_ledger", str(fill_at), fill_px, None)


def resolve_outcome_after_fill(
    frame: pd.DataFrame,
    *,
    signal_i: int,
    fill: EntryFill,
    protocol: StrategyProtocol,
    allow_partial: bool = True,
) -> BarrierResolution:
    """Resolve one protocol outcome without inspecting any pre-fill price path."""
    try:
        atr = float(frame["atr14"].iloc[int(signal_i)])
    except (IndexError, TypeError, ValueError) as exc:
        raise OutcomeContractError("signal ATR is unavailable") from exc
    entry_i = _first_safe_bar_i(frame, fill)
    return resolve_barrier_outcome(
        frame,
        side=protocol.side,
        entry_i=entry_i,
        entry_price=fill.fill_px,
        atr=atr,
        tp_atr_mult=protocol.tp_atr_mult,
        sl_atr_mult=protocol.sl_atr_mult,
        horizon_bars=protocol.horizon_bars,
        same_bar_policy=protocol.same_bar_policy,
        gap_policy=protocol.gap_policy,
        return_convention=protocol.return_convention,
        allow_partial=allow_partial,
    )


def _first_safe_bar_i(frame: pd.DataFrame, fill: EntryFill) -> int:
    times = pd.to_datetime(frame["open_time"], errors="coerce", utc=True)
    if fill.source == "paper_after_decision":
        if fill.bar_i is None or fill.bar_i < 0 or fill.bar_i >= len(frame):
            raise FillEvidenceError("paper fill must carry its exact bar index")
        if times.iloc[fill.bar_i] != _utc_timestamp(fill.fill_at, "fill_at"):
            raise FillEvidenceError("paper fill bar index/time mismatch")
        return int(fill.bar_i)
    if fill.source != "broker_ledger":
        raise FillEvidenceError(f"unsupported fill source={fill.source!r}")
    fill_at = _utc_timestamp(fill.fill_at, "fill_at")
    eligible = np.flatnonzero((times >= fill_at).to_numpy())
    if len(eligible) == 0:
        raise OutcomeContractError("no complete OHLC bar is available after broker fill")
    return int(eligible[0])


def _utc_timestamp(value: object, field: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise FillEvidenceError(f"{field} is not a timestamp") from exc
    if pd.isna(timestamp):
        raise FillEvidenceError(f"{field} is missing")
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _positive_price(value: object, field: str) -> float:
    try:
        price = float(value)
    except (TypeError, ValueError) as exc:
        raise FillEvidenceError(f"{field} is not numeric") from exc
    if not np.isfinite(price) or price <= 0:
        raise FillEvidenceError(f"{field} must be finite and positive")
    return price


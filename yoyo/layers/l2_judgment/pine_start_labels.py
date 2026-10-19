"""Causal-feature / future-path labels for the frozen ETH 15m Pine V9.

The signal and all model features are fixed at confirmed 15-minute bar ``t``.
Entry is the open of ``t+1``.  The future-only label asks whether price reaches
``+/- target_percent`` before the frozen V9 initial protective stop or the next
guarded opposite V9 signal fills at its following bar open.

Columns read from the 15-minute frame are ``open_time/open/high/low/close/atr``.
The stop uses only signal-bar ``close`` and ``atr``; future OHLC is used solely
for the outcome label.  Ordered lower-timeframe bars may resolve a 15-minute
bar that touches both barriers.  If both barriers remain possible inside one
lowest-resolution bar, the outcome is explicitly ambiguous rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

import numpy as np
import pandas as pd


Side = Literal["long", "short"]


@dataclass(frozen=True)
class PineStartOutcome:
    """One automatically resolved, ambiguous, or right-censored path label."""

    status: Literal["resolved", "ambiguous", "censored"]
    label: int | None
    outcome: str
    entry_i: int
    entry_time: pd.Timestamp
    entry_price: float
    target_price: float
    initial_stop_price: float
    initial_stop_distance: float
    opposite_signal_i: int | None
    opposite_entry_time: pd.Timestamp | None
    event_i: int | None
    event_time: pd.Timestamp | None
    label_available_at: pd.Timestamp
    bars_to_event: int | None
    resolution: str


@dataclass(frozen=True)
class _Touch:
    outcome: Literal["target", "initial_stop", "ambiguous"]
    event_time: pd.Timestamp
    resolution: str


def rounded_initial_stop_distance(
    *,
    signal_close: float,
    signal_atr: float,
    atr_mult: float = 4.0,
    max_sl_percent: float = 3.0,
    tick_size: float = 0.01,
) -> float:
    """Reproduce the frozen V9 signal-close stop distance and tick rounding."""

    values = (signal_close, signal_atr, atr_mult, max_sl_percent, tick_size)
    if not all(np.isfinite(value) and value > 0.0 for value in values):
        raise ValueError(f"invalid stop inputs: {values}")
    raw = min(signal_atr * atr_mult, signal_close * max_sl_percent / 100.0)
    ticks = max(1, int(round(raw / tick_size)))
    return ticks * tick_size


def _bar_touches(
    *,
    side: Side,
    high: float,
    low: float,
    target: float,
    stop: float,
) -> tuple[bool, bool]:
    if side == "long":
        return high >= target, low <= stop
    if side == "short":
        return low <= target, high >= stop
    raise ValueError(f"unknown side: {side!r}")


def _open_touch(*, side: Side, open_price: float, target: float, stop: float) -> str | None:
    """Resolve a gap at bar open before consulting that bar's range."""

    if side == "long":
        if open_price >= target:
            return "target"
        if open_price <= stop:
            return "initial_stop"
    elif side == "short":
        if open_price <= target:
            return "target"
        if open_price >= stop:
            return "initial_stop"
    else:
        raise ValueError(f"unknown side: {side!r}")
    return None


def resolve_parent_touch(
    *,
    side: Side,
    parent_time: pd.Timestamp,
    parent: pd.Series,
    target: float,
    stop: float,
    lower_bars: pd.DataFrame | None,
) -> _Touch | None:
    """Resolve one 15m parent, descending to ordered lower bars only if needed."""

    parent_time = pd.Timestamp(parent_time)
    gap = _open_touch(
        side=side,
        open_price=float(parent["open"]),
        target=target,
        stop=stop,
    )
    if gap is not None:
        return _Touch(gap, parent_time, "15m_open_gap")

    target_touched, stop_touched = _bar_touches(
        side=side,
        high=float(parent["high"]),
        low=float(parent["low"]),
        target=target,
        stop=stop,
    )
    if not target_touched and not stop_touched:
        return None
    if target_touched and not stop_touched:
        return _Touch("target", parent_time, "15m_single_touch")
    if stop_touched and not target_touched:
        return _Touch("initial_stop", parent_time, "15m_single_touch")

    if lower_bars is None or lower_bars.empty:
        return _Touch("ambiguous", parent_time, "15m_double_touch_no_lower_path")

    ordered = lower_bars.sort_values("open_time", kind="stable")
    for row in ordered.itertuples(index=False):
        lower_time = pd.Timestamp(row.open_time)
        lower_gap = _open_touch(
            side=side,
            open_price=float(row.open),
            target=target,
            stop=stop,
        )
        if lower_gap is not None:
            return _Touch(lower_gap, lower_time, "lower_open_gap")
        lower_target, lower_stop = _bar_touches(
            side=side,
            high=float(row.high),
            low=float(row.low),
            target=target,
            stop=stop,
        )
        if lower_target and lower_stop:
            return _Touch("ambiguous", lower_time, "lower_bar_double_touch")
        if lower_target:
            return _Touch("target", lower_time, "ordered_lower_path")
        if lower_stop:
            return _Touch("initial_stop", lower_time, "ordered_lower_path")

    raise RuntimeError("lower bars did not reproduce a double-touch parent")


def label_pine_start_candidate(
    frame: pd.DataFrame,
    *,
    signal_i: int,
    side: Side,
    opposite_signal_i: int | None,
    lower_by_parent: Mapping[pd.Timestamp, pd.DataFrame] | None = None,
    target_percent: float = 1.5,
    atr_mult: float = 4.0,
    max_sl_percent: float = 3.0,
    tick_size: float = 0.01,
) -> PineStartOutcome:
    """Label one raw guarded V9 candidate without changing execution parameters."""

    if signal_i < 0 or signal_i + 1 >= len(frame):
        raise IndexError("candidate must have a next-open entry bar")
    if side not in ("long", "short"):
        raise ValueError(f"unknown side: {side!r}")
    if target_percent <= 0.0:
        raise ValueError("target_percent must be positive")
    entry_i = signal_i + 1
    times = pd.to_datetime(frame["open_time"], utc=True)
    entry_time = times.iloc[entry_i]
    entry_price = float(frame["open"].iloc[entry_i])
    if not np.isfinite(entry_price) or entry_price <= 0.0:
        raise ValueError("entry price must be finite and positive")

    stop_distance = rounded_initial_stop_distance(
        signal_close=float(frame["close"].iloc[signal_i]),
        signal_atr=float(frame["atr"].iloc[signal_i]),
        atr_mult=atr_mult,
        max_sl_percent=max_sl_percent,
        tick_size=tick_size,
    )
    direction = 1.0 if side == "long" else -1.0
    target_price = entry_price * (1.0 + direction * target_percent / 100.0)
    stop_price = entry_price - direction * stop_distance

    opposite_entry_i: int | None = None
    opposite_entry_time: pd.Timestamp | None = None
    if opposite_signal_i is not None:
        if opposite_signal_i <= signal_i:
            raise ValueError("opposite signal must occur after candidate signal")
        opposite_entry_i = opposite_signal_i + 1
        if opposite_entry_i < len(frame):
            opposite_entry_time = times.iloc[opposite_entry_i]
        else:
            opposite_entry_i = None

    # The opposite signal is only actionable after its bar closes, so its own
    # 15-minute price path remains part of this candidate's label path.
    last_path_i = len(frame) - 1 if opposite_entry_i is None else opposite_entry_i - 1
    lower_by_parent = {} if lower_by_parent is None else lower_by_parent
    for event_i in range(entry_i, last_path_i + 1):
        parent_time = times.iloc[event_i]
        touch = resolve_parent_touch(
            side=side,
            parent_time=parent_time,
            parent=frame.iloc[event_i],
            target=target_price,
            stop=stop_price,
            lower_bars=lower_by_parent.get(parent_time),
        )
        if touch is None:
            continue
        available_at = parent_time + pd.Timedelta(minutes=15)
        if touch.outcome == "ambiguous":
            return PineStartOutcome(
                status="ambiguous",
                label=None,
                outcome="ambiguous_double_touch",
                entry_i=entry_i,
                entry_time=entry_time,
                entry_price=entry_price,
                target_price=target_price,
                initial_stop_price=stop_price,
                initial_stop_distance=stop_distance,
                opposite_signal_i=opposite_signal_i,
                opposite_entry_time=opposite_entry_time,
                event_i=event_i,
                event_time=touch.event_time,
                label_available_at=available_at,
                bars_to_event=event_i - entry_i + 1,
                resolution=touch.resolution,
            )
        return PineStartOutcome(
            status="resolved",
            label=int(touch.outcome == "target"),
            outcome=touch.outcome,
            entry_i=entry_i,
            entry_time=entry_time,
            entry_price=entry_price,
            target_price=target_price,
            initial_stop_price=stop_price,
            initial_stop_distance=stop_distance,
            opposite_signal_i=opposite_signal_i,
            opposite_entry_time=opposite_entry_time,
            event_i=event_i,
            event_time=touch.event_time,
            label_available_at=available_at,
            bars_to_event=event_i - entry_i + 1,
            resolution=touch.resolution,
        )

    if opposite_entry_i is not None and opposite_entry_time is not None:
        return PineStartOutcome(
            status="resolved",
            label=0,
            outcome="opposite_signal_exit",
            entry_i=entry_i,
            entry_time=entry_time,
            entry_price=entry_price,
            target_price=target_price,
            initial_stop_price=stop_price,
            initial_stop_distance=stop_distance,
            opposite_signal_i=opposite_signal_i,
            opposite_entry_time=opposite_entry_time,
            event_i=opposite_entry_i,
            event_time=opposite_entry_time,
            label_available_at=opposite_entry_time,
            bars_to_event=opposite_entry_i - entry_i,
            resolution="next_open_opposite_signal",
        )

    end_exclusive = times.iloc[-1] + pd.Timedelta(minutes=15)
    return PineStartOutcome(
        status="censored",
        label=None,
        outcome="development_end_censored",
        entry_i=entry_i,
        entry_time=entry_time,
        entry_price=entry_price,
        target_price=target_price,
        initial_stop_price=stop_price,
        initial_stop_distance=stop_distance,
        opposite_signal_i=opposite_signal_i,
        opposite_entry_time=None,
        event_i=None,
        event_time=None,
        label_available_at=end_exclusive,
        bars_to_event=None,
        resolution="right_censored_at_development_end",
    )

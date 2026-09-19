"""No-stop replay for the BTC RSI strong-diamond fifth-event hypothesis.

Signals are the close-labelled output of :func:`btc_rsi_fifth.prepare_signals`.
Only ``strong_side`` values of +1/-1 participate in the sequence: ordinary
flips, circles, and all zero rows are deliberately ignored.  A signal at a
close timestamp fills at the raw five-minute candle *open* with that same
timestamp, so no later candle is used to make an entry or reduction decision.

This is a deterministic research ledger.  It neither loads market data nor
scores the strategy.  There is no stop, target, funding, leverage, or R-based
metric; four opposite strong diamonds each reduce 25% of the original entry
quantity.  Costs are fixed at 10 bp at entry and 10 bp per exited fraction.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_rsi_sixma import _validate_frame


ENTRY_COST = 0.001
EXIT_COST = 0.001
SCALEOUT_FRACTION = 0.25
BAR = pd.Timedelta(minutes=5)

POSITION_COLUMNS = [
    "position_id", "status", "entry_time", "entry_price", "side", "orig_qty",
    "remaining_fraction", "gross_realized_return", "costs", "terminal_mark_cost",
    "terminal_mark_time", "terminal_mark_price", "terminal_marked_net_return",
    "finalexit_time", "noR",
]
FILL_COLUMNS = [
    "position_id", "fill_type", "time", "price", "side", "fraction", "quantity",
    "gross_return", "cost", "remaining_fraction_after",
]
EVENT_COLUMNS = [
    "event_time", "strong_side", "streak", "window_status", "action", "position_id",
    "price", "remaining_fraction_before", "remaining_fraction_after",
]


def _utc_time(value: Any, name: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tz is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    return stamp.tz_convert("UTC")


def _frame_bound(frame: pd.DataFrame, value: Any, name: str, *, allow_terminal: bool) -> int:
    stamp = _utc_time(value, name)
    terminal = frame.index[-1] + BAR
    if stamp < frame.index[0] or stamp > terminal or (stamp == terminal and not allow_terminal):
        raise ValueError(f"{name} is outside the five-minute frame")
    place = int(frame.index.searchsorted(stamp))
    if place < len(frame) and frame.index[place] != stamp:
        raise ValueError(f"{name} must fall on the five-minute grid")
    return place


def _signal_table(signals: pd.DataFrame) -> pd.DataFrame:
    """Validate close-labelled source signals without deriving any new signal."""
    if not isinstance(signals, pd.DataFrame) or "strong_side" not in signals:
        raise ValueError("signals requires a strong_side column")
    if not isinstance(signals.index, pd.DatetimeIndex) or signals.index.tz is None:
        raise ValueError("signals requires a timezone-aware UTC DatetimeIndex")
    if str(signals.index.tz) != "UTC" or not signals.index.is_monotonic_increasing or not signals.index.is_unique:
        raise ValueError("signals index must be unique, chronological UTC timestamps")
    side = pd.to_numeric(signals["strong_side"], errors="coerce")
    if side.isna().any() or not side.isin((-1, 0, 1)).all():
        raise ValueError("strong_side values must be -1, 0, or 1")
    out = signals.copy()
    out["strong_side"] = side.astype(int)
    return out


def signals_with_streak(signals: pd.DataFrame) -> pd.DataFrame:
    """Attach the strong-diamond run count while leaving zero rows inert.

    The output's ``strong_streak`` is zero on an ordinary row for audit
    clarity.  Internally such rows do not reset the last strong sign, so
    ``+1, 0, +1`` has counts ``1, 0, 2`` and only a strong ``-1`` resets it.
    """
    out = _signal_table(signals)
    streaks = np.zeros(len(out), dtype=int)
    previous = 0
    count = 0
    for i, side in enumerate(out["strong_side"].to_numpy(int)):
        if side == 0:
            continue
        count = count + 1 if side == previous else 1
        previous = int(side)
        streaks[i] = count
    out["strong_streak"] = streaks
    return out


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _entry_state(position_id: int, when: pd.Timestamp, price: float, side: int) -> dict[str, Any]:
    return dict(
        position_id=position_id,
        entry_time=when,
        entry_price=float(price),
        side=int(side),
        orig_qty=1.0 / float(price),
        remaining_fraction=1.0,
        gross_realized_return=0.0,
        costs=ENTRY_COST,
        finalexit_time=pd.NaT,
    )


def _entry_fill(state: dict[str, Any]) -> dict[str, Any]:
    return dict(
        position_id=state["position_id"], fill_type="entry", time=state["entry_time"],
        price=state["entry_price"], side=state["side"], fraction=1.0,
        quantity=state["orig_qty"], gross_return=0.0, cost=ENTRY_COST,
        remaining_fraction_after=1.0,
    )


def _reduce(state: dict[str, Any], when: pd.Timestamp, price: float) -> dict[str, Any]:
    """Apply one owner-specified 25%-of-original exit at an event open."""
    fraction = min(SCALEOUT_FRACTION, float(state["remaining_fraction"]))
    gross = fraction * int(state["side"]) * (float(price) - float(state["entry_price"])) / float(state["entry_price"])
    cost = EXIT_COST * fraction
    state["remaining_fraction"] = max(0.0, float(state["remaining_fraction"]) - fraction)
    state["gross_realized_return"] += gross
    state["costs"] += cost
    if state["remaining_fraction"] == 0.0:
        state["finalexit_time"] = when
    return dict(
        position_id=state["position_id"], fill_type="partial_exit", time=when, price=float(price),
        side=state["side"], fraction=fraction, quantity=fraction * state["orig_qty"],
        gross_return=gross, cost=cost, remaining_fraction_after=state["remaining_fraction"],
    )


def _position_row(state: dict[str, Any], terminal_time: pd.Timestamp, terminal_price: float) -> dict[str, Any]:
    remaining = float(state["remaining_fraction"])
    terminal_gross = float(state["gross_realized_return"]) + remaining * int(state["side"]) * (
        float(terminal_price) - float(state["entry_price"])
    ) / float(state["entry_price"])
    terminal_mark_cost = EXIT_COST * remaining
    return dict(
        position_id=state["position_id"], status="closed" if remaining == 0.0 else "open",
        entry_time=state["entry_time"], entry_price=state["entry_price"], side=state["side"],
        orig_qty=state["orig_qty"], remaining_fraction=remaining,
        gross_realized_return=state["gross_realized_return"], costs=state["costs"],
        terminal_mark_cost=terminal_mark_cost, terminal_mark_time=terminal_time,
        terminal_mark_price=float(terminal_price),
        terminal_marked_net_return=terminal_gross - float(state["costs"]) - terminal_mark_cost,
        finalexit_time=state["finalexit_time"], noR=True,
    )


def _event_rows(signals: pd.DataFrame) -> pd.DataFrame:
    table = signals_with_streak(signals)
    return table.loc[table.strong_side.ne(0), ["strong_side", "strong_streak"]].copy()


def _terminal(frame: pd.DataFrame, end_i: int) -> tuple[pd.Timestamp, float]:
    # The final close belongs to [bar_open, bar_open + 5m), so a mark with
    # that price is available at the exclusive boundary, never at bar open.
    return frame.index[end_i - 1] + BAR, float(frame.close.iloc[end_i - 1])


def replay(frame5: pd.DataFrame, signals: pd.DataFrame, start: Any, end: Any) -> dict[str, pd.DataFrame]:
    """Replay a single serial no-stop stream from ``start`` through exclusive ``end``.

    Strong diamonds before ``start`` populate their run counter only; they can
    never create a carried position.  At a current event its opposite exit is
    processed first, then a newly flat stream may enter only on exactly its
    fifth same-sided strong diamond.  Later sixth-or-more diamonds never queue
    or retrigger an entry.
    """
    frame = _validate_frame(frame5)
    start_i = _frame_bound(frame, start, "start", allow_terminal=False)
    end_i = _frame_bound(frame, end, "end", allow_terminal=True)
    if start_i >= end_i:
        raise ValueError("start must precede end")
    event_source = _event_rows(signals)
    terminal_time, terminal_price = _terminal(frame, end_i)
    positions: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    state: dict[str, Any] | None = None
    next_position_id = 0
    start_time = frame.index[start_i]
    end_time = frame.index[end_i] if end_i < len(frame) else frame.index[-1] + BAR

    for when, event in event_source.iterrows():
        side, streak = int(event.strong_side), int(event.strong_streak)
        record = dict(event_time=when, strong_side=side, streak=streak, position_id=pd.NA,
                      price=np.nan, remaining_fraction_before=np.nan, remaining_fraction_after=np.nan)
        if when < start_time:
            audit.append(record | dict(window_status="before_start", action="warmup_count_only"))
            continue
        if when >= end_time:
            audit.append(record | dict(window_status="after_end", action="outside_window"))
            continue
        if when not in frame.index:
            audit.append(record | dict(window_status="in_window", action="unfillable_signal"))
            continue
        price = float(frame.at[when, "open"])
        record["price"] = price
        action_parts: list[str] = []
        if state is not None:
            record["position_id"] = state["position_id"]
            record["remaining_fraction_before"] = state["remaining_fraction"]
            if side == -int(state["side"]):
                fill = _reduce(state, when, price)
                fills.append(fill)
                action_parts.append("final_exit" if state["remaining_fraction"] == 0.0 else "partial_exit")
                record["remaining_fraction_after"] = state["remaining_fraction"]
                if state["remaining_fraction"] == 0.0:
                    positions.append(_position_row(state, terminal_time, terminal_price))
                    state = None
            else:
                action_parts.append("same_direction_hold")
                record["remaining_fraction_after"] = state["remaining_fraction"]
        if state is None:
            if streak == 5:
                state = _entry_state(next_position_id, when, price, side)
                fills.append(_entry_fill(state))
                record["position_id"] = state["position_id"]
                record["remaining_fraction_after"] = 1.0
                next_position_id += 1
                action_parts.append("entry")
            elif not action_parts:
                action_parts.append("flat_no_entry" if streak < 5 else "streak_not_fifth")
        audit.append(record | dict(window_status="in_window", action="+".join(action_parts)))

    if state is not None:
        positions.append(_position_row(state, terminal_time, terminal_price))
    return dict(
        positions=pd.DataFrame(positions, columns=POSITION_COLUMNS),
        fills=pd.DataFrame(fills, columns=FILL_COLUMNS),
        events=pd.DataFrame(audit, columns=EVENT_COLUMNS),
    )


def trace_position(frame5: pd.DataFrame, signals: pd.DataFrame, entry_time: Any, side: int, end: Any) -> dict[str, pd.DataFrame]:
    """Trace one externally chosen entry under the same scale-out rules.

    This matched-control helper deliberately ignores source signals whose
    close time is at or before ``entry_time``.  Therefore the entry's own
    strong diamond cannot immediately reduce the just-opened position.
    """
    frame = _validate_frame(frame5)
    if side not in (-1, 1):
        raise ValueError("side must be +1 or -1")
    entry_i = _frame_bound(frame, entry_time, "entry_time", allow_terminal=False)
    end_i = _frame_bound(frame, end, "end", allow_terminal=True)
    if entry_i >= end_i:
        raise ValueError("entry_time must precede end")
    entry_stamp = frame.index[entry_i]
    end_time = frame.index[end_i] if end_i < len(frame) else frame.index[-1] + BAR
    terminal_time, terminal_price = _terminal(frame, end_i)
    state = _entry_state(0, entry_stamp, float(frame.open.iloc[entry_i]), side)
    fills: list[dict[str, Any]] = [_entry_fill(state)]
    audit: list[dict[str, Any]] = []
    for when, event in _event_rows(signals).iterrows():
        if when <= entry_stamp or when >= end_time or when not in frame.index:
            continue
        event_side, streak = int(event.strong_side), int(event.strong_streak)
        price = float(frame.at[when, "open"])
        before = state["remaining_fraction"]
        if event_side == -side:
            fill = _reduce(state, when, price)
            fills.append(fill)
            action = "final_exit" if state["remaining_fraction"] == 0.0 else "partial_exit"
        else:
            action = "same_direction_hold"
        audit.append(dict(event_time=when, strong_side=event_side, streak=streak, window_status="in_window",
                          action=action, position_id=0, price=price, remaining_fraction_before=before,
                          remaining_fraction_after=state["remaining_fraction"]))
        if state["remaining_fraction"] == 0.0:
            break
    return dict(
        positions=pd.DataFrame([_position_row(state, terminal_time, terminal_price)], columns=POSITION_COLUMNS),
        fills=pd.DataFrame(fills, columns=FILL_COLUMNS),
        events=pd.DataFrame(audit, columns=EVENT_COLUMNS),
    )

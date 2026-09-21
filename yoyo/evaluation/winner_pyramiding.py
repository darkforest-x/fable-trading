"""Causal, offline winner-pyramiding replay for a single long SPIKE trade.

The input frame has a UTC, increasing bar-open index and the columns ``open``,
``high``, ``low``, ``close`` and ``atr``.  Decisions use the completed current
bar and prior bars only: close decisions are effective at the following open.
``raw_short`` is the already-produced opposite SPIKE signal; this module never
creates or filters signals.  The structural stop uses completed closes and a
running high/pullback low, while the legacy close-based 4 ATR trail and its
2R trigger retain the original entry risk even after an addition.

Quantities are continuous fractional units.  This research engine deliberately
does not model venue quantity ticks, partial fills, funding, or liquidation.
It uses the frozen 20 bp round-trip contract: one half is charged at entry and
the other half is reserved against every entry leg's original notional.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


ROUND_TRIP_FEE = 0.002
ENTRY_FEE = ROUND_TRIP_FEE / 2.0
_EPS = 1e-12


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float, np.number)) and math.isfinite(float(value))


def _floor_tick(value: float, tick: float) -> float:
    """Floor with a small relative tolerance for binary decimal ticks."""
    scaled = value / tick
    return math.floor(scaled + 1e-10 * max(1.0, abs(scaled))) * tick


def _event(items: list[dict[str, Any]], kind: str, *, bar: int, known_at: Any,
           filled_at: Any | None = None, **details: Any) -> None:
    items.append({"kind": kind, "bar": bar, "known_at": known_at,
                  "filled_at": filled_at, **details})


def replay_path(
    frame: pd.DataFrame,
    raw_short: np.ndarray | pd.Series | list[bool],
    gap: np.ndarray | pd.Series | list[bool],
    entry_i: int,
    initial_stop: float,
    tick: float,
    max_adds: int | None,
    *,
    capital: float = 100.0,
    gross_risk_budget: float = 1.0,
    leverage_cap: float = 1.0,
    bar_minutes: int = 60,
    events: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replay one already-opened long path without looking past each close.

    ``entry_i`` is the caller's original next-open fill bar. ``bar_minutes``
    is the public bar schedule (60 for this fixed-1h benchmark), rather than
    an inference from later timestamps. ``gap[i]`` means bar ``i`` is unknown,
    so an open position is censored at ``i - 1``'s close.
    Returns a terminal result and, when requested, an ordered audit event list.
    """
    required = {"open", "high", "low", "close", "atr"}
    if not required.issubset(frame.columns):
        raise ValueError(f"frame missing columns: {sorted(required - set(frame.columns))}")
    if (not isinstance(frame.index, pd.DatetimeIndex) or str(frame.index.tz) != "UTC"
            or not frame.index.is_monotonic_increasing or not frame.index.is_unique):
        raise ValueError("frame index must be increasing UTC timestamps")
    n = len(frame)
    if n == 0 or not 0 <= entry_i < n:
        raise ValueError("entry_i must identify a frame bar")
    if len(raw_short) != n or len(gap) != n:
        raise ValueError("raw_short and gap must align with frame")
    if max_adds is not None and (not isinstance(max_adds, int) or max_adds < 0):
        raise ValueError("max_adds must be a non-negative integer or None")
    if not isinstance(bar_minutes, int) or bar_minutes <= 0:
        raise ValueError("bar_minutes must be a positive integer")
    if not all(_finite(v) and float(v) > 0 for v in (tick, capital, gross_risk_budget, leverage_cap)):
        raise ValueError("tick, capital, gross_risk_budget, and leverage_cap must be finite and positive")

    o = frame.open.to_numpy(dtype=float); h = frame.high.to_numpy(dtype=float)
    lo = frame.low.to_numpy(dtype=float); cl = frame.close.to_numpy(dtype=float); atr = frame.atr.to_numpy(dtype=float)
    raw_short = np.asarray(raw_short, dtype=bool); gap = np.asarray(gap, dtype=bool)
    # A dropped timestamp is a data condition, not a malformed calendar.  The
    # caller marks its first later bar in ``gap`` and this fixed schedule gives
    # the preceding observed close its honest completed-close timestamp.
    bar_duration = pd.Timedelta(minutes=bar_minutes)

    def close_at(i: int) -> pd.Timestamp:
        return frame.index[i] + bar_duration
    if not np.isfinite(np.r_[o, h, lo, cl]).all():
        raise ValueError("OHLC values must be finite")
    if np.any(h < lo):
        raise ValueError("high must be at least low")
    p0, s0 = float(o[entry_i]), float(initial_stop)
    risk0 = p0 - s0
    if not (_finite(s0) and risk0 > 0):
        raise ValueError("initial_stop must be finite and below entry open")
    if bool(gap[entry_i]):
        raise ValueError("initial entry bar cannot be a data gap")

    q0 = min(float(gross_risk_budget) / risk0, float(capital) / (p0 * (1.0 + ENTRY_FEE)))
    if not _finite(q0) or q0 <= 0:
        raise ValueError("initial quantity is not positive")
    q, cost = q0, q0 * p0
    stop = s0
    initial_risk_usd = q0 * risk0
    stop_floor = q * stop - cost - ROUND_TRIP_FEE * cost
    last_entry = p0
    adds_count = 0
    trail_armed = False
    running_high = float(h[entry_i])
    pullback = False
    pullback_low = math.nan
    pending_add: dict[str, Any] | None = None
    pending_reverse = False
    audit: list[dict[str, Any]] = []
    # This is the equity immediately after the known entry-open fill.  Do not
    # seed a peak with entry-bar close information when that bar can stop first.
    peak_equity = float(capital - ENTRY_FEE * cost)
    max_drawdown = 0.0

    def net_at(price: float) -> float:
        return q * price - cost - ROUND_TRIP_FEE * cost

    def record_floor() -> None:
        nonlocal stop_floor
        stop_floor = max(stop_floor, net_at(stop))

    def result(exit_i: int, price: float, reason: str, censored: bool, *,
               known_at: Any, exit_time_precision: str) -> dict[str, Any]:
        gross = q * price - cost
        net = gross - ROUND_TRIP_FEE * cost
        initial_notional = q0 * p0
        return {
            "entry_i": entry_i, "entry_time": frame.index[entry_i], "entry_price": p0,
            "initial_stop": s0, "initial_risk": risk0, "initial_risk_usd": initial_risk_usd,
            "exit_i": exit_i, "exit_time": frame.index[exit_i], "exit_price": price,
            "exit_reason": reason, "exit_known_at": known_at,
            "exit_time_precision": exit_time_precision, "censored": censored,
            "quantity": q, "entry_notional": cost,
            # Stable runner-facing names; the shorter names above remain useful
            # in per-path event and ledger inspection.
            "initial_quantity": q0, "final_quantity": q,
            "adds_count": adds_count, "common_stop": stop, "net_stop_floor": stop_floor,
            "gross_usd": gross, "net_usd": net,
            "gross_r": gross / initial_risk_usd, "net_r": net / initial_risk_usd,
            "gross_return": gross / initial_notional, "net_return": net / initial_notional,
            "fees_total": ROUND_TRIP_FEE * cost, "entry_fees_paid": ENTRY_FEE * cost,
            "exit_fees_reserved": ENTRY_FEE * cost, "peak_close_equity": peak_equity,
            "max_close_drawdown": max_drawdown, "max_close_drawdown_usd": max_drawdown,
        }

    def finish(i: int, price: float, reason: str, censored: bool, *, known_at: Any,
               exit_time_precision: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        filled_at = frame.index[i] if exit_time_precision == "bar_open" else None
        _event(audit, "exit", bar=i, known_at=known_at, filled_at=filled_at,
               reason=reason, exit_time_precision=exit_time_precision, quantity=q, price=price,
               commonstop=stop, net_stop=net_at(stop), pnl=net_at(price))
        return result(i, price, reason, censored, known_at=known_at,
                      exit_time_precision=exit_time_precision), audit if events else []

    _event(audit, "entry", bar=entry_i, known_at=frame.index[entry_i], filled_at=frame.index[entry_i],
           quantity=q, price=p0, commonstop=stop, net_stop=net_at(stop), pnl=net_at(p0), reason="initial_next_open")

    # The entry bar can stop an already-filled trade, but it cannot begin a
    # structure pullback.  Close-based protection still becomes effective next bar.
    start = entry_i
    for i in range(start, n):
        stamp = frame.index[i]
        if i > entry_i and bool(gap[i]):
            return finish(i - 1, float(cl[i - 1]), "data_gap_censored", True,
                          known_at=close_at(i - 1), exit_time_precision="bar_close_mark")

        if i > entry_i:
            # 1) Protection active from prior closes has priority over every intent.
            if o[i] <= stop + _EPS:
                return finish(i, min(float(o[i]), stop), "stop_gap", False, known_at=stamp,
                              exit_time_precision="bar_open")
            # 2) An opposite completed signal exits next open and consumes any add.
            if pending_reverse:
                return finish(i, float(o[i]), "opposite_short_next_open", False, known_at=stamp,
                              exit_time_precision="bar_open")
            # 3) A pending add is one-shot.  It is consumed even when rejected.
            if pending_add is not None:
                intent = pending_add; pending_add = None
                reject: str | None = None
                p = float(o[i])
                if p <= stop + _EPS:
                    reject = "opening_at_or_below_stop"
                elif p <= last_entry + _EPS:
                    reject = "not_above_last_entry"
                else:
                    hnet = net_at(stop)
                    floor_before = float(intent["floor_before"])
                    reserve = max(floor_before, 0.5 * hnet, 0.0)
                    room = hnet - reserve
                    unit_loss = p - stop + ROUND_TRIP_FEE * p
                    equity = capital + q * p - cost - ENTRY_FEE * cost
                    q_risk = max(0.0, room / unit_loss) if unit_loss > 0 else 0.0
                    q_margin = max(0.0, (leverage_cap * equity - q * p) / (p * (1.0 + leverage_cap * ENTRY_FEE)))
                    add_q = min(q_risk, q_margin)
                    if not all(_finite(v) for v in (hnet, floor_before, reserve, room, unit_loss, equity, q_risk, q_margin, add_q)):
                        reject = "nonfinite_sizing"
                    elif add_q <= 1e-10 * max(1.0, q):
                        reject = "insufficient_risk_or_margin_room"
                if reject is not None:
                    # The newly-raised stop was deliberately not committed at
                    # its signal close.  A consumed skip may now commit it.
                    record_floor()
                    _event(audit, "reject", bar=i, known_at=stamp, filled_at=stamp, reason=reject,
                           quantity=0.0, price=p, commonstop=stop, net_stop=net_at(stop), pnl=net_at(stop),
                           eligibility=intent["eligibility"], net_stop_floor=stop_floor)
                else:
                    q += add_q; cost += add_q * p; last_entry = p; adds_count += 1
                    record_floor()
                    _event(audit, "add", bar=i, known_at=stamp, filled_at=stamp, reason="structure_profit_add",
                           quantity=add_q, price=p, commonstop=stop, net_stop=net_at(stop), pnl=net_at(stop),
                           eligibility=intent["eligibility"], floor_before=floor_before,
                           net_stop_floor=stop_floor)

        # An intrabar stop uses the protection known before this bar's close.
        if lo[i] <= stop + _EPS:
            return finish(i, stop, "stop", False, known_at=close_at(i),
                          exit_time_precision="intrabar_window")

        # End-of-bar equity only uses the close, avoiding an invented high/low order.
        equity_close = capital + q * cl[i] - cost - ENTRY_FEE * cost
        peak_equity = max(peak_equity, equity_close)
        max_drawdown = max(max_drawdown, peak_equity - equity_close)

        old_stop, old_floor = stop, stop_floor
        structure_raised = False
        structure_candidate = math.nan
        if i > entry_i:
            if not pullback:
                if cl[i] < cl[i - 1] - _EPS:
                    pullback = True
                    pullback_low = float(lo[i])
                else:
                    running_high = max(running_high, float(h[i]))
            else:
                pullback_low = min(pullback_low, float(lo[i]))
                if cl[i] > running_high + _EPS:
                    structure_candidate = _floor_tick(pullback_low - tick, tick)
                    if structure_candidate > stop + _EPS and structure_candidate < cl[i] - _EPS:
                        stop = structure_candidate
                        structure_raised = True
                    _event(audit, "structure", bar=i, known_at=close_at(i), reason="close_above_frozen_high",
                           quantity=q, price=float(cl[i]), commonstop=stop, net_stop=net_at(stop),
                           pnl=net_at(stop), frozen_high=running_high, pullback_low=pullback_low,
                           candidate_stop=structure_candidate, raised=structure_raised)
                    # A confirmation always starts a fresh skeleton, even when an add is unavailable.
                    running_high = float(h[i]); pullback = False; pullback_low = math.nan

        if cl[i] >= p0 + 2.0 * risk0 - _EPS:
            trail_armed = True
        if trail_armed and _finite(atr[i]) and atr[i] > 0:
            trail_candidate = _floor_tick(float(cl[i]) - 4.0 * float(atr[i]), tick)
            if trail_candidate > stop + _EPS and trail_candidate < cl[i] - _EPS:
                stop = trail_candidate

        eligible = (structure_raised and cl[i] >= p0 + 2.0 * risk0 - _EPS
                    and (max_adds is None or adds_count < max_adds))
        # An eligible add may only promise the post-fill floor.  Committing the
        # pre-fill stop value here would claim profit the new quantity gives up.
        defer_floor_commit = eligible and not bool(raw_short[i])
        if stop > old_stop + _EPS:
            _event(audit, "stop_update", bar=i, known_at=close_at(i), reason="close_effective_next_open",
                   quantity=q, price=float(cl[i]), commonstop=stop, net_stop=net_at(stop), pnl=net_at(stop),
                   prior_stop=old_stop, floor_before=old_floor,
                   floor_after=old_floor if defer_floor_commit else max(old_floor, net_at(stop)),
                   floor_commit_deferred=defer_floor_commit)
        if defer_floor_commit:
            pending_add = {"floor_before": old_floor,
                           "eligibility": "structure_raise_and_original_2r"}
        if bool(raw_short[i]):
            pending_reverse = True
            pending_add = None
        # A non-add close can safely commit its raised stop.  The deferred case
        # instead commits only after its next-open fill or one-shot rejection.
        if stop > old_stop + _EPS and not defer_floor_commit:
            record_floor()

    return finish(n - 1, float(cl[-1]), "boundary_mark", True, known_at=close_at(n - 1),
                  exit_time_precision="bar_close_mark")

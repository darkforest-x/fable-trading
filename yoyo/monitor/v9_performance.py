"""Closed-bar V9 position projection for signal cards; never an order or fill.

The card projection replays the same frozen serial engine the V9 backtest used:
one position per symbol/timeframe stream, next-open reference entry, the stop
frozen at the signal close, 2R arming of the 4ATR closed-bar trail, and raw
opposite V6 confirmations exiting at the next open.  ``simulate_v6_variant`` is
used rather than ``spike_v9.replay_v9`` because the latter zeroes every signal
outside the frozen 2024-09-10..2026-09-10 research window and would silently
drop live bars.  ``tests/parity/test_duplicate_semantics.py`` pins the two
engines to the same trades inside that window.

Only bars already closed and supplied by the caller are read; a projection is a
mutable display reference that never feeds signal generation, notification
eligibility, or an account.  An admitted signal that the serial engine could
not open (a position was already running) reports ``unknown`` rather than
borrowing another trade's result.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, simulate_v6_variant

# The card's R is net of the same fixed 0.2% round trip the backtest reports.
BASIS = "v9_next_open_serial_replay_net_of_round_trip_cost"
ROUND_TRIP_COST = ExecutionSpec.round_trip_cost


def _number(value: object) -> float | None:
    """Keep an unavailable projection value explicit instead of imputing zero."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _status(net_r: float | None) -> str:
    if net_r is None:
        return "unknown"
    return "profit" if net_r > 1e-9 else "loss" if net_r < -1e-9 else "breakeven"


def _projection(trade: dict, *, step: int, active: bool) -> dict:
    """Serialize one replayed trade as a display-only card projection."""
    net_r, gross_r = _number(trade.get("net_r")), _number(trade.get("gross_r"))
    entry_i, exit_i = int(trade["entry_i"]), int(trade["exit_i"])
    initial_stop, protection = _number(trade.get("initial_stop")), _number(trade.get("protection"))
    exit_time_ms = int(pd.Timestamp(trade["exit_time"]).value // 1_000_000) + step
    reason = str(trade.get("exit_reason"))
    trailing = (initial_stop is not None and protection is not None
                and not math.isclose(initial_stop, protection, rel_tol=0., abs_tol=0.))
    return {
        "status": "active" if active else _status(net_r),
        "stop_triggered": not active and reason.startswith(("initial_stop", "trailing_stop")),
        "current_r": net_r,
        "peak_r": _number(trade.get("mfe_r")),
        "exit_r": None if active else net_r,
        "exit_price": None if active else _number(trade.get("exit_price")),
        "stop_price": protection,
        "trailing_active": trailing,
        "bars_held": exit_i - entry_i,
        "updated_at_ms": exit_time_ms,
        "exit_time_ms": None if active else exit_time_ms,
        "basis": BASIS,
        "entry_price": _number(trade.get("entry_price")),
        "entry_time_ms": int(pd.Timestamp(trade["entry_time"]).value // 1_000_000),
        "initial_stop": initial_stop,
        "initial_risk": _number(trade.get("initial_risk")),
        "gross_r": gross_r,
        "net_r": net_r,
        "exit_reason": None if active else reason,
        "mark": "last_closed_bar_close" if active else None,
        "round_trip_cost": ROUND_TRIP_COST,
    }


def _unopened(reason: str) -> dict:
    """An admitted signal the serial engine never opened carries no R at all."""
    return {"status": "unknown", "stop_triggered": False, "current_r": None, "peak_r": None,
            "exit_r": None, "exit_price": None, "stop_price": None, "trailing_active": False,
            "bars_held": 0, "updated_at_ms": None, "exit_time_ms": None, "basis": BASIS,
            "reason": reason, "round_trip_cost": ROUND_TRIP_COST}


def project(built: pd.DataFrame, evidence: pd.DataFrame, *, minutes: int, tick: float,
            data_gap: pd.Series, admitted: pd.Series | np.ndarray | None = None) -> dict[int, dict]:
    """Project each admitted V9 signal's closed-bar path, keyed by its bar close.

    ``built`` and ``evidence`` are this caller's own supplied closed prefix from
    ``v9_signals._decision_frame``; columns read are open/high/low/close/atr and
    the evidence ``side``/``v9``/``risk_status``.  Raw opposite confirmations
    remain exits even when V9 refuses them as entries, exactly as in the frozen
    replay.  No future bar is read: the last projection is a mark at the final
    supplied close, not a realized exit.
    """
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        raise ValueError("minutes must be a positive integer")
    if isinstance(tick, bool) or not math.isfinite(float(tick)) or float(tick) <= 0:
        raise ValueError("tick must be positive and finite")
    if len(built) == 0 or len(evidence) == 0:
        return {}
    if not built.index.equals(evidence.index):
        raise ValueError("evidence must be aligned with the supplied bars")
    step = minutes * 60_000
    side = evidence.side.to_numpy(int, copy=False)
    signals = pd.DataFrame({"long_signal": side == 1, "short_signal": side == -1}, index=built.index)
    if admitted is None:
        admitted = (evidence.v9.to_numpy(bool, copy=False)
                    & evidence.risk_status.eq("known").to_numpy(bool, copy=False))
    admission = pd.Series(np.asarray(admitted, dtype=bool), index=built.index)
    frame = pd.DataFrame(built[["open", "high", "low", "close", "atr"]]).copy()
    frame.attrs["minutes"] = minutes
    gap = pd.Series(data_gap, index=built.index).fillna(True).astype(bool)
    _, trades = simulate_v6_variant(frame, signals, admission=admission, variant="v9_monitor",
                                    data_gap=gap, spec=ExecutionSpec(tick=float(tick)))
    projections: dict[int, dict] = {
        int(pd.Timestamp(stamp).value // 1_000_000) + step: _unopened("serial_position_already_open")
        for stamp in built.index[admission.to_numpy(bool)]
    }
    for trade in trades.to_dict("records"):
        close_ms = int(pd.Timestamp(trade["signal_bar_open"]).value // 1_000_000) + step
        if close_ms not in projections:
            # A raw reversal can close a position without being V9-admitted; it
            # never creates a card of its own.
            continue
        censored, reason = bool(trade["censored"]), str(trade["exit_reason"])
        if censored and reason == "boundary_mark":
            projections[close_ms] = _projection(trade, step=step, active=True)
        elif censored:
            projections[close_ms] = _unopened("data_gap_censored")
        else:
            projections[close_ms] = _projection(trade, step=step, active=False)
    return projections

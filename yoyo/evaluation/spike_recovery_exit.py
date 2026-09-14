"""Bounded, research-only exit replay for one already-admitted SPIKE V8 entry.

The module consumes closed OHLC/ATR bars and raw V6 sides supplied by its
caller.  It never derives admissions, loads market data, or serialises entries:
each ``replay_entry`` call has one independent next-open position.  That keeps
an altered exit from creating a shadow opportunity before the account simulator
has decided whether cash permits a later entry.

The frozen five-bar / 0.2 ATR buffer / 2 ATR floor initial stop, 2R close arm,
4 ATR close trail and 20 bp round-trip cost come from ``ExecutionSpec``.  A
new fixed take-profit is a limit price (filled at the observed favorable open
when the market gaps beyond it), while a protective stop keeps the frozen
adverse-open fill convention.  All close-derived changes are effective only on
the following bar.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Integral
from typing import Optional

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, _close_trade, _data_gap, _gap_censor
from yoyo.evaluation.spike_v7_fast import _initial_position_fast


@dataclass(frozen=True)
class Prepared:
    """Immutable, precomputed inputs shared by independent entry replays."""

    index: pd.Index
    gap: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray
    raw_side: np.ndarray


def _coerce_gap(frame: pd.DataFrame, raw_signals: pd.DataFrame) -> np.ndarray:
    """Use the supplied V6 gap field, or derive its normal closed-bar boundary."""
    for name in ("_data_gap", "data_gap"):
        if name in raw_signals:
            return pd.Series(raw_signals[name], index=frame.index).fillna(True).astype(bool).to_numpy()
    minutes = frame.attrs.get("minutes")
    if isinstance(minutes, (bool, np.bool_)) or not isinstance(minutes, Integral) or minutes <= 0:
        raise ValueError("frame.attrs minutes is required when raw signals omit _data_gap")
    return _data_gap(frame, int(minutes)).to_numpy(bool)


def prepare(frame: pd.DataFrame, raw_signals: pd.DataFrame) -> Prepared:
    """Materialize closed-bar V6 inputs once without evaluating an entry.

    ``raw_signals`` must carry raw V6 ``long_signal`` and ``short_signal``
    columns.  If it carries ``_data_gap`` (or ``data_gap``), that exact gap
    boundary is retained.  Otherwise the normal timestamp-derived V6 gap is
    calculated; this only uses the frame index and never reads a future bar.
    """
    required = {"open", "high", "low", "close", "atr"}
    if not isinstance(frame, pd.DataFrame) or not required.issubset(frame):
        raise ValueError("OHLC and atr columns are required")
    if not isinstance(raw_signals, pd.DataFrame) or not frame.index.equals(raw_signals.index):
        raise ValueError("frame and raw signals need the same index")
    if not {"long_signal", "short_signal"}.issubset(raw_signals):
        raise ValueError("raw V6 long_signal and short_signal columns are required")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("frame index must be unique and chronological")
    long = raw_signals.long_signal.fillna(False).astype(bool).to_numpy()
    short = raw_signals.short_signal.fillna(False).astype(bool).to_numpy()
    if (long & short).any():
        raise ValueError("a raw V6 bar cannot carry both sides")
    return Prepared(
        index=frame.index.copy(), gap=_coerce_gap(frame, raw_signals),
        open=frame.open.to_numpy(float, copy=True), high=frame.high.to_numpy(float, copy=True),
        low=frame.low.to_numpy(float, copy=True), close=frame.close.to_numpy(float, copy=True),
        atr=frame.atr.to_numpy(float, copy=True), raw_side=np.where(long, 1, np.where(short, -1, 0)),
    )


def _valid_end(end_i: Optional[int], size: int) -> int:
    if end_i is None:
        return size
    if isinstance(end_i, (bool, np.bool_)) or not isinstance(end_i, Integral) or not 0 <= int(end_i) <= size:
        raise ValueError("end_i must be an exclusive frame bound")
    return int(end_i)


def _round_favorable(price: float, *, side: int, tick: float) -> float:
    """Round a new cost-protection or limit target in its favorable direction."""
    ratio = price / tick
    # Decimal tick multiples commonly arrive a few ulps above/below their
    # mathematical value.  The tolerance only restores that exact multiple;
    # it never changes a material one-tick favorable rounding decision.
    epsilon = 1e-12
    return (math.ceil(ratio - epsilon) * tick if side == 1 else math.floor(ratio + epsilon) * tick)


def _round_frozen_trail(price: float, *, side: int, tick: float) -> float:
    """Keep frozen V6 trail rounding: floor long and ceil short."""
    return (math.floor(price / tick) * tick if side == 1 else math.ceil(price / tick) * tick)


def _stop_hit(side: int, high: float, low: float, protection: float) -> bool:
    return low <= protection if side == 1 else high >= protection


def _stop_gap(side: int, opening: float, protection: float) -> bool:
    return opening <= protection if side == 1 else opening >= protection


def _target_hit(side: int, high: float, low: float, target: float) -> bool:
    epsilon = max(abs(target), abs(high), abs(low), 1.0) * 1e-12
    return high >= target - epsilon if side == 1 else low <= target + epsilon


def _target_gap(side: int, opening: float, target: float) -> bool:
    epsilon = max(abs(target), abs(opening), 1.0) * 1e-12
    return opening >= target - epsilon if side == 1 else opening <= target + epsilon


def _decorate(trade: dict[str, object], *, exit_at_open: bool, protection_mode: str,
              trigger_r: Optional[float], protection_armed: bool, protection_trigger_count: int,
              protection_trigger_i: Optional[int], protection_price: float, target_price: float,
              take_profit_r: Optional[float], ambiguous_stop_tp_count: int,
              mfe_precision: str, cost_r: float, side_override: Optional[int]) -> dict[str, object]:
    """Add the explicit policy metadata while retaining frozen trade fields."""
    trade.update(
        exit_at_open=exit_at_open, final_protection=protection_price,
        protection_mode=protection_mode, trigger_r=trigger_r, protection_trigger_r=trigger_r,
        protection_armed=protection_armed, protection_trigger_count=protection_trigger_count,
        protection_trigger_i=protection_trigger_i, protection_price=protection_price,
        take_profit_r=take_profit_r, target_price=target_price,
        cost_r=cost_r, entry_side_source="side_override" if side_override is not None else "raw_v6_signal",
        side_override=side_override,
        be_armed=protection_armed,
        ambiguous_stop_tp=bool(ambiguous_stop_tp_count),
        ambiguous_stop_tp_count=ambiguous_stop_tp_count,
        mfe_precision=mfe_precision,
        mfe_r_strategy_usable=mfe_precision != "capped_at_fixed_take_profit",
    )
    return trade


def replay_entry(prepared: Prepared, signal_i: int, *, take_profit_r: Optional[float] = 3,
                 protection_mode: str = "none", trigger_r: Optional[float] = None,
                 tick: float = .01, end_i: Optional[int] = None,
                 side_override: Optional[int] = None) -> Optional[dict[str, object]]:
    """Replay one raw V6 signal with an exclusive no-future ``end_i`` boundary.

    ``protection_mode='entry'`` uses gross favorable R; ``'cost'`` uses net
    floating R, so it requires gross R >= ``0.002 / initial_risk_frac`` plus
    ``trigger_r``.  Trigger highs/lows are observed at bar close and install
    protection only for the next bar.  Stop priority is conservative when a
    bar also reaches fixed TP intrabar.  A TP gap fills at its favorable
    observed open (a limit price or better) before a later intrabar stop; a
    pending raw reverse still has precedence at that same open.  A stop gap
    fills at the adverse observed open.
    ``side_override`` is only for a matched non-signal control; it changes the
    initial side, never suppresses or invents raw V6 reverse exits.
    """
    if not isinstance(prepared, Prepared):
        raise TypeError("prepared must come from prepare()")
    size = len(prepared.index)
    if isinstance(signal_i, (bool, np.bool_)) or not isinstance(signal_i, Integral) or not 0 <= int(signal_i) < size:
        raise ValueError("signal_i must be a valid frame position")
    signal_i = int(signal_i)
    end = _valid_end(end_i, size)
    if protection_mode not in {"none", "entry", "cost"}:
        raise ValueError("protection_mode must be none, entry, or cost")
    if not math.isfinite(float(tick)) or float(tick) <= 0:
        raise ValueError("tick must be positive and finite")
    tick = float(tick)
    if take_profit_r is not None and (not math.isfinite(float(take_profit_r)) or float(take_profit_r) <= 0):
        raise ValueError("take_profit_r must be positive and finite or None")
    if protection_mode == "none":
        if trigger_r is not None:
            raise ValueError("trigger_r requires an entry or cost protection mode")
    elif trigger_r is None or not math.isfinite(float(trigger_r)) or float(trigger_r) < 0:
        raise ValueError("entry and cost protection modes require non-negative trigger_r")
    elif trigger_r is not None:
        trigger_r = float(trigger_r)
    if side_override is not None:
        if isinstance(side_override, (bool, np.bool_)) or not isinstance(side_override, Integral) or int(side_override) not in (-1, 1):
            raise ValueError("side_override must be +1, -1, or None")
        side_override = int(side_override)
    raw_signal_side = int(prepared.raw_side[signal_i])
    if (side_override is None and raw_signal_side == 0) or signal_i + 1 >= end:
        return None

    # Matched controls can supply a direction at a non-signal bar.  In normal
    # use this stays exactly tied to the raw V6 close event at ``signal_i``.
    side = raw_signal_side if side_override is None else side_override
    spec = ExecutionSpec(tick=tick)
    position = _initial_position_fast(prepared.index, prepared.open, prepared.high, prepared.low,
                                      prepared.close, prepared.atr, prepared.gap, signal_i, side, spec)
    if position is None:
        return None
    entry_i = int(position["entry_i"])
    entry, risk = float(position["entry_price"]), float(position["initial_risk"])
    initial_stop = float(position["initial_stop"])
    risk_frac = float(position["initial_risk_frac"])
    target = (math.nan if take_profit_r is None else _round_favorable(
        entry + side * float(take_profit_r) * risk, side=side, tick=tick))
    cost_r = spec.round_trip_cost / risk_frac
    cost_protection = _round_favorable(entry + side * spec.round_trip_cost * entry, side=side, tick=tick)
    protection_armed = False
    protection_trigger_count = 0
    protection_trigger_i: Optional[int] = None
    ambiguous = 0
    pending_reverse = False
    mfe_precision = "legacy_excludes_stopped_exit_bar"

    def finish(price: float, reason: str, i: int, *, open_exit: bool, censored: bool = False) -> dict[str, object]:
        trade = _close_trade(position, exit_i=i, exit_time=prepared.index[i], exit_price=float(price), reason=reason, spec=spec)
        if censored:
            trade["censored"] = True
            trade["exit_time_precision"] = "last_complete_close"
        return _decorate(trade, exit_at_open=open_exit, protection_mode=protection_mode, trigger_r=trigger_r,
                         protection_armed=protection_armed, protection_trigger_count=protection_trigger_count,
                         protection_trigger_i=protection_trigger_i, protection_price=float(position["protection"]),
                         target_price=target, take_profit_r=take_profit_r,
                         ambiguous_stop_tp_count=ambiguous, mfe_precision=mfe_precision,
                         cost_r=cost_r, side_override=side_override)

    for i in range(entry_i, end):
        if bool(prepared.gap[i]):
            trade = _gap_censor(position, exit_i=i, exit_time=prepared.index[i])
            return _decorate(trade, exit_at_open=False, protection_mode=protection_mode, trigger_r=trigger_r,
                             protection_armed=protection_armed, protection_trigger_count=protection_trigger_count,
                             protection_trigger_i=protection_trigger_i, protection_price=float(position["protection"]),
                             target_price=target, take_profit_r=take_profit_r,
                             ambiguous_stop_tp_count=ambiguous, mfe_precision=mfe_precision,
                             cost_r=cost_r, side_override=side_override)
        opening, high, low, close, atr = (float(values[i]) for values in
                                          (prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr))
        protection = float(position["protection"])
        if pending_reverse:
            # Existing stop protection is known before this open; TP at this
            # same open remains a raw reverse close rather than a path claim.
            if _stop_gap(side, opening, protection):
                reason = "trailing_stop_gap" if protection != initial_stop else "initial_stop_gap"
            else:
                reason = "opposite_v6_next_open"
            return finish(opening, reason, i, open_exit=True)

        # A favorable TP opening is observed before the bar's later unknown
        # path.  It therefore closes at the observed opening even if that bar
        # subsequently spans the old stop.
        if take_profit_r is not None and _target_gap(side, opening, target):
            position["mfe_r"] = max(float(position["mfe_r"]), side * (target - entry) / risk)
            mfe_precision = "capped_at_fixed_take_profit"
            return finish(opening, "take_profit_gap", i, open_exit=True)

        stopped = _stop_hit(side, high, low, protection)
        hit_tp = take_profit_r is not None and _target_hit(side, high, low, target)
        if stopped:
            if hit_tp:
                ambiguous += 1
            price = min(opening, protection) if side == 1 else max(opening, protection)
            reason = "trailing_stop" if protection != initial_stop else "initial_stop"
            if _stop_gap(side, opening, protection):
                reason += "_gap"
            return finish(price, reason, i, open_exit=reason.endswith("_gap"))
        if hit_tp:
            # Only the filled target is known as MFE; intrabar overshoot is not.
            position["mfe_r"] = max(float(position["mfe_r"]), side * (target - entry) / risk)
            mfe_precision = "capped_at_fixed_take_profit"
            price = opening if _target_gap(side, opening, target) else target
            return finish(price, "take_profit_gap" if price == opening else "take_profit", i,
                          open_exit=price == opening)

        favorable = high if side == 1 else low
        favourable_r = side * (favorable - entry) / risk
        position["mfe_r"] = max(float(position["mfe_r"]), favourable_r)
        if protection_mode != "none" and not protection_armed:
            required_r = float(trigger_r) if protection_mode == "entry" else cost_r + float(trigger_r)
            if favourable_r >= required_r:
                candidate = entry if protection_mode == "entry" else cost_protection
                old = float(position["protection"])
                position["protection"] = max(old, candidate) if side == 1 else min(old, candidate)
                protection_armed = True
                protection_trigger_count += 1
                protection_trigger_i = i
        current_r = side * (close - entry) / risk
        position["trail_armed"] = bool(position["trail_armed"]) or current_r >= spec.arm_r
        if bool(position["trail_armed"]) and math.isfinite(atr) and atr > 0:
            raw = close - side * spec.trail_atr * atr
            candidate = _round_frozen_trail(raw, side=side, tick=tick)
            old = float(position["protection"])
            position["protection"] = max(old, candidate) if side == 1 else min(old, candidate)
        pending_reverse = int(prepared.raw_side[i]) == -side

    # The boundary is a complete close only: no bar at end_i has been read.
    last = end - 1
    return finish(float(prepared.close[last]), "boundary_mark", last, open_exit=False, censored=True)

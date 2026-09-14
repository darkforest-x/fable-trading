"""Research-only net-target exit replay for one already-admitted V8 position.

Inputs are the closed-bar :class:`Prepared` arrays from ``spike_recovery_exit``:
open/high/low/close/ATR, raw V6 sides and the precomputed data-gap boundary.
No admissions or market data are derived here.  ``next_bar`` observes a bar
using the frozen stop-first OHLC convention and installs cost protection after
that bar closes.  ``ohlc`` and ``olhc`` instead replay the explicitly assumed
O-H-L-C or O-L-H-C piecewise-linear path and may arm cost protection within a
bar.  Those path modes are scenarios, not observed intrabar truth or bounds.
"""
from __future__ import annotations

import math
from numbers import Integral
from typing import Any, Optional

import numpy as np

from yoyo.evaluation.spike_recovery_exit import (
    Prepared, _round_favorable, _round_frozen_trail, _stop_gap, _stop_hit,
    _target_gap, _target_hit, _valid_end, prepare,
)
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, _close_trade, _gap_censor
from yoyo.evaluation.spike_v7_fast import _initial_position_fast

_EPS = 1e-12


def _side_override(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if (isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral)
            or int(value) not in (-1, 1)):
        raise ValueError("side_override must be +1, -1, or None")
    return int(value)


def _cross_parameter(start: float, end: float, price: float) -> Optional[float]:
    """Return a strictly-forward crossing fraction on a monotone segment."""
    delta = end - start
    if abs(delta) <= _EPS:
        return None
    fraction = (price - start) / delta
    return fraction if fraction > _EPS and fraction <= 1 + _EPS else None


def replay_net_entry(
    prepared: Prepared,
    signal_i: int,
    *,
    net_take_profit_r: float = 1.0,
    timing: str = "next_bar",
    tick: float = .01,
    end_i: Optional[int] = None,
    side_override: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Replay one entry with a target worth ``net_take_profit_r`` after costs.

    The standing target is ``entry + side * (net_take_profit_r + cost_r) *
    initial_risk`` rounded favorably.  Cost protection is price ``entry*(1 +
    side*.002)`` rounded favorably, and becomes eligible at one further
    favorable tick.  It never loosens an initial or frozen 2R-close/4ATR trail.
    Gap exits use the observed opening; data gaps are censored without a price.
    """
    if not isinstance(prepared, Prepared):
        raise TypeError("prepared must come from prepare()")
    size = len(prepared.index)
    if (isinstance(signal_i, (bool, np.bool_)) or not isinstance(signal_i, Integral)
            or not 0 <= int(signal_i) < size):
        raise ValueError("signal_i must be a valid frame position")
    signal_i = int(signal_i)
    end = _valid_end(end_i, size)
    if timing not in {"next_bar", "ohlc", "olhc"}:
        raise ValueError("timing must be next_bar, ohlc, or olhc")
    if not math.isfinite(float(net_take_profit_r)) or float(net_take_profit_r) <= 0:
        raise ValueError("net_take_profit_r must be positive and finite")
    if not math.isfinite(float(tick)) or float(tick) <= 0:
        raise ValueError("tick must be positive and finite")
    net_take_profit_r, tick = float(net_take_profit_r), float(tick)
    side_override = _side_override(side_override)
    raw_side = int(prepared.raw_side[signal_i])
    if (side_override is None and raw_side == 0) or signal_i + 1 >= end:
        return None
    side = raw_side if side_override is None else side_override
    spec = ExecutionSpec(tick=tick)
    position = _initial_position_fast(prepared.index, prepared.open, prepared.high, prepared.low,
                                      prepared.close, prepared.atr, prepared.gap, signal_i, side, spec)
    if position is None:
        return None
    entry_i = int(position["entry_i"])
    entry, risk = float(position["entry_price"]), float(position["initial_risk"])
    initial_stop, risk_frac = float(position["initial_stop"]), float(position["initial_risk_frac"])
    cost_r = spec.round_trip_cost / risk_frac
    cost_protection = _round_favorable(entry + side * spec.round_trip_cost * entry, side=side, tick=tick)
    activation_price = _round_favorable(cost_protection + side * tick, side=side, tick=tick)
    target = _round_favorable(entry + side * (net_take_profit_r + cost_r) * risk, side=side, tick=tick)
    protection_source = "initial"
    armed = False
    trigger_i: Optional[int] = None
    pending_reverse = False
    ambiguous = 0
    mfe_precision = "legacy_excludes_stopped_exit_bar"

    def reason_for_stop(*, gap: bool) -> str:
        base = "cost_be" if protection_source == "cost" else (
            "trailing_stop" if protection_source == "trail" else "initial_stop")
        return base + "_gap" if gap else base

    def decorate(trade: dict[str, Any], *, open_exit: bool) -> dict[str, Any]:
        trade.update(
            cost_r=cost_r, protection_armed=armed, be_armed=armed,
            protection_trigger_i=trigger_i, trigger_i=trigger_i,
            activation_timing=timing, cost_protection=cost_protection,
            activation_price=activation_price, target_price=target,
            net_take_profit_r=net_take_profit_r, exit_at_open=open_exit,
            full_initial_stop=(not armed and str(trade["exit_reason"]) in {"initial_stop", "initial_stop_gap"}),
            ambiguous_stop_tp=bool(ambiguous), ambiguous_stop_tp_count=ambiguous,
            entry_side_source="side_override" if side_override is not None else "raw_v6_signal",
            side_override=side_override, final_protection=float(position["protection"]),
            mfe_precision=mfe_precision, mfe_r_strategy_usable=False,
        )
        return trade

    def finish(price: float, reason: str, i: int, *, open_exit: bool, censored: bool = False) -> dict[str, Any]:
        if censored and not math.isfinite(price):
            trade = _gap_censor(position, exit_i=i, exit_time=prepared.index[i])
        else:
            trade = _close_trade(position, exit_i=i, exit_time=prepared.index[i],
                                 exit_price=float(price), reason=reason, spec=spec)
            if censored:
                trade["censored"] = True
                trade["exit_time_precision"] = "last_complete_close"
        return decorate(trade, open_exit=open_exit)

    def tighten_cost(i: int) -> None:
        nonlocal armed, trigger_i, protection_source
        if armed:
            return
        old = float(position["protection"])
        new = max(old, cost_protection) if side == 1 else min(old, cost_protection)
        position["protection"] = new
        if (side == 1 and new > old + _EPS) or (side == -1 and new < old - _EPS):
            protection_source = "cost"
        armed, trigger_i = True, i

    def update_close(close: float, atr: float) -> None:
        nonlocal protection_source
        current_r = side * (close - entry) / risk
        position["trail_armed"] = bool(position["trail_armed"]) or current_r >= spec.arm_r
        if bool(position["trail_armed"]) and math.isfinite(atr) and atr > 0:
            candidate = _round_frozen_trail(close - side * spec.trail_atr * atr, side=side, tick=tick)
            old = float(position["protection"])
            new = max(old, candidate) if side == 1 else min(old, candidate)
            position["protection"] = new
            if (side == 1 and new > old + _EPS) or (side == -1 and new < old - _EPS):
                protection_source = "trail"

    def favorable_reaches(price: float) -> bool:
        return price >= activation_price - _EPS if side == 1 else price <= activation_price + _EPS

    for i in range(entry_i, end):
        if bool(prepared.gap[i]):
            return finish(math.nan, "data_gap_censored", i, open_exit=False, censored=True)
        opening, high, low, close, atr = (float(values[i]) for values in
                                          (prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr))
        protection = float(position["protection"])
        if pending_reverse:
            return finish(opening, reason_for_stop(gap=True) if _stop_gap(side, opening, protection)
                          else "opposite_v6_next_open", i, open_exit=True)

        # At a real opening an already-active stop wins; a TP is only then a
        # standing favorable limit at that observed price.
        opening_stop, opening_tp = _stop_gap(side, opening, protection), _target_gap(side, opening, target)
        if opening_stop:
            if opening_tp:
                ambiguous += 1
            return finish(opening, reason_for_stop(gap=True), i, open_exit=True)
        if opening_tp:
            position["mfe_r"] = max(float(position["mfe_r"]), side * (target - entry) / risk)
            mfe_precision = "capped_at_fixed_take_profit"
            return finish(opening, "take_profit_gap", i, open_exit=True)

        if timing == "next_bar":
            stopped, hit_tp = _stop_hit(side, high, low, protection), _target_hit(side, high, low, target)
            if stopped:
                if hit_tp:
                    ambiguous += 1
                price = min(opening, protection) if side == 1 else max(opening, protection)
                return finish(price, reason_for_stop(gap=_stop_gap(side, opening, protection)), i,
                              open_exit=_stop_gap(side, opening, protection))
            if hit_tp:
                position["mfe_r"] = max(float(position["mfe_r"]), side * (target - entry) / risk)
                mfe_precision = "capped_at_fixed_take_profit"
                return finish(target, "take_profit", i, open_exit=False)
            favorable = high if side == 1 else low
            position["mfe_r"] = max(float(position["mfe_r"]), side * (favorable - entry) / risk)
            if favorable_reaches(favorable):
                tighten_cost(i)
        else:
            # A favorable opening already satisfies the explicitly modeled
            # activation condition.  It cannot itself hit the lower/higher
            # protection because the opening-stop check above already ran.
            if favorable_reaches(opening):
                tighten_cost(i)
            nodes = (opening, high, low, close) if timing == "ohlc" else (opening, low, high, close)
            current = nodes[0]
            closed: Optional[dict[str, Any]] = None
            for end_price in nodes[1:]:
                while True:
                    events: list[tuple[float, int, str, float]] = []
                    stop_t = _cross_parameter(current, end_price, float(position["protection"]))
                    if stop_t is not None:
                        events.append((stop_t, 0, "stop", float(position["protection"])))
                    target_t = _cross_parameter(current, end_price, target)
                    if target_t is not None:
                        events.append((target_t, 1, "target", target))
                    activation_t = _cross_parameter(current, end_price, activation_price)
                    favorable_motion = (end_price > current + _EPS if side == 1 else end_price < current - _EPS)
                    if not armed and favorable_motion and activation_t is not None:
                        events.append((activation_t, 2, "activate", activation_price))
                    if not events:
                        current = end_price
                        break
                    _, _, kind, price = min(events)
                    if kind == "stop":
                        closed = finish(price, reason_for_stop(gap=False), i, open_exit=False)
                        break
                    if kind == "target":
                        position["mfe_r"] = max(float(position["mfe_r"]), side * (target - entry) / risk)
                        mfe_precision = "capped_at_fixed_take_profit"
                        closed = finish(price, "take_profit", i, open_exit=False)
                        break
                    tighten_cost(i)
                    current = price
                if closed is not None:
                    break
            if closed is not None:
                return closed
            favorable = high if side == 1 else low
            position["mfe_r"] = max(float(position["mfe_r"]), side * (favorable - entry) / risk)

        update_close(close, atr)
        pending_reverse = int(prepared.raw_side[i]) == -side

    last = end - 1
    return finish(float(prepared.close[last]), "boundary_mark", last, open_exit=False, censored=True)


# A short alias keeps callers' per-entry naming consistent with the older replay.
replay_entry = replay_net_entry

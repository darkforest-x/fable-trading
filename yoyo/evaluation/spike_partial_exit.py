"""Bounded, research-only partial-exit replay for an already-admitted V8 entry.

This module shares :class:`Prepared` inputs and the frozen V6 initial-position
helper with ``spike_recovery_exit``.  It neither chooses entries nor reads
market data.  Each call replays one independent next-open position so a cash
simulator, rather than an altered exit, remains responsible for serial entry
admission.

Partial and final targets are standing favorable limit orders.  A target that
is already crossed at an observed opening fills at that opening; a pending raw
V6 reverse still closes every remaining unit first.  Protective stops use the
frozen adverse-open convention and win an unknown intrabar stop/limit tie.
Close-derived protection, trailing, and residual stops apply only from the
following bar.
"""
from __future__ import annotations

import math
from numbers import Integral
from typing import Any, Optional

import numpy as np

from yoyo.evaluation.spike_recovery_exit import (
    Prepared,
    _round_favorable,
    _round_frozen_trail,
    _stop_gap,
    _stop_hit,
    _target_gap,
    _target_hit,
    _valid_end,
    replay_entry,
)
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec
from yoyo.evaluation.spike_v7_fast import _initial_position_fast

_EPS = 1e-12


def _validate_side_override(side_override: Optional[int]) -> Optional[int]:
    if side_override is None:
        return None
    if (isinstance(side_override, (bool, np.bool_)) or not isinstance(side_override, Integral)
            or int(side_override) not in (-1, 1)):
        raise ValueError("side_override must be +1, -1, or None")
    return int(side_override)


def replay_partial(
    prepared: Prepared,
    signal_i: int,
    *,
    partial_r: float = 1.0,
    partial_fraction: float = .5,
    final_tp_r: Optional[float] = 3.0,
    after_partial_stop_r: Optional[float | str] = None,
    protection_mode: str = "none",
    trigger_r: Optional[float] = None,
    trail_atr: float = 4.0,
    tick: float = .01,
    end_i: Optional[int] = None,
    side_override: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Replay one entry with an optional first partial take-profit.

    ``partial_fraction=0`` with the frozen 4-ATR trail delegates to
    :func:`replay_entry`, preserving its exact no-partial behavior.  A numeric
    ``after_partial_stop_r`` is a gross initial-R residual stop; ``"net_be"``
    installs, after the partial bar closes, the residual price that covers the
    full trade's one 20 bp round-trip cost at the current partial realization.
    The cost is charged once to aggregate trade R, never once per fill.

    ``end_i`` is exclusive.  At the boundary a residual position is marked at
    the last complete close and flagged ``censored``.  The fields
    ``realized_partial_net_r`` and ``residual_mark_net_r`` allocate the same
    single proportional total cost between the realized partial and marked
    residual; callers must exclude censored outcomes from natural win/streak
    and reset decisions.
    """
    if not isinstance(prepared, Prepared):
        raise TypeError("prepared must come from prepare()")
    size = len(prepared.index)
    if (isinstance(signal_i, (bool, np.bool_)) or not isinstance(signal_i, Integral)
            or not 0 <= int(signal_i) < size):
        raise ValueError("signal_i must be a valid frame position")
    signal_i = int(signal_i)
    end = _valid_end(end_i, size)
    if protection_mode not in {"none", "entry", "cost"}:
        raise ValueError("protection_mode must be none, entry, or cost")
    if not math.isfinite(float(tick)) or float(tick) <= 0:
        raise ValueError("tick must be positive and finite")
    tick = float(tick)
    if not math.isfinite(float(partial_fraction)) or not 0 <= float(partial_fraction) < 1:
        raise ValueError("partial_fraction must be finite in [0, 1)")
    partial_fraction = float(partial_fraction)
    if not math.isfinite(float(partial_r)) or float(partial_r) <= 0:
        raise ValueError("partial_r must be positive and finite")
    partial_r = float(partial_r)
    if final_tp_r is not None and (not math.isfinite(float(final_tp_r)) or float(final_tp_r) <= 0):
        raise ValueError("final_tp_r must be positive and finite or None")
    if final_tp_r is not None:
        final_tp_r = float(final_tp_r)
        if partial_fraction > 0 and final_tp_r <= partial_r:
            raise ValueError("final_tp_r must exceed partial_r when partial_fraction is positive")
    if not math.isfinite(float(trail_atr)) or float(trail_atr) <= 0:
        raise ValueError("trail_atr must be positive and finite")
    trail_atr = float(trail_atr)
    if protection_mode == "none":
        if trigger_r is not None:
            raise ValueError("trigger_r requires entry or cost protection")
    elif trigger_r is None or not math.isfinite(float(trigger_r)) or float(trigger_r) < 0:
        raise ValueError("entry and cost protection modes require non-negative trigger_r")
    else:
        trigger_r = float(trigger_r)
    if after_partial_stop_r is not None:
        if after_partial_stop_r == "net_be":
            pass
        elif (isinstance(after_partial_stop_r, str)
              or not math.isfinite(float(after_partial_stop_r))
              or float(after_partial_stop_r) < 0):
            raise ValueError("after_partial_stop_r must be non-negative R, net_be, or None")
        else:
            after_partial_stop_r = float(after_partial_stop_r)
    side_override = _validate_side_override(side_override)

    # This branch is an intentional behavioral identity contract.  The
    # residual-stop argument is irrelevant without a partial fill.
    if partial_fraction == 0 and trail_atr == 4:
        legacy = replay_entry(prepared, signal_i, take_profit_r=final_tp_r,
                              protection_mode=protection_mode, trigger_r=trigger_r,
                              tick=tick, end_i=end_i, side_override=side_override)
        if legacy is None:
            return None
        gross = float(legacy["gross_r"])
        cost_r = float(legacy["cost_r"])
        fill_r = gross if math.isfinite(gross) else math.nan
        legacy.update(
            fills=[{"fraction": 1.0, "price": legacy["exit_price"], "fill_r": fill_r,
                    "gross_r_contribution": gross, "allocated_cost_r": cost_r,
                    "reason": legacy["exit_reason"], "exit_i": legacy["exit_i"],
                    "exit_time": legacy["exit_time"], "exit_at_open": legacy["exit_at_open"]}],
            partial_executed=False,
            full_initial_stop=str(legacy["exit_reason"]) in {"initial_stop", "initial_stop_gap"},
            realized_partial_net_r=0.0,
            residual_mark_net_r=float(legacy["net_r"]),
            partial_fraction=0.0,
            partial_r=partial_r,
            final_tp_r=final_tp_r,
            after_partial_stop_r=after_partial_stop_r,
            trail_atr=trail_atr,
            aggregate_cost_r=cost_r,
        )
        return legacy

    raw_signal_side = int(prepared.raw_side[signal_i])
    if (side_override is None and raw_signal_side == 0) or signal_i + 1 >= end:
        return None
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
    cost_r = spec.round_trip_cost / risk_frac
    partial_target = _round_favorable(entry + side * partial_r * risk, side=side, tick=tick)
    final_target = (math.nan if final_tp_r is None else _round_favorable(
        entry + side * final_tp_r * risk, side=side, tick=tick))

    remaining = 1.0
    partial_executed = False
    has_partial = partial_fraction > _EPS
    protection_armed = False
    after_partial_armed = False
    protection_trigger_count = 0
    protection_trigger_i: Optional[int] = None
    pending_reverse = False
    ambiguous = 0
    fills: list[dict[str, Any]] = []
    mfe_precision = "legacy_excludes_stopped_exit_bar"

    def fill_r_at(price: float) -> float:
        return side * (float(price) - entry) / risk

    def add_fill(fraction: float, price: float, reason: str, i: int, *, open_exit: bool) -> None:
        nonlocal remaining
        fraction = min(float(fraction), remaining)
        if fraction <= _EPS:
            return
        fill_r = fill_r_at(price) if math.isfinite(float(price)) else math.nan
        fills.append({
            "fraction": fraction, "price": float(price), "fill_r": fill_r,
            "gross_r_contribution": fraction * fill_r if math.isfinite(fill_r) else math.nan,
            "allocated_cost_r": fraction * cost_r,
            "reason": reason, "exit_i": i, "exit_time": prepared.index[i], "exit_at_open": open_exit,
        })
        remaining = max(0.0, remaining - fraction)

    def aggregate() -> tuple[float, float, float, float]:
        gross = sum(float(row["gross_r_contribution"]) for row in fills)
        net = gross - cost_r
        return gross, net, gross * risk_frac, gross * risk_frac - spec.round_trip_cost

    def finish(price: float, reason: str, i: int, *, open_exit: bool, censored: bool = False) -> dict[str, Any]:
        nonlocal remaining
        if remaining > _EPS:
            add_fill(remaining, price, reason, i, open_exit=open_exit)
        # Every terminal route, including a censored data gap, accounts for all
        # units.  A NaN residual causes aggregate P&L to remain intentionally
        # unknown rather than silently dropping the open fraction.
        gross_r, net_r, gross_return, net_return = aggregate()
        realized_partial = sum(
            float(row["gross_r_contribution"]) - float(row["allocated_cost_r"])
            for row in fills if row["reason"].startswith("partial_take_profit")
        )
        residual_rows = [row for row in fills if not row["reason"].startswith("partial_take_profit")]
        residual_net = sum(
            float(row["gross_r_contribution"]) - float(row["allocated_cost_r"]) for row in residual_rows
        )
        trade: dict[str, Any] = {
            **position,
            "exit_i": i, "exit_time": prepared.index[i], "exit_price": float(price),
            "exit_reason": reason, "gross_return": gross_return, "net_return": net_return,
            "gross_r": gross_r, "net_r": net_r, "censored": censored,
            "exit_time_precision": "last_complete_close" if censored else "bar_open_or_intrabar_window",
            "exit_at_open": open_exit,
            "final_protection": float(position["protection"]),
            "protection_mode": protection_mode, "trigger_r": trigger_r,
            "protection_trigger_r": trigger_r, "protection_armed": protection_armed,
            "protection_trigger_count": protection_trigger_count,
            "protection_trigger_i": protection_trigger_i,
            "protection_price": float(position["protection"]),
            "take_profit_r": final_tp_r, "target_price": final_target,
            "partial_target_price": partial_target, "partial_r": partial_r,
            "partial_fraction": partial_fraction, "partial_executed": partial_executed,
            "after_partial_stop_r": after_partial_stop_r, "after_partial_armed": after_partial_armed,
            "be_armed": protection_armed or after_partial_armed,
            "ambiguous_stop_tp": bool(ambiguous), "ambiguous_stop_tp_count": ambiguous,
            "mfe_precision": mfe_precision, "mfe_r_strategy_usable": False,
            "cost_r": cost_r, "aggregate_cost_r": cost_r,
            "entry_side_source": "side_override" if side_override is not None else "raw_v6_signal",
            "side_override": side_override, "trail_atr": trail_atr,
            "fills": fills, "full_initial_stop": (not partial_executed and reason in {"initial_stop", "initial_stop_gap"}),
            "realized_partial_net_r": realized_partial,
            "residual_mark_net_r": residual_net,
        }
        if censored and math.isnan(float(price)):
            trade["exit_time_precision"] = "unknown_gap"
        return trade

    def install_after_partial_stop() -> None:
        nonlocal after_partial_armed
        if not partial_executed or after_partial_armed or after_partial_stop_r is None or remaining <= _EPS:
            return
        realized_gross = sum(float(row["gross_r_contribution"]) for row in fills)
        if after_partial_stop_r == "net_be":
            stop_r = (cost_r - realized_gross) / remaining
        else:
            stop_r = float(after_partial_stop_r)
        candidate = _round_favorable(entry + side * stop_r * risk, side=side, tick=tick)
        old = float(position["protection"])
        position["protection"] = max(old, candidate) if side == 1 else min(old, candidate)
        after_partial_armed = True

    for i in range(entry_i, end):
        if bool(prepared.gap[i]):
            return finish(math.nan, "data_gap_censored", i, open_exit=False, censored=True)
        opening, high, low, close, atr = (float(values[i]) for values in
                                          (prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr))
        protection = float(position["protection"])
        if pending_reverse:
            if _stop_gap(side, opening, protection):
                reason = "trailing_stop_gap" if protection != initial_stop else "initial_stop_gap"
            else:
                reason = "opposite_v6_next_open"
            return finish(opening, reason, i, open_exit=True)

        # A pre-existing protection is known before this opening.  In the rare
        # case it straddles a standing limit at the same opening (for example a
        # high cost-covering residual stop), use the conservative stop gap
        # rather than claim both fills at an unknowable opening path.
        opening_hits_partial = has_partial and not partial_executed and _target_gap(
            side, opening, partial_target)
        opening_hits_final = final_tp_r is not None and _target_gap(side, opening, final_target)
        if _stop_gap(side, opening, protection):
            if opening_hits_partial or opening_hits_final:
                ambiguous += 1
            reason = "trailing_stop_gap" if protection != initial_stop else "initial_stop_gap"
            return finish(opening, reason, i, open_exit=True)

        # Observed opening limits precede the unknown later path.  If an open
        # crosses final TP before partial TP was filled, both standing limits
        # receive the same observed favorable opening.
        if opening_hits_final:
            if has_partial and not partial_executed:
                add_fill(partial_fraction, opening, "partial_take_profit_gap", i, open_exit=True)
                partial_executed = True
            position["mfe_r"] = max(float(position["mfe_r"]), fill_r_at(final_target))
            mfe_precision = "capped_at_fixed_take_profit"
            return finish(opening, "take_profit_gap", i, open_exit=True)
        if opening_hits_partial:
            add_fill(partial_fraction, opening, "partial_take_profit_gap", i, open_exit=True)
            partial_executed = True

        protection = float(position["protection"])
        stopped = _stop_hit(side, high, low, protection)
        hit_partial = has_partial and not partial_executed and _target_hit(side, high, low, partial_target)
        hit_final = final_tp_r is not None and _target_hit(side, high, low, final_target)
        if stopped:
            if hit_partial or hit_final:
                ambiguous += 1
            price = min(opening, protection) if side == 1 else max(opening, protection)
            reason = "trailing_stop" if protection != initial_stop else "initial_stop"
            if _stop_gap(side, opening, protection):
                reason += "_gap"
            return finish(price, reason, i, open_exit=reason.endswith("_gap"))
        if hit_final:
            if has_partial and not partial_executed:
                add_fill(partial_fraction, partial_target, "partial_take_profit", i, open_exit=False)
                partial_executed = True
            position["mfe_r"] = max(float(position["mfe_r"]), fill_r_at(final_target))
            mfe_precision = "capped_at_fixed_take_profit"
            return finish(final_target, "take_profit", i, open_exit=False)
        if hit_partial:
            add_fill(partial_fraction, partial_target, "partial_take_profit", i, open_exit=False)
            partial_executed = True
            position["mfe_r"] = max(float(position["mfe_r"]), fill_r_at(partial_target))

        favorable = high if side == 1 else low
        favorable_r = fill_r_at(favorable)
        position["mfe_r"] = max(float(position["mfe_r"]), favorable_r)
        if protection_mode != "none" and not protection_armed:
            required_r = float(trigger_r) if protection_mode == "entry" else cost_r + float(trigger_r)
            if favorable_r >= required_r:
                candidate = (entry if protection_mode == "entry"
                             else _round_favorable(entry + side * spec.round_trip_cost * entry, side=side, tick=tick))
                old = float(position["protection"])
                position["protection"] = max(old, candidate) if side == 1 else min(old, candidate)
                protection_armed = True
                protection_trigger_count += 1
                protection_trigger_i = i
        current_r = fill_r_at(close)
        position["trail_armed"] = bool(position["trail_armed"]) or current_r >= spec.arm_r
        if bool(position["trail_armed"]) and math.isfinite(atr) and atr > 0:
            candidate = _round_frozen_trail(close - side * trail_atr * atr, side=side, tick=tick)
            old = float(position["protection"])
            position["protection"] = max(old, candidate) if side == 1 else min(old, candidate)
        # A post-partial residual stop is observed only after this bar closes.
        install_after_partial_stop()
        pending_reverse = int(prepared.raw_side[i]) == -side

    last = end - 1
    return finish(float(prepared.close[last]), "boundary_mark", last, open_exit=False, censored=True)

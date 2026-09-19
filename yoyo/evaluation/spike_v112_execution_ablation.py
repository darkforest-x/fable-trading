"""Fixed-entry execution ablations for the SPIKE V11.2 offline replay.

This module changes no signal, entry, cost, or serial-account rule.  Its
public ``attempt_variant`` function accepts an already prepared frozen V9
stream and one joint candidate ``i``.  ``baseline`` delegates directly to the
published V10.4 increment attempt.  ``parent_stop`` keeps that candidate's
next-open entry but replaces only its initial stop with the stop which the
causal parent index would have had under the original V9 initial-stop rule.
``wick_arm`` keeps the original stop and changes only the 2R trail arm test
from close to the completed bar's favourable high; its close-4ATR protection
still becomes active only for the following bar.

It is an offline research seam.  It never reads data, derives candidates,
changes live execution, or supplies a fallback stop when the requested parent
anchor is invalid.
"""
from __future__ import annotations

import math
from numbers import Integral

import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v10_4_increment as increment
from yoyo.evaluation import spike_v1_v8_be05 as fixed
from yoyo.evaluation.spike_v7_fast import _initial_position_fast


ARMS = ("baseline", "parent_stop", "wick_arm")


def _status(result: dict[str, object]) -> str:
    """Classify a complete fixed-entry result using increment's public statuses."""
    if not bool(result["censored"]):
        return "closed"
    return "censored_boundary" if result["exit_reason"] == "boundary_mark" else "censored_gap"


def _joint_initial(prepared: fixed.PreparedArm, i: int) -> tuple[str, dict[str, object] | None]:
    """Build the unchanged long joint entry, preserving increment's boundary statuses."""
    if i + 1 >= len(prepared.frame):
        return "no_next_bar", None
    if bool(prepared.gap[i + 1]):
        return "next_bar_is_gap", None
    row = _initial_position_fast(prepared.frame.index, prepared.open, prepared.high, prepared.low,
                                 prepared.close, prepared.atr, prepared.gap, i, 1, prepared.spec)
    return ("risk_invalid", None) if row is None else ("ready", row)


def _parent_stop_row(prepared: fixed.PreparedArm, joint: dict[str, object], i: int,
                     parent_i: int | None) -> tuple[str, dict[str, object] | None]:
    """Replace only ``joint``'s stop with a causally available parent anchor.

    ``parent_i`` may be a non-signal historical index for a matched random
    control.  The anchor itself is nevertheless calculated with the original
    V9 initial-position function, so its five bars and ATR end at ``parent_i``.
    A bad anchor is a rejected candidate, never a substitute for the joint
    stop.  The joint candidate retains its own actual next-open entry.
    """
    if (parent_i is None or isinstance(parent_i, bool) or not isinstance(parent_i, Integral)
            or parent_i < 0 or parent_i > i):
        return "parent_stop_invalid", None
    parent_i = int(parent_i)
    parent = _initial_position_fast(prepared.frame.index, prepared.open, prepared.high, prepared.low,
                                    prepared.close, prepared.atr, prepared.gap, parent_i, 1, prepared.spec)
    if parent is None:
        return "parent_stop_invalid", None
    stop, entry = float(parent["initial_stop"]), float(joint["entry_price"])
    risk = entry - stop
    # Both prices originate on the exchange tick grid.  A sub-tick residual at
    # the scale of binary floating point is an equal stop/entry, not a viable
    # risk denominator.  The tolerance is 1e-8 of one tick, so a legitimate
    # one-tick stop remains accepted by a factor of 100 million.
    numerical_zero = float(prepared.spec.tick) * 1e-8
    if (not all(math.isfinite(value) for value in (stop, entry, risk)) or stop <= 0 or entry <= 0
            or risk <= numerical_zero):
        return "parent_stop_invalid", None
    out = dict(joint)
    out["initial_stop"] = stop
    out["initial_risk"] = risk
    out["initial_risk_frac"] = risk / entry
    out["protection"] = stop
    return "ready", out


def _wick_replay(prepared: fixed.PreparedArm, row: dict[str, object]) -> dict[str, object]:
    """Replay one long entry with only high-based 2R arming changed.

    The stop check occurs before the close update.  Thus a bar that touches an
    already active stop exits at its established price and cannot use its own
    high to manufacture a trail.  Conversely, a surviving bar's high can arm
    the original close-4ATR trail for the following bar only.
    """
    context, cohort, frame, gap, raw_side, spec = (prepared.context, prepared.cohort, prepared.frame,
                                                    prepared.gap, prepared.raw_side, prepared.spec)
    oa, ha, la, ca, aa = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr
    side = int(row["side"])
    if side != 1:
        raise ValueError("wick_arm is defined for long entries only")
    start = int(frame.index.get_loc(pd.Timestamp(row["entry_time"])))
    pos = {key: row[key] for key in ("signal_i", "signal_bar_open", "entry_i", "entry_time", "side",
                                     "entry_price", "initial_stop", "initial_risk", "initial_risk_frac")}
    pos["frozen_index_offset"] = int(row["entry_i"]) - start
    pos = base._new_trade(pos, trade_id=f"{context.key}:wick_arm:fixed", cohort=cohort,
                          policy="wick_arm", context=context)
    pos.update(protection=float(row["initial_stop"]), mfe_r=0., trail_armed=False,
               be_armed=False, be_trigger_count=0)
    pending_reverse: int | None = None
    for bar_i in range(start, len(frame)):
        stamp = frame.index[bar_i]
        if bool(gap[bar_i]):
            pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_reason"] = bar_i, stamp, "data_gap_censored"
            return base._trade_row(pos, censored=True, precision="unknown_gap") | {
                "be_armed": False, "be_trigger_count": 0, "protection": float(pos["protection"]),
            }
        protection = float(pos["protection"])
        if pending_reverse is not None:
            if side == pending_reverse:
                price = float(oa[bar_i])
                reason = (("trailing_stop_gap" if protection != float(pos["initial_stop"]) else "initial_stop_gap")
                          if price <= protection else "opposite_v6_next_open")
                pos["qty_realized"], pos["qty_remaining"] = 1., 0.
                pos["realized_gross_return"] = price / float(pos["entry_price"]) - 1
                pos["realized_net_return"] = pos["realized_gross_return"] - base.ENTRY_COST - base.EXIT_COST
                pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = bar_i, stamp, price, reason
                return base._trade_row(pos, censored=False, precision="bar_open_or_intrabar_window") | {
                    "be_armed": False, "be_trigger_count": 0, "protection": protection,
                }
            pending_reverse = None
        if la[bar_i] <= protection:
            price = min(float(oa[bar_i]), protection)
            reason = "trailing_stop" if protection != float(pos["initial_stop"]) else "initial_stop"
            if float(oa[bar_i]) <= protection:
                reason += "_gap"
            pos["qty_realized"], pos["qty_remaining"] = 1., 0.
            pos["realized_gross_return"] = price / float(pos["entry_price"]) - 1
            pos["realized_net_return"] = pos["realized_gross_return"] - base.ENTRY_COST - base.EXIT_COST
            pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = bar_i, stamp, price, reason
            return base._trade_row(pos, censored=False, precision="bar_open_or_intrabar_window") | {
                "be_armed": False, "be_trigger_count": 0, "protection": protection,
            }
        entry, risk = float(pos["entry_price"]), float(pos["initial_risk"])
        high_r = (float(ha[bar_i]) - entry) / risk
        pos["mfe_r"] = max(float(pos["mfe_r"]), high_r)
        pos["trail_armed"] = bool(pos["trail_armed"]) or high_r >= spec.arm_r
        if bool(pos["trail_armed"]) and math.isfinite(float(aa[bar_i])) and float(aa[bar_i]) > 0:
            raw = float(ca[bar_i]) - spec.trail_atr * float(aa[bar_i])
            candidate = math.floor(raw / spec.tick) * spec.tick
            pos["protection"] = max(float(pos["protection"]), candidate)
        if int(raw_side[bar_i]) == -side:
            pending_reverse = side
    last = len(frame) - 1
    pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = last, frame.index[last], ca[last], "boundary_mark"
    return base._trade_row(pos, censored=True, precision="last_complete_close") | {
        "be_armed": False, "be_trigger_count": 0, "protection": float(pos["protection"]),
    }


def attempt_variant(prepared: fixed.PreparedArm, i: int, parent_i: int | None, arm: str) -> tuple[str, dict[str, object] | None]:
    """Replay one V11.2 candidate under one isolated execution arm.

    Args:
        prepared: Frozen V9 prepared arrays supplied by the caller.
        i: Joint candidate close index; entry remains at ``i + 1``'s open.
        parent_i: Causal V9-parent (or matched-control surrogate) index for
            ``parent_stop``.  It must be an integer at or before ``i``.
        arm: One of ``baseline``, ``parent_stop``, or ``wick_arm``.

    Returns:
        The normal V10.4 attempt status and its inc.attempt-compatible trade
        dictionary, or ``None`` for an untaken candidate.  Parent-anchor
        rejection is explicitly ``parent_stop_invalid``.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown execution arm: {arm}")
    if arm == "baseline":
        # Deliberately do not reconstruct the frozen arm: this is the parity arm.
        return increment.attempt(prepared, i)
    status, joint = _joint_initial(prepared, i)
    if joint is None:
        return status, None
    if arm == "parent_stop":
        status, row = _parent_stop_row(prepared, joint, i, parent_i)
        if row is None:
            return status, None
        result = fixed.replay_fixed_entry(prepared.context, pd.Series(row), arm="v8", enable_be=False, prepared=prepared)
        return _status(result), result
    result = _wick_replay(prepared, joint)
    return _status(result), result

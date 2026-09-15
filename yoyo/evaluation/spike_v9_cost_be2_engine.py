"""Causal V9 net-2R cost-break-even replay for frozen SPIKE V1-common/V8.

The entry, initial stop, old close-2R/4ATR trail, raw V6 next-open reversal,
gap censoring, and same-direction terminal-bar suppression are canonical.
This version adds one close-confirmed, next-bar-effective protection: a
surviving bar may lock entry plus/minus 20bp only when its favourable wick,
in actual next-open initial-R units, remains at least 2R after deducting
``0.002 * entry / risk``.  This research module has no CLI or market loader.
"""
from __future__ import annotations

from dataclasses import replace
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation.spike_v8_replay import v8_admissions


BE_TRIGGER_NET_R = 2.0
ROUND_TRIP_COST = 0.002
ARMS = ("v1_common_execution_long", "v8")
KEY = ["signal_i", "entry_i", "side", "exit_i", "exit_reason", "entry_price", "exit_price", "initial_stop", "initial_risk", "net_return", "net_r"]
BE_COLUMNS = ["be_armed", "be_trigger_count", "be_trigger_bar_open", "be_gross_favourable_r", "be_net_favourable_r", "be_lock_price", "be_lock_changed"]
TRADE_COLUMNS = [*base.TRADE_COLUMNS, *BE_COLUMNS]
FIXED_COLUMNS = TRADE_COLUMNS


def v8_context(context: base.StreamContext) -> base.StreamContext:
    """Inject only V8's frozen admission while retaining raw opposite exits."""
    gates = v8_admissions(context)
    cache = dict(context.cache)
    cache["bb"] = context.cache["bb"].copy()
    cache["bb"]["prior_squeeze_run3"] = gates.v8.reindex(cache["bb"].index).fillna(False).astype(bool)
    return replace(context, cache=cache)


def arm_context(context: base.StreamContext, arm: str) -> tuple[base.StreamContext, str]:
    """Map each frozen entry contract to its one allowed replay cohort."""
    if arm == "v1_common_execution_long":
        return context, "v1_common_long"
    if arm == "v8":
        return v8_context(context), "v7_both"
    raise ValueError(f"unsupported arm: {arm}")


@dataclass(frozen=True)
class PreparedArm:
    """One cached arm context, preventing per-entry V8 admission recomputation."""

    context: base.StreamContext
    arm: str
    cohort: str
    frame: pd.DataFrame
    gap: np.ndarray
    allowed: np.ndarray
    raw_side: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray
    ordinal: dict[pd.Timestamp, int]
    spec: base.ExecutionSpec


def prepare_arm(context: base.StreamContext, *, arm: str) -> PreparedArm:
    """Materialize immutable per-stream/arm arrays once for serial and paired paths."""
    arm_context_value, cohort = arm_context(context, arm)
    frame = arm_context_value.cache["bars"]
    signals, allowed = base._cohort_inputs(arm_context_value, cohort)
    long, short = signals.long_signal.fillna(False).to_numpy(bool), signals.short_signal.fillna(False).to_numpy(bool)
    if (long & short).any():
        raise ValueError("ambiguous frozen signal side")
    arrays = {name: frame[name].to_numpy(float) for name in ("open", "high", "low", "close", "atr")}
    return PreparedArm(arm_context_value, arm, cohort, frame,
                       arm_context_value.cache["data_gap"].reindex(frame.index).fillna(True).astype(bool).to_numpy(bool),
                       allowed.to_numpy(bool), np.where(long, 1, np.where(short, -1, 0)),
                       arrays["open"], arrays["high"], arrays["low"], arrays["close"], arrays["atr"],
                       dict(zip(pd.to_datetime(arm_context_value.signals_ledger.signal_bar_open, utc=True), arm_context_value.signals_ledger.signal_i.astype(int))),
                       base.ExecutionSpec(tick=float(arm_context_value.cache["tick"])))


def _favourable_r(side: int, entry: float, risk: float, high: float, low: float) -> float:
    """Return this observed bar's favourable excursion in frozen initial-R units."""
    favourable = high if side == 1 else low
    return side * (favourable - entry) / risk


def _be_touched(side: int, entry: float, risk: float, high: float, low: float) -> bool:
    """Test current-bar net favourable R; 20bp is fixed, not a fill estimate."""
    return (math.isfinite(risk) and risk > 0
            and _favourable_r(side, entry, risk, high, low) - ROUND_TRIP_COST * entry / risk >= BE_TRIGGER_NET_R)


def _cost_be_level(side: int, entry: float, tick: float) -> float:
    """Round the 20bp fee lock conservatively towards the protected loss side."""
    raw = entry * (1.002 if side == 1 else .998)
    return math.ceil(raw / tick) * tick if side == 1 else math.floor(raw / tick) * tick


def _raise_to_cost_be(position: dict[str, object], tick: float) -> bool:
    """Ratchet to the tick-rounded cost lock only if it tightens protection."""
    before, entry, side = float(position["protection"]), float(position["entry_price"]), int(position["side"])
    level = _cost_be_level(side, entry, tick)
    after = max(before, level) if side == 1 else min(before, level)
    position["protection"] = after
    position["be_lock_price"] = level
    return after != before


def _metadata(position: dict[str, object]) -> dict[str, object]:
    """Preserve the candidate's trigger audit fields on every terminal row."""
    return {name: position.get(name, pd.NaT if name == "be_trigger_bar_open" else math.nan)
            for name in BE_COLUMNS}


def _trade_row(position: dict[str, object], *, censored: bool, precision: str) -> dict[str, object]:
    """Keep canonical economics while making the new policy observable."""
    return base._trade_row(position, censored=censored, precision=precision) | _metadata(position)


def _close_updates(position: dict[str, object], *, high: float, low: float, close: float, atr: float,
                   spec: base.ExecutionSpec, enable_be: bool, events: list[dict[str, object]],
                   context: base.StreamContext, arm: str, policy: str, i: int) -> None:
    """Apply close-confirmed BE then unchanged trail, both effective next bar.

    Inputs are high/low/close from the observed bar.  The current bar's stop
    was already tested by the caller, so a trigger cannot create a same-bar
    retroactive exit.
    """
    side, entry, risk = int(position["side"]), float(position["entry_price"]), float(position["initial_risk"])
    position["mfe_r"] = max(float(position["mfe_r"]), _favourable_r(side, entry, risk, high, low))
    gross_r = _favourable_r(side, entry, risk, high, low)
    net_r = gross_r - ROUND_TRIP_COST * entry / risk
    if enable_be and not bool(position.get("be_armed", False)) and _be_touched(side, entry, risk, high, low):
        position["be_armed"] = True
        position["be_trigger_count"] = int(position.get("be_trigger_count", 0)) + 1
        position["be_trigger_bar_open"] = context.cache["bars"].index[i]
        position["be_gross_favourable_r"] = gross_r
        position["be_net_favourable_r"] = net_r
        before = float(position["protection"])
        changed = _raise_to_cost_be(position, spec.tick)
        position["be_lock_changed"] = changed
        events.append({"trade_id": position["trade_id"], "cohort": position["cohort"], "policy": policy,
                       "stream_key": context.key, "bar_open": context.cache["bars"].index[i],
                       "event_time": context.cache["bars"].index[i] + pd.Timedelta(minutes=context.minutes),
                       "execution_phase": "close", "event_kind": "protection_update",
                       "reason": "v9_cost_be2_next_bar", "protection_before": before,
                       "protection_after": float(position["protection"]), "qty_fraction": float(position["qty_remaining"]),
                       "arm": arm, "changed_protection": changed, "gross_favourable_r": gross_r,
                       "net_favourable_r": net_r, "lock_price": float(position["be_lock_price"])})
    current_r = side * (close - entry) / risk
    position["trail_armed"] = bool(position["trail_armed"]) or current_r >= spec.arm_r
    if bool(position["trail_armed"]) and math.isfinite(atr) and atr > 0:
        raw = close - side * spec.trail_atr * atr
        candidate = math.floor(raw / spec.tick) * spec.tick if side == 1 else math.ceil(raw / spec.tick) * spec.tick
        before = float(position["protection"])
        position["protection"] = max(before, candidate) if side == 1 else min(before, candidate)


def replay_serial(context: base.StreamContext, *, arm: str, enable_be: bool, prepared: PreparedArm | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay the clean serial contract, clearing an intent when its owner closes.

    ``pending_reverse`` belongs to the position that scheduled it.  Every
    terminal close, including an opening gap, therefore clears that intent
    before a subsequent entry can be considered.  This preserves the frozen
    source engine's intent lifetime while allowing the BE treatment to alter
    the sequence of later entries causally.
    """
    prepared = prepare_arm(context, arm=arm) if prepared is None else prepared
    if prepared.arm != arm:
        raise ValueError("prepared arm differs from requested arm")
    context, cohort, frame, spec = prepared.context, prepared.cohort, prepared.frame, prepared.spec
    gap, allowed, raw_side = prepared.gap, prepared.allowed, prepared.raw_side
    oa, ha, la, ca, aa, ordinal = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr, prepared.ordinal
    trades: list[dict[str, object]] = []
    fills: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    position: dict[str, object] | None = None
    pending_entry: tuple[int, int] | None = None
    pending_reverse: tuple[int, int] | None = None
    next_id = 0
    for i, stamp in enumerate(frame.index):
        ended_side = 0
        if bool(gap[i]):
            if position is not None:
                base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                                  phase="close", precision="unknown_gap", kind="censor", fraction=float(position["qty_remaining"]),
                                  price=math.nan, cost=0.0, reason="data_gap_censored", context=context)
                position["last_exit_i"], position["last_exit_time"], position["last_exit_reason"] = i, stamp, "data_gap_censored"
                trades.append(_trade_row(position, censored=True, precision="unknown_gap"))
            position = None; pending_entry = pending_reverse = None
            continue
        # A protection that was active before this open always wins a scheduled reverse.
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            stop_open = oa[i] <= protection if side == 1 else oa[i] >= protection
            if stop_open:
                reason = "trailing_stop_gap" if protection != float(position["initial_stop"]) else "initial_stop_gap"
                fraction = float(position["qty_remaining"]); gross = fraction * side * (oa[i] / float(position["entry_price"]) - 1)
                position["qty_realized"] += fraction; position["qty_remaining"] = 0.0
                position["realized_gross_return"] += gross; position["realized_net_return"] += gross - fraction * base.EXIT_COST
                position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, oa[i], reason
                base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                                  phase="open", precision="bar_open", kind="exit", fraction=fraction, price=oa[i], cost=fraction * base.EXIT_COST, reason=reason, context=context)
                trades.append(_trade_row(position, censored=False, precision="bar_open_or_intrabar_window"))
                ended_side, position, pending_reverse = side, None, None
        if position is not None and pending_reverse is not None:
            _, old_side = pending_reverse
            if int(position["side"]) == old_side:
                fraction = float(position["qty_remaining"]); gross = fraction * old_side * (oa[i] / float(position["entry_price"]) - 1)
                position["qty_realized"] += fraction; position["qty_remaining"] = 0.0
                position["realized_gross_return"] += gross; position["realized_net_return"] += gross - fraction * base.EXIT_COST
                position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, oa[i], "opposite_v6_next_open"
                base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                                  phase="open", precision="bar_open", kind="exit", fraction=fraction, price=oa[i], cost=fraction * base.EXIT_COST, reason="opposite_v6_next_open", context=context)
                trades.append(_trade_row(position, censored=False, precision="bar_open_or_intrabar_window"))
                ended_side, position = old_side, None
            pending_reverse = None
        if pending_entry is not None:
            signal_i, side = pending_entry
            if position is None and side != ended_side:
                made = base._initial_position_fast(frame.index, oa, ha, la, ca, aa, gap, signal_i, side, spec)
                if made is not None:
                    made["initial_risk_frac"] = float(made["initial_risk"]) / float(made["entry_price"])
                    original_i = ordinal.get(frame.index[signal_i])
                    if original_i is not None:
                        made["signal_i"], made["entry_i"], made["frozen_index_offset"] = original_i, original_i + 1, original_i - signal_i
                    next_id += 1
                    position = base._new_trade(made, trade_id=f"{context.key}:{arm}:{'v9_cost_be2' if enable_be else 'baseline'}:{next_id}", cohort=cohort,
                                               policy="v9_cost_be2" if enable_be else "baseline", context=context)
                    position.update(be_armed=False, be_trigger_count=0, be_trigger_bar_open=pd.NaT,
                                    be_gross_favourable_r=math.nan, be_net_favourable_r=math.nan,
                                    be_lock_price=math.nan, be_lock_changed=False)
                    base._append_fill(fills, position, leg_no=1, bar_open=stamp, event_time=stamp, phase="open", precision="bar_open",
                                      kind="entry", fraction=1., price=float(position["entry_price"]), cost=base.ENTRY_COST, reason="next_open_entry", context=context)
            pending_entry = None
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            stopped = la[i] <= protection if side == 1 else ha[i] >= protection
            if stopped:
                price = min(oa[i], protection) if side == 1 else max(oa[i], protection)
                reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
                if oa[i] <= protection if side == 1 else oa[i] >= protection: reason += "_gap"
                fraction = float(position["qty_remaining"]); gross = fraction * side * (price / float(position["entry_price"]) - 1)
                position["qty_realized"] += fraction; position["qty_remaining"] = 0.0
                position["realized_gross_return"] += gross; position["realized_net_return"] += gross - fraction * base.EXIT_COST
                position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, price, reason
                base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                                  phase="open" if reason.endswith("_gap") else "intrabar", precision="bar_open" if reason.endswith("_gap") else "within_bar",
                                  kind="exit", fraction=fraction, price=price, cost=fraction * base.EXIT_COST, reason=reason, context=context)
                trades.append(_trade_row(position, censored=False, precision="bar_open_or_intrabar_window")); ended_side, position, pending_reverse = side, None, None
            else:
                _close_updates(position, high=ha[i], low=la[i], close=ca[i], atr=aa[i], spec=spec, enable_be=enable_be,
                               events=events, context=context, arm=arm, policy="v9_cost_be2" if enable_be else "baseline", i=i)
        signal_side = int(raw_side[i])
        if signal_side:
            if position is not None and signal_side != int(position["side"]):
                pending_reverse = (i, int(position["side"]))
                if bool(allowed[i]): pending_entry = (i, signal_side)
            elif position is None and signal_side != ended_side and bool(allowed[i]):
                pending_entry = (i, signal_side)
    if position is not None:
        last, stamp = len(frame) - 1, frame.index[-1]
        base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp,
                          event_time=stamp + pd.Timedelta(minutes=context.minutes), phase="close", precision="last_complete_close",
                          kind="censor", fraction=float(position["qty_remaining"]), price=ca[last], cost=0., reason="boundary_mark", context=context)
        position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = last, stamp, ca[last], "boundary_mark"
        trades.append(_trade_row(position, censored=True, precision="last_complete_close"))
    return (pd.DataFrame(trades, columns=TRADE_COLUMNS), pd.DataFrame(fills, columns=base.FILL_COLUMNS),
            pd.DataFrame(events))


def replay_fixed_entry(context: base.StreamContext, row: pd.Series, *, arm: str, enable_be: bool, prepared: PreparedArm | None = None) -> dict[str, object]:
    """Re-evaluate exactly one original entry; no BE-created re-entry is possible."""
    prepared = prepare_arm(context, arm=arm) if prepared is None else prepared
    if prepared.arm != arm:
        raise ValueError("prepared arm differs from requested arm")
    context, cohort, frame, gap, raw_side, spec = prepared.context, prepared.cohort, prepared.frame, prepared.gap, prepared.raw_side, prepared.spec
    oa, ha, la, ca, aa = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr
    side = int(row.side); start = int(frame.index.get_loc(pd.Timestamp(row.entry_time)))
    pos = {key: row[key] for key in ("signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "entry_price", "initial_stop", "initial_risk", "initial_risk_frac")}
    # Serial output preserves full-frame ordinals even though each cache begins
    # at a shorter authenticated prefix.  Fixed exits need the same offset.
    pos["frozen_index_offset"] = int(row.entry_i) - start
    pos = base._new_trade(pos, trade_id=f"{context.key}:{arm}:fixed", cohort=cohort,
                          policy="v9_cost_be2" if enable_be else "baseline", context=context)
    pos.update(protection=float(row.initial_stop), mfe_r=0., trail_armed=False, be_armed=False,
               be_trigger_count=0, be_trigger_bar_open=pd.NaT, be_gross_favourable_r=math.nan,
               be_net_favourable_r=math.nan, be_lock_price=math.nan, be_lock_changed=False)
    pending_reverse: int | None = None
    for i in range(start, len(frame)):
        stamp = frame.index[i]
        if gap[i]:
            pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_reason"] = i, stamp, "data_gap_censored"
            return _trade_row(pos, censored=True, precision="unknown_gap") | {"protection": float(pos["protection"])}
        protection = float(pos["protection"])
        if pending_reverse is not None:
            if side == pending_reverse:
                price = oa[i]; reason = ("trailing_stop_gap" if protection != float(pos["initial_stop"]) else "initial_stop_gap") if (price <= protection if side == 1 else price >= protection) else "opposite_v6_next_open"
                pos["qty_realized"], pos["qty_remaining"] = 1., 0.; pos["realized_gross_return"] = side * (price / float(pos["entry_price"]) - 1); pos["realized_net_return"] = pos["realized_gross_return"] - base.ENTRY_COST - base.EXIT_COST
                pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, stamp, price, reason
                return _trade_row(pos, censored=False, precision="bar_open_or_intrabar_window") | {"protection": protection}
            pending_reverse = None
        if la[i] <= protection if side == 1 else ha[i] >= protection:
            price = min(oa[i], protection) if side == 1 else max(oa[i], protection); reason = "trailing_stop" if protection != float(pos["initial_stop"]) else "initial_stop"
            if oa[i] <= protection if side == 1 else oa[i] >= protection: reason += "_gap"
            pos["qty_realized"], pos["qty_remaining"] = 1., 0.; pos["realized_gross_return"] = side * (price / float(pos["entry_price"]) - 1); pos["realized_net_return"] = pos["realized_gross_return"] - base.ENTRY_COST - base.EXIT_COST
            pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, stamp, price, reason
            return _trade_row(pos, censored=False, precision="bar_open_or_intrabar_window") | {"protection": protection}
        _close_updates(pos, high=ha[i], low=la[i], close=ca[i], atr=aa[i], spec=spec, enable_be=enable_be, events=[], context=context, arm=arm, policy="v9_cost_be2", i=i)
        if int(raw_side[i]) == -side:
            pending_reverse = side
    pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = len(frame)-1, frame.index[-1], ca[-1], "boundary_mark"
    return _trade_row(pos, censored=True, precision="last_complete_close") | {"protection": float(pos["protection"])}

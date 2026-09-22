"""Reusable HTF-SMA confirmation exits for the frozen 15m SPIKE replay.

This is an offline V9 plus completed-H1-SMA60 risk-box study requested on
2026-09-22.  It deliberately does not claim parity with the V12 ``bk``
visual geometry.  ``higher`` is a frozen, already aligned array of the H1
SMA60 value visible at each 15m bar OPEN; this module never fills it from a
future value.  The caller may pass the prior HTF initial-stop transform so the
``htf_touch`` arm uses exactly the earlier risk anchor.

The ``htf_close`` and ``htf_body`` modes make the initial stop a reference
line for the structural test.  They do not treat that reference risk as a
hard maximum loss: a structure confirmation is observed at a completed 15m
close and exits at the next open.  A real 4ATR trail is still a hard stop once
its candidate is tighter than the frozen initial stop.  ``tp2`` uses a fixed
two-reference-R target and no trail.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Callable, Mapping

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_ma_stop
from yoyo.evaluation import spike_v1_v8_be05 as old_engine


STOP_MODES = ("original", "htf_touch", "htf_close", "htf_body")
PROFIT_MODES = ("trail", "tp2")
POLICIES = tuple(f"{stop}__{profit}" for stop in STOP_MODES for profit in PROFIT_MODES)
EXTRA_COLUMNS = [
    "ma_confirm_time", "ma_confirm_level", "mae_reference_r", "ambiguous_bar",
    "reference_risk_only",
]


@dataclass(frozen=True)
class _Policy:
    """Parsed execution policy and the two independent rule axes."""

    name: str
    stop_mode: str
    profit_mode: str

    @property
    def structural(self) -> bool:
        return self.stop_mode in {"htf_close", "htf_body"}

    @property
    def legacy_baseline(self) -> bool:
        return self.name == "original__trail"

    @property
    def output_policy(self) -> str:
        # Keep the old published baseline ledger fields byte-compatible for
        # the parity arm.  New arms carry their explicit policy name.
        return "baseline" if self.legacy_baseline else self.name


def _parse_policy(policy: str) -> _Policy:
    """Parse one explicit stop/profit combination and fail closed otherwise."""
    if not isinstance(policy, str) or policy not in POLICIES:
        raise ValueError(f"unsupported MA-confirm policy: {policy!r}")
    stop_mode, profit_mode = policy.split("__", 1)
    return _Policy(policy, stop_mode, profit_mode)


def _higher_array(p: old_engine.PreparedArm, higher: np.ndarray) -> np.ndarray:
    """Validate the caller's causal H1 value array without filling unknowns."""
    values = np.asarray(higher, dtype=float)
    if values.ndim != 1 or len(values) != len(p.frame):
        raise ValueError("higher must be a one-dimensional array aligned to p.frame")
    return values


def _valid_higher(value: object) -> bool:
    """Return whether one H1 SMA value is usable for a strict comparison."""
    try:
        return math.isfinite(float(value)) and float(value) > 0.0
    except (TypeError, ValueError):
        return False


def _close_time(p: old_engine.PreparedArm, i: int) -> pd.Timestamp:
    """Return the completed chart-bar time used for a frozen MA confirmation."""
    return pd.Timestamp(p.frame.index[i]) + pd.Timedelta(minutes=p.context.minutes)


def _initial_transform(
    p: old_engine.PreparedArm,
    row: Mapping[str, object],
    *,
    signal_i: int,
    side: int,
    policy: _Policy,
    higher: np.ndarray,
    callback: Callable[[dict[str, object], int, int], dict[str, object] | None] | None,
) -> dict[str, object] | None:
    """Apply the caller's prior transform or the exact internal HTF arm.

    The parent runner supplies the previous ``htf_sma60`` transform for
    ``htf_touch`` and the structural modes.  The internal fallback supports serial calls without a callback; it uses
    the raw H1 line with the prior 0.2-ATR initial stop buffer exactly once.
    Fixed replay consumes an already transformed, frozen entry row.
    """
    source = dict(row)
    if callback is not None:
        return callback(source, signal_i, side)
    if policy.stop_mode == "original":
        return source
    if not _valid_higher(higher[signal_i]):
        return None
    return spike_ma_stop.transform_initial(
        source,
        arm="htf_sma60",
        sma120=None,
        htf_sma60=float(higher[signal_i]),
        atr=float(p.atr[signal_i]),
        tick=float(p.spec.tick),
        buffer_atr=float(p.spec.stop_buffer_atr),
    )


def _target_price(entry: float, side: int, risk: float, tick: float) -> float:
    """Round a fixed 2R target in the favorable direction without shrinking it."""
    raw = entry + side * 2.0 * risk
    units = raw / tick
    rounded = math.ceil(units) if side == 1 else math.floor(units)
    return float(rounded * tick)


def _new_position(
    p: old_engine.PreparedArm,
    row: Mapping[str, object],
    *,
    policy: _Policy,
    trade_id: str,
    entry_local_i: int,
) -> dict[str, object]:
    """Initialize the shared serial/fixed position state."""
    context = p.context
    pos = dict(row)
    pos["frozen_index_offset"] = int(row["entry_i"]) - int(entry_local_i)
    pos = base._new_trade(pos, trade_id=trade_id, cohort=p.cohort,
                           policy=policy.output_policy, context=context)
    initial_stop = float(pos["initial_stop"])
    initial_risk = float(pos["initial_risk"])
    pos.update(
        protection=initial_stop,
        mfe_r=0.0,
        trail_armed=False,
        hard_stop_active=not policy.structural,
        hard_stop_is_trail=False,
        ma_confirm_time=None,
        ma_confirm_level=np.nan,
        ma_pending_reason=None,
        ma_pending_time=None,
        ma_pending_level=np.nan,
        mae_reference_r=0.0,
        ambiguous_bar=False,
        reference_risk_only=policy.structural,
        tp_price=(
            _target_price(float(pos["entry_price"]), int(pos["side"]), initial_risk, float(p.spec.tick))
            if policy.profit_mode == "tp2" else np.nan
        ),
    )
    return pos


def _result_row(pos: dict[str, object], *, censored: bool, precision: str) -> dict[str, object]:
    """Add research diagnostics to the stable base trade schema."""
    row = base._trade_row(pos, censored=censored, precision=precision)
    row.update({name: pos.get(name) for name in EXTRA_COLUMNS})
    return row


def _update_mfe(pos: dict[str, object], *, high: float, low: float) -> None:
    """Update the original replay's favorable close-confirmed R metric."""
    side = int(pos["side"])
    entry = float(pos["entry_price"])
    risk = float(pos["initial_risk"])
    favorable = high if side == 1 else low
    pos["mfe_r"] = max(float(pos["mfe_r"]), side * (favorable - entry) / risk)


def _update_mae_reference(pos: dict[str, object], *, high: float, low: float) -> None:
    """Track a conservative whole-bar adverse upper bound in reference-R.

    OHLC does not reveal the path after an intrabar exit, so a bar's full
    adverse extreme can include movement after the fill.  The field is an
    excursion diagnostic in the frozen reference denominator, never a claim
    about the realized loss or a maximum-loss guarantee.
    """
    side = int(pos["side"])
    entry = float(pos["entry_price"])
    risk = float(pos["initial_risk"])
    adverse = (entry - low) / risk if side == 1 else (high - entry) / risk
    if math.isfinite(adverse):
        pos["mae_reference_r"] = max(float(pos["mae_reference_r"]), adverse)


def _stop_hit(side: int, value: float, level: float) -> bool:
    """Return whether an OHLC value reaches an active protective stop."""
    return value <= level if side == 1 else value >= level


def _target_hit(side: int, high: float, low: float, target: float) -> bool:
    """Return whether a bar touches a fixed favorable TP target."""
    return high >= target if side == 1 else low <= target


def _exit_reason(pos: dict[str, object], *, gap: bool) -> str:
    """Classify a hard stop while preserving old initial/trailing labels."""
    reason = "trailing_stop" if bool(pos["hard_stop_is_trail"]) else "initial_stop"
    return reason + "_gap" if gap else reason


def _close_position(
    p: old_engine.PreparedArm,
    pos: dict[str, object],
    *,
    i: int,
    price: float,
    reason: str,
    fills: list[dict[str, object]],
    precision: str,
    phase: str,
) -> dict[str, object]:
    """Apply one full exit and return the shared trade row."""
    stamp = p.frame.index[i]
    side = int(pos["side"])
    fraction = float(pos["qty_remaining"])
    cost = fraction * base.EXIT_COST
    gross = fraction * side * (price / float(pos["entry_price"]) - 1.0)
    pos["qty_realized"] += fraction
    pos["qty_remaining"] = 0.0
    pos["realized_gross_return"] += gross
    pos["realized_net_return"] += gross - cost
    pos["last_exit_i"], pos["last_exit_time"] = i, stamp
    pos["last_exit_price"], pos["last_exit_reason"] = price, reason
    base._append_fill(
        fills,
        pos,
        leg_no=int(pos.get("fill_count", 0)) + 1,
        bar_open=stamp,
        event_time=stamp,
        phase=phase,
        precision="bar_open" if phase == "open" else "within_bar",
        kind="exit",
        fraction=fraction,
        price=price,
        cost=cost,
        reason=reason,
        context=p.context,
    )
    return _result_row(pos, censored=False, precision=precision)


def _censor_position(
    p: old_engine.PreparedArm,
    pos: dict[str, object],
    *,
    i: int,
    reason: str,
    fills: list[dict[str, object]],
    precision: str,
) -> dict[str, object]:
    """Censor a gap/unknown-MA position without inventing a future exit."""
    stamp = p.frame.index[i]
    event_time = stamp if reason == "data_gap_censored" else _close_time(p, i)
    pos["last_exit_i"], pos["last_exit_time"] = i, stamp
    pos["last_exit_reason"] = reason
    base._append_fill(
        fills,
        pos,
        leg_no=int(pos.get("fill_count", 0)) + 1,
        bar_open=stamp,
        event_time=event_time,
        phase="close",
        precision=precision,
        kind="censor",
        fraction=float(pos["qty_remaining"]),
        price=math.nan,
        cost=0.0,
        reason=reason,
        context=p.context,
    )
    return _result_row(pos, censored=True, precision=precision)


def _ambiguous_event(p: old_engine.PreparedArm, pos: dict[str, object], *, i: int,
                     events: list[dict[str, object]]) -> None:
    """Record conservative stop-over-TP priority when both touch one bar."""
    pos["ambiguous_bar"] = True
    stamp = p.frame.index[i]
    events.append({
        "trade_id": pos["trade_id"], "cohort": pos["cohort"], "policy": pos["policy"],
        "stream_key": p.context.key, "bar_open": stamp, "event_time": _close_time(p, i),
        "execution_phase": "intrabar", "event_kind": "ambiguous_bar",
        "reason": "hard_stop_and_tp2_same_bar", "protection_before": float(pos["protection"]),
        "protection_after": float(pos["protection"]), "qty_fraction": float(pos["qty_remaining"]),
    })


def _schedule_ma(
    p: old_engine.PreparedArm,
    pos: dict[str, object],
    *,
    i: int,
    higher_value: float,
    reason: str,
    events: list[dict[str, object]],
) -> None:
    """Freeze a close-confirmed structural exit for the next open."""
    confirm_time = _close_time(p, i)
    pos["ma_confirm_time"] = confirm_time
    pos["ma_confirm_level"] = float(higher_value)
    pos["ma_pending_reason"] = reason
    pos["ma_pending_time"] = confirm_time
    pos["ma_pending_level"] = float(higher_value)
    events.append({
        "trade_id": pos["trade_id"], "cohort": pos["cohort"], "policy": pos["policy"],
        "stream_key": p.context.key, "bar_open": p.frame.index[i], "event_time": confirm_time,
        "execution_phase": "close", "event_kind": "exit_scheduled", "reason": reason,
        "protection_before": float(pos["protection"]), "protection_after": float(pos["protection"]),
        "qty_fraction": float(pos["qty_remaining"]), "ma_confirm_level": float(higher_value),
    })


def _trail_close_update(p: old_engine.PreparedArm, pos: dict[str, object], *, i: int,
                        policy: _Policy) -> None:
    """Apply the unchanged close-confirmed 2R/4ATR trail to the next bar."""
    side = int(pos["side"])
    entry = float(pos["entry_price"])
    risk = float(pos["initial_risk"])
    close = float(p.close[i])
    current_r = side * (close - entry) / risk
    pos["trail_armed"] = bool(pos["trail_armed"]) or current_r >= float(p.spec.arm_r)
    atr = float(p.atr[i])
    if not bool(pos["trail_armed"]) or not math.isfinite(atr) or atr <= 0:
        return
    raw = close - side * float(p.spec.trail_atr) * atr
    candidate = (math.floor(raw / float(p.spec.tick)) * float(p.spec.tick)
                 if side == 1 else math.ceil(raw / float(p.spec.tick)) * float(p.spec.tick))
    initial = float(pos["initial_stop"])
    if policy.structural:
        # A structural reference stop is not active protection.  Only a real
        # trail candidate beyond it may become a hard stop.
        tighter = candidate > initial if side == 1 else candidate < initial
        if not bool(pos["hard_stop_active"]) and tighter:
            pos["hard_stop_active"] = True
            pos["hard_stop_is_trail"] = True
            pos["protection"] = candidate
        elif bool(pos["hard_stop_active"]):
            before = float(pos["protection"])
            pos["protection"] = max(before, candidate) if side == 1 else min(before, candidate)
    else:
        before = float(pos["protection"])
        pos["protection"] = max(before, candidate) if side == 1 else min(before, candidate)
        if (side == 1 and float(pos["protection"]) > initial) or (side == -1 and float(pos["protection"]) < initial):
            pos["hard_stop_is_trail"] = True


def _step_open(
    p: old_engine.PreparedArm,
    pos: dict[str, object],
    *,
    i: int,
    pending_reverse: tuple[int, int] | None,
    policy: _Policy,
    higher: np.ndarray,
    fills: list[dict[str, object]],
    events: list[dict[str, object]],
) -> tuple[dict[str, object] | None, tuple[int, int] | None, dict[str, object] | None]:
    """Run only the bar-open lifecycle shared by serial and fixed replay.

    The old engine processes an active position's opening gap, pending
    reverse, and then the pending entry before it tests the new position's
    current-bar range.  Keeping this phase separate prevents an old position
    from being passed through a complete bar twice when a next-open entry is
    pending.
    """
    opening = float(p.open[i])
    side = int(pos["side"])
    hard_active = bool(pos["hard_stop_active"])
    protection = float(pos["protection"])
    target = float(pos["tp_price"])
    tp_enabled = policy.profit_mode == "tp2"

    # A hard stop already active before this open has priority over every
    # pending intent, exactly as the old engine's initial/trailing gap rule.
    if hard_active and _stop_hit(side, opening, protection):
        _update_mae_reference(pos, high=opening, low=opening)
        result = _close_position(
            p, pos, i=i,
            price=min(opening, protection) if side == 1 else max(opening, protection),
            reason=_exit_reason(pos, gap=True), fills=fills,
            precision="bar_open_or_intrabar_window", phase="open",
        )
        # The open has already selected the stop path.  A later high/low TP
        # touch is not an ambiguous same-time event.
        return None, None, result

    # A close-confirmed structure exit is a next-open market exit.  It wins
    # over an opposite signal and a TP gap when both were pending at this open.
    ma_reason = pos.get("ma_pending_reason")
    if ma_reason is not None:
        pos["ma_pending_reason"] = None
        pos["ma_pending_time"] = None
        pos["ma_pending_level"] = np.nan
        _update_mae_reference(pos, high=opening, low=opening)
        result = _close_position(
            p, pos, i=i, price=opening, reason=str(ma_reason), fills=fills,
            precision="bar_open_or_intrabar_window", phase="open",
        )
        return None, None, result

    if pending_reverse is not None:
        _, old_side = pending_reverse
        if side == old_side:
            _update_mae_reference(pos, high=opening, low=opening)
            result = _close_position(
                p, pos, i=i, price=opening, reason="opposite_v6_next_open", fills=fills,
                precision="bar_open_or_intrabar_window", phase="open",
            )
            return None, None, result
        pending_reverse = None

    # A fixed limit fills at its target price if the next open gaps through it.
    if tp_enabled and ((side == 1 and opening >= target) or (side == -1 and opening <= target)):
        _update_mae_reference(pos, high=opening, low=opening)
        result = _close_position(
            p, pos, i=i, price=target, reason="tp2_gap", fills=fills,
            precision="bar_open_or_intrabar_window", phase="open",
        )
        return None, None, result

    return pos, pending_reverse, None


def _step_intrabar_close(
    p: old_engine.PreparedArm,
    pos: dict[str, object],
    *,
    i: int,
    policy: _Policy,
    higher: np.ndarray,
    fills: list[dict[str, object]],
    events: list[dict[str, object]],
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """Run one surviving position through its range and completed close.

    This is called exactly once after the opening phase and any pending entry.
    A trail or MA confirmation created here is effective only on a later bar.
    """
    opening, high, low, close = (float(array[i]) for array in (p.open, p.high, p.low, p.close))
    side = int(pos["side"])
    hard_active = bool(pos["hard_stop_active"])
    protection = float(pos["protection"])
    target = float(pos["tp_price"])
    tp_enabled = policy.profit_mode == "tp2"

    # Existing hard stop/TP intrabar priority.  Structural initial stops are
    # deliberately absent from this check until a real trail activates.
    _update_mae_reference(pos, high=high, low=low)
    hard_touch = hard_active and _stop_hit(side, low if side == 1 else high, protection)
    tp_touch = tp_enabled and _target_hit(side, high, low, target)
    if hard_touch and tp_touch:
        _ambiguous_event(p, pos, i=i, events=events)
    if hard_touch:
        result = _close_position(
            p, pos, i=i,
            price=min(opening, protection) if side == 1 else max(opening, protection),
            reason=_exit_reason(pos, gap=False),
            fills=fills,
            precision="bar_open_or_intrabar_window",
            phase="intrabar",
        )
        return None, result
    if tp_touch:
        result = _close_position(
            p, pos, i=i, price=target, reason="tp2", fills=fills,
            precision="bar_open_or_intrabar_window", phase="intrabar",
        )
        return None, result

    # A surviving bar is the only bar that can arm/update the original trail.
    _update_mfe(pos, high=high, low=low)
    if policy.profit_mode == "trail":
        _trail_close_update(p, pos, i=i, policy=policy)

    if policy.structural:
        higher_value = higher[i]
        if not _valid_higher(higher_value):
            result = _censor_position(
                p, pos, i=i, reason="ma_unknown_censored", fills=fills,
                precision="unknown_ma",
            )
            return None, result
        if side == 1:
            broken = close < float(higher_value)
            if policy.stop_mode == "htf_body":
                broken = broken and opening < float(higher_value)
        else:
            broken = close > float(higher_value)
            if policy.stop_mode == "htf_body":
                broken = broken and opening > float(higher_value)
        if broken:
            _schedule_ma(p, pos, i=i, higher_value=float(higher_value),
                         reason=("ma_body_next_open" if policy.stop_mode == "htf_body" else "ma_close_next_open"),
                         events=events)
    return pos, None


def _serial_row(
    p: old_engine.PreparedArm,
    *,
    signal_i: int,
    side: int,
    policy: _Policy,
    higher: np.ndarray,
    initial_transform: Callable[[dict[str, object], int, int], dict[str, object] | None] | None,
) -> dict[str, object] | None:
    """Build one serial entry row with local/full-frame ordinal parity."""
    row = old_engine.base._initial_position_fast(
        p.frame.index, p.open, p.high, p.low, p.close, p.atr, p.gap, signal_i, side, p.spec
    )
    if row is None:
        return None
    row = _initial_transform(p, row, signal_i=signal_i, side=side, policy=policy,
                             higher=higher, callback=initial_transform)
    if row is None:
        return None
    row["initial_risk_frac"] = float(row["initial_risk"]) / float(row["entry_price"])
    original_i = p.ordinal.get(p.frame.index[signal_i])
    if original_i is not None:
        row["signal_i"], row["entry_i"] = original_i, original_i + 1
        row["frozen_index_offset"] = original_i - signal_i
    return row


def _boundary(p: old_engine.PreparedArm, pos: dict[str, object], *, fills: list[dict[str, object]]) -> dict[str, object]:
    """Mark an observed boundary only when no unresolved MA remains."""
    last = len(p.frame) - 1
    stamp = p.frame.index[last]
    base._append_fill(
        fills, pos, leg_no=int(pos.get("fill_count", 0)) + 1, bar_open=stamp,
        event_time=stamp + pd.Timedelta(minutes=p.context.minutes), phase="close",
        precision="last_complete_close", kind="censor", fraction=float(pos["qty_remaining"]),
        price=float(p.close[last]), cost=0.0, reason="boundary_mark", context=p.context,
    )
    pos["last_exit_i"], pos["last_exit_time"] = last, stamp
    pos["last_exit_price"], pos["last_exit_reason"] = float(p.close[last]), "boundary_mark"
    return _result_row(pos, censored=True, precision="last_complete_close")


def replay_serial(
    p: old_engine.PreparedArm,
    *,
    policy: str,
    higher: np.ndarray,
    initial_transform: Callable[[dict[str, object], int, int], dict[str, object] | None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay one stop/profit policy with the old single-position lifecycle.

    ``higher`` is aligned to the prepared 15m bars and may contain NaN for an
    unavailable confirmed H1 line.  Structural modes censor an open position
    at the first surviving bar with an unknown line; no later line is carried
    backward.  ``initial_transform`` is called immediately after the frozen
    next-open initializer and receives only ``(row, signal_i, side)``.
    """
    cfg = _parse_policy(policy)
    higher_a = _higher_array(p, higher)
    frame, gap, allowed, raw_side = p.frame, p.gap, p.allowed, p.raw_side
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
                trades.append(_censor_position(p, position, i=i, reason="data_gap_censored", fills=fills,
                                                precision="unknown_gap"))
            position = None
            pending_entry = pending_reverse = None
            continue

        # Process only the existing position's opening lifecycle before a
        # pending entry.  Its range/close phase runs once below, after the
        # entry decision, matching the frozen engine ordering.
        if position is not None:
            position, pending_reverse, result = _step_open(
                p, position, i=i, pending_reverse=pending_reverse, policy=cfg,
                higher=higher_a, fills=fills, events=events,
            )
            if result is not None:
                ended_side = int(result["side"])
                trades.append(result)

        if pending_entry is not None:
            signal_i, side = pending_entry
            if position is None and side != ended_side:
                made = _serial_row(p, signal_i=signal_i, side=side, policy=cfg, higher=higher_a,
                                   initial_transform=initial_transform)
                if made is not None:
                    next_id += 1
                    position = _new_position(
                        p, made, policy=cfg,
                        trade_id=f"{p.context.key}:{p.arm}:{cfg.output_policy}:{next_id}",
                        entry_local_i=i,
                    )
                    base._append_fill(
                        fills, position, leg_no=1, bar_open=stamp, event_time=stamp,
                        phase="open", precision="bar_open", kind="entry", fraction=1.0,
                        price=float(position["entry_price"]), cost=base.ENTRY_COST,
                        reason="next_open_entry", context=p.context,
                    )
            pending_entry = None

        if position is not None:
            position, result = _step_intrabar_close(
                p, position, i=i, policy=cfg, higher=higher_a,
                fills=fills, events=events,
            )
            if result is not None:
                ended_side = int(result["side"])
                trades.append(result)
                pending_reverse = None

        signal_side = int(raw_side[i])
        if signal_side:
            if position is not None and signal_side != int(position["side"]):
                pending_reverse = (i, int(position["side"]))
                if bool(allowed[i]):
                    pending_entry = (i, signal_side)
            elif position is None and signal_side != ended_side and bool(allowed[i]):
                pending_entry = (i, signal_side)

    if position is not None:
        trades.append(_boundary(p, position, fills=fills))
    columns = [*base.TRADE_COLUMNS, *EXTRA_COLUMNS]
    return pd.DataFrame(trades, columns=columns), pd.DataFrame(fills, columns=base.FILL_COLUMNS), pd.DataFrame(events)


def replay_fixed(
    p: old_engine.PreparedArm,
    row: pd.Series,
    *,
    policy: str,
    higher: np.ndarray,
) -> dict[str, object]:
    """Replay one already transformed frozen entry through serial's step.

    ``row`` is the output of the caller's signal-close initializer and, for
    HTF arms, its prior initial-stop transform.  Fixed replay therefore never
    applies that transform a second time.  This keeps fixed and serial paths
    paired on the same frozen entry risk denominator.
    """
    cfg = _parse_policy(policy)
    higher_a = _higher_array(p, higher)
    side = int(row["side"])
    start = int(p.frame.index.get_loc(pd.Timestamp(row["entry_time"])))
    source = row.to_dict()
    source["initial_risk_frac"] = float(source["initial_risk"]) / float(source["entry_price"])
    pos = _new_position(p, source, policy=cfg, trade_id=f"{p.context.key}:{p.arm}:fixed",
                        entry_local_i=start)
    fills: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    pending_reverse: tuple[int, int] | None = None
    for i in range(start, len(p.frame)):
        if bool(p.gap[i]):
            return _censor_position(
                p, pos, i=i, reason="data_gap_censored", fills=fills,
                precision="unknown_gap",
            )
        pos, pending_reverse, result = _step_open(
            p, pos, i=i, pending_reverse=pending_reverse, policy=cfg,
            higher=higher_a, fills=fills, events=events,
        )
        if result is not None:
            return result
        pos, result = _step_intrabar_close(
            p, pos, i=i, policy=cfg, higher=higher_a,
            fills=fills, events=events,
        )
        if result is not None:
            return result
        if int(p.raw_side[i]) == -side:
            pending_reverse = (i, side)
    return _boundary(p, pos, fills=fills)

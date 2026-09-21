"""Causal high-anchor trailing sensitivity for the frozen SPIKE V9 replay.

This offline research engine consumes the authenticated V8 ``PreparedArm``
after :func:`yoyo.evaluation.spike_v9_full_replay.prepare_v9` has supplied
the V9 admission mask.  It reads only ``open``, ``high``, ``low``, ``close``,
``atr`` and the existing gap/signal arrays.  A long's running high starts at
its actual next-open entry and is updated from each completed bar; therefore
no decision uses a later bar.  The frozen 2-initial-R arm remains a *close*
test and any protection calculated at a close is available only at the next
observed bar.  Short positions deliberately retain the frozen current-close
4-ATR anchor.  Initial stops, tick rounding, raw opposite exits, gaps and the
frozen 20bp cost contract are unchanged.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v9_full_replay import CONTROL_SEED, VOL_BINS


POLICY = "high_r_v1"


def make_initial(prepared: engine.PreparedArm, signal_i: int, side: int) -> dict[str, object] | None:
    """Make one frozen next-open position without changing the prepared cache."""
    initial = base._initial_position_fast(
        prepared.frame.index, prepared.open, prepared.high, prepared.low,
        prepared.close, prepared.atr, prepared.gap, signal_i, side, prepared.spec,
    )
    if initial is not None:
        initial["initial_risk_frac"] = float(initial["initial_risk"]) / float(initial["entry_price"])
    return initial


def _original_ordinals(prepared: engine.PreparedArm, made: dict[str, object], signal_i: int) -> None:
    """Keep the parent replay's full-source ordinal convention for short caches."""
    original_i = prepared.ordinal.get(prepared.frame.index[signal_i])
    if original_i is not None:
        made["signal_i"], made["entry_i"], made["frozen_index_offset"] = original_i, original_i + 1, original_i - signal_i


def _favourable_r(side: int, entry: float, risk: float, high: float, low: float) -> float:
    """Return current-bar favourable movement in the frozen actual-entry R unit."""
    return side * ((high if side == 1 else low) - entry) / risk


def _close_update(position: dict[str, object], *, high: float, low: float, close: float, atr: float,
                  prepared: engine.PreparedArm, events: list[dict[str, object]], i: int) -> None:
    """Update MFE and next-bar protection after the current stop check.

    Long ``highest_known`` includes every observed bar after actual entry,
    even before the frozen close-based 2R arm.  It is intentionally not used
    to arm the trail.  For shorts the candidate remains the frozen close minus
    side times four ATR; ``highest_known`` is absent because no peak policy is
    applied to shorts.
    """
    side = int(position["side"])
    entry, risk = float(position["entry_price"]), float(position["initial_risk"])
    position["mfe_r"] = max(float(position["mfe_r"]), _favourable_r(side, entry, risk, high, low))
    if side == 1:
        position["highest_known"] = max(float(position["highest_known"]), high)
    close_r = side * (close - entry) / risk
    position["trail_armed"] = bool(position["trail_armed"]) or close_r >= prepared.spec.arm_r
    if not bool(position["trail_armed"]) or not math.isfinite(atr) or atr <= 0:
        return
    anchor = float(position["highest_known"]) if side == 1 else close
    raw = anchor - side * prepared.spec.trail_atr * atr
    candidate = math.floor(raw / prepared.spec.tick) * prepared.spec.tick if side == 1 else math.ceil(raw / prepared.spec.tick) * prepared.spec.tick
    before = float(position["protection"])
    after = max(before, candidate) if side == 1 else min(before, candidate)
    position["protection"] = after
    if after == before:
        return
    stamp = prepared.frame.index[i]
    events.append({
        "trade_id": position["trade_id"], "cohort": position["cohort"], "policy": POLICY,
        "stream_key": prepared.context.key, "bar_open": stamp,
        "event_time": stamp + pd.Timedelta(minutes=prepared.context.minutes),
        "available_at": stamp + pd.Timedelta(minutes=prepared.context.minutes),
        "effective_next_bar": True,
        "execution_phase": "close", "event_kind": "protection_update",
        "reason": "high_anchor_trail4atr_next_bar" if side == 1 else "close_anchor_trail4atr_next_bar",
        "protection_before": before, "protection_after": after,
        "qty_fraction": float(position["qty_remaining"]),
        "highest_known": float(position["highest_known"]) if side == 1 else math.nan,
        "anchor": anchor,
    })


def _new_position(prepared: engine.PreparedArm, made: dict[str, object], *, trade_id: str) -> dict[str, object]:
    """Attach parent accounting fields plus a causal high cache for one position."""
    position = base._new_trade(made, trade_id=trade_id, cohort=prepared.cohort, policy=POLICY, context=prepared.context)
    position.update(protection=float(position["initial_stop"]), mfe_r=0., trail_armed=False,
                    highest_known=float(position["entry_price"]))
    return position


def _close(position: dict[str, object], *, i: int, price: float, reason: str, fills: list[dict[str, object]],
           prepared: engine.PreparedArm, phase: str, precision: str) -> dict[str, object]:
    """Apply unchanged single-leg accounting and return the parent trade row."""
    fraction, side = float(position["qty_remaining"]), int(position["side"])
    gross = fraction * side * (price / float(position["entry_price"]) - 1)
    position["qty_realized"] += fraction
    position["qty_remaining"] = 0.0
    position["realized_gross_return"] += gross
    position["realized_net_return"] += gross - fraction * base.EXIT_COST
    stamp = prepared.frame.index[i]
    position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, price, reason
    base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp,
                      event_time=stamp, phase=phase, precision=precision, kind="exit", fraction=fraction,
                      price=price, cost=fraction * base.EXIT_COST, reason=reason, context=prepared.context)
    return base._trade_row(position, censored=False, precision="bar_open_or_intrabar_window")


def replay_serial(prepared: engine.PreparedArm, *, peak_long: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay V9 admissions serially, using running-long-high anchors when requested.

    ``peak_long=False`` delegates directly to the frozen parent engine so its
    baseline output remains exactly the parent V9 economic contract.
    """
    if not peak_long:
        return engine.replay_serial(prepared.context, arm="v8", enable_be=False, prepared=prepared)
    context, frame, spec = prepared.context, prepared.frame, prepared.spec
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
                                  price=math.nan, cost=0., reason="data_gap_censored", context=context)
                position["last_exit_i"], position["last_exit_time"], position["last_exit_reason"] = i, stamp, "data_gap_censored"
                trades.append(base._trade_row(position, censored=True, precision="unknown_gap"))
            position = None; pending_entry = pending_reverse = None
            continue
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            if oa[i] <= protection if side == 1 else oa[i] >= protection:
                reason = "trailing_stop_gap" if protection != float(position["initial_stop"]) else "initial_stop_gap"
                trades.append(_close(position, i=i, price=oa[i], reason=reason, fills=fills, prepared=prepared,
                                     phase="open", precision="bar_open"))
                ended_side, position, pending_reverse = side, None, None
        if position is not None and pending_reverse is not None:
            _, old_side = pending_reverse
            if int(position["side"]) == old_side:
                trades.append(_close(position, i=i, price=oa[i], reason="opposite_v6_next_open", fills=fills, prepared=prepared,
                                     phase="open", precision="bar_open"))
                ended_side, position = old_side, None
            pending_reverse = None
        if pending_entry is not None:
            signal_i, side = pending_entry
            if position is None and side != ended_side:
                made = make_initial(prepared, signal_i, side)
                if made is not None:
                    _original_ordinals(prepared, made, signal_i)
                    next_id += 1
                    position = _new_position(prepared, made, trade_id=f"{context.key}:v8:{POLICY}:{next_id}")
                    base._append_fill(fills, position, leg_no=1, bar_open=stamp, event_time=stamp, phase="open", precision="bar_open",
                                      kind="entry", fraction=1., price=float(position["entry_price"]), cost=base.ENTRY_COST,
                                      reason="next_open_entry", context=context)
            pending_entry = None
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            stopped = la[i] <= protection if side == 1 else ha[i] >= protection
            if stopped:
                price = min(oa[i], protection) if side == 1 else max(oa[i], protection)
                reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
                if oa[i] <= protection if side == 1 else oa[i] >= protection:
                    reason += "_gap"
                trades.append(_close(position, i=i, price=price, reason=reason, fills=fills, prepared=prepared,
                                     phase="open" if reason.endswith("_gap") else "intrabar",
                                     precision="bar_open" if reason.endswith("_gap") else "within_bar"))
                ended_side, position, pending_reverse = side, None, None
            else:
                _close_update(position, high=ha[i], low=la[i], close=ca[i], atr=aa[i], prepared=prepared, events=events, i=i)
        signal_side = int(raw_side[i])
        if signal_side:
            if position is not None and signal_side != int(position["side"]):
                pending_reverse = (i, int(position["side"]))
                if bool(allowed[i]):
                    pending_entry = (i, signal_side)
            elif position is None and signal_side != ended_side and bool(allowed[i]):
                pending_entry = (i, signal_side)
    if position is not None:
        last, stamp = len(frame) - 1, frame.index[-1]
        base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp,
                          event_time=stamp + pd.Timedelta(minutes=context.minutes), phase="close", precision="last_complete_close",
                          kind="censor", fraction=float(position["qty_remaining"]), price=ca[last], cost=0.,
                          reason="boundary_mark", context=context)
        position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = last, stamp, ca[last], "boundary_mark"
        trades.append(base._trade_row(position, censored=True, precision="last_complete_close"))
    return pd.DataFrame(trades, columns=base.TRADE_COLUMNS), pd.DataFrame(fills, columns=base.FILL_COLUMNS), pd.DataFrame(events)


def replay_fixed_entry(prepared: engine.PreparedArm, row: pd.Series, *, peak_long: bool = True) -> dict[str, object]:
    """Replay one immutable entry under the requested causal trailing policy."""
    if not peak_long:
        return engine.replay_fixed_entry(prepared.context, row, arm="v8", enable_be=False, prepared=prepared)
    frame, side = prepared.frame, int(row.side)
    start = int(frame.index.get_loc(pd.Timestamp(row.entry_time)))
    values = {key: row[key] for key in ("signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "entry_price", "initial_stop", "initial_risk", "initial_risk_frac")}
    values["frozen_index_offset"] = int(row.entry_i) - start
    pos = _new_position(prepared, values, trade_id=f"{prepared.context.key}:v8:{POLICY}:fixed")
    pending_reverse: int | None = None
    for i in range(start, len(frame)):
        stamp = frame.index[i]
        if prepared.gap[i]:
            pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_reason"] = i, stamp, "data_gap_censored"
            return base._trade_row(pos, censored=True, precision="unknown_gap")
        protection = float(pos["protection"])
        if pending_reverse is not None:
            if side == pending_reverse:
                price = prepared.open[i]
                reason = ("trailing_stop_gap" if protection != float(pos["initial_stop"]) else "initial_stop_gap") if (price <= protection if side == 1 else price >= protection) else "opposite_v6_next_open"
                return _fixed_close(pos, i=i, price=price, reason=reason, prepared=prepared)
            pending_reverse = None
        if prepared.low[i] <= protection if side == 1 else prepared.high[i] >= protection:
            price = min(prepared.open[i], protection) if side == 1 else max(prepared.open[i], protection)
            reason = "trailing_stop" if protection != float(pos["initial_stop"]) else "initial_stop"
            if prepared.open[i] <= protection if side == 1 else prepared.open[i] >= protection:
                reason += "_gap"
            return _fixed_close(pos, i=i, price=price, reason=reason, prepared=prepared)
        _close_update(pos, high=prepared.high[i], low=prepared.low[i], close=prepared.close[i], atr=prepared.atr[i], prepared=prepared, events=[], i=i)
        if int(prepared.raw_side[i]) == -side:
            pending_reverse = side
    pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = len(frame) - 1, frame.index[-1], prepared.close[-1], "boundary_mark"
    return base._trade_row(pos, censored=True, precision="last_complete_close")


def _fixed_close(pos: dict[str, object], *, i: int, price: float, reason: str, prepared: engine.PreparedArm) -> dict[str, object]:
    """Close a fixed path with the parent engine's frozen whole-position cost."""
    side = int(pos["side"])
    pos["qty_realized"], pos["qty_remaining"] = 1., 0.
    pos["realized_gross_return"] = side * (price / float(pos["entry_price"]) - 1)
    pos["realized_net_return"] = pos["realized_gross_return"] - base.ENTRY_COST - base.EXIT_COST
    pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, prepared.frame.index[i], price, reason
    return base._trade_row(pos, censored=False, precision="bar_open_or_intrabar_window")


def matched_controls(prepared: engine.PreparedArm, targets: pd.DataFrame, *, peak_long: bool) -> pd.DataFrame:
    """Draw the original deterministic V9 controls and score them under one policy.

    Selection is identical for both policies: one hash-selected same-stream,
    side, scheduled-month, split-fold and contemporaneous ATR/close bucket
    candidate.  Failed initial positions and censored paths are retained and
    are never resampled.
    """
    frame, context = prepared.frame, prepared.context
    delta = pd.Timedelta(minutes=context.minutes)
    fraction = prepared.atr / prepared.close
    bins, months = np.searchsorted(VOL_BINS, fraction, side="left"), frame.index.strftime("%Y-%m")
    ready = frame.get("ready", pd.Series(False, index=frame.index)).fillna(False).to_numpy(bool)
    finite = np.isfinite(frame[["open", "high", "low", "close", "atr"]].to_numpy(float)).all(axis=1)
    eligible = ready & finite & (prepared.close > 0) & (prepared.atr > 0)
    scheduled = frame.index + delta
    folds = np.where(scheduled < base.SPLIT, "earlier", "later")
    eligible &= (scheduled >= base.START) & (scheduled < base.END)
    pools: dict[tuple[str, int, str], np.ndarray] = {}
    for month, bucket, fold in sorted(set(zip(months[eligible], bins[eligible], folds[eligible]))):
        pools[(month, int(bucket), fold)] = np.flatnonzero(eligible & (months == month) & (bins == bucket) & (folds == fold))
    paths: dict[tuple[int, int], dict[str, object] | None] = {}
    rows: list[dict[str, object]] = []
    for target in targets.itertuples(index=False):
        stamp, side = pd.Timestamp(target.signal_bar_open), int(target.side)
        i = int(frame.index.get_loc(stamp))
        item = {"stream_key": context.key, **context.identity, "arm": getattr(target, "arm", "v9"),
                "signal_i": int(target.signal_i), "signal_bar_open": stamp, "entry_time": target.entry_time,
                "exit_time": target.exit_time, "side": side, "matched": False, "reason": "no_exact_match",
                "target_net_r": target.net_r, "target_net_return": target.net_return,
                "target_censored": bool(target.censored), "control_net_r": math.nan, "control_net_return": math.nan,
                "control_signal_time": pd.NaT, "control_entry_time": pd.NaT, "control_exit_time": pd.NaT,
                "control_censored": True, "month": months[i], "vol_bin": int(bins[i])}
        options = pools.get((months[i], int(bins[i]), folds[i]), np.array([], dtype=int))
        options = options[options != i]
        if len(options):
            choice = int(hashlib.sha256(f"{CONTROL_SEED}|{context.key}|{stamp.isoformat()}|{side}".encode()).hexdigest(), 16) % len(options)
            chosen = int(options[choice])
            item["control_signal_time"] = frame.index[chosen]
            key = chosen, side
            if key not in paths:
                initial = make_initial(prepared, chosen, side)
                paths[key] = (replay_fixed_entry(prepared, pd.Series(initial), peak_long=peak_long)
                              if initial is not None and base.START <= initial["entry_time"] < base.END else None)
            control = paths[key]
            if control is None:
                item["reason"] = "initial_position_unavailable"
            else:
                item.update(control_entry_time=control["entry_time"], control_exit_time=control["exit_time"],
                            control_censored=bool(control["censored"]), control_net_r=control["net_r"],
                            control_net_return=control["net_return"])
                item["matched"] = not bool(target.censored) and not bool(control["censored"])
                item["reason"] = "matched" if item["matched"] else "censored_pair"
        rows.append(item)
    columns = ["stream_key", "venue", "symbol", "asset", "timeframe_min", "arm", "signal_i", "signal_bar_open",
               "entry_time", "exit_time", "side", "matched", "reason", "target_net_r", "target_net_return",
               "target_censored", "control_net_r", "control_net_return", "control_signal_time", "control_entry_time",
               "control_exit_time", "control_censored", "month", "vol_bin"]
    return pd.DataFrame(rows, columns=columns)

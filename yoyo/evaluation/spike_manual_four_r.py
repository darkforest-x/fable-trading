"""Single-entry SPIKE replay with a resting gross 4R limit exit.

Entry construction, initial stop, raw opposite-signal reverse, close-confirmed
2R trail arming, 4 ATR trailing distance, and costs remain inherited from the
frozen V12.8/V12.6 prepared replay. The sole treatment is a limit at four
times the actual next-open initial risk, priced from the entry and never
ratcheted. OHLC bars cannot reveal the order of a same-bar stop and target, so
the adverse stop wins and the checkpoint records that ambiguity.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v1_v8_be05 as fixed
from yoyo.evaluation import spike_v128_recent as recent


TARGET_R = 4.0


def _row_value(row: Mapping[str, Any] | pd.Series, name: str) -> Any:
    """Read an initial-position field from either the builder dict or a Series."""
    return row[name]


def _is_stop_open(side: int, price: float, protection: float) -> bool:
    return price <= protection if side == 1 else price >= protection


def _is_target_open(side: int, price: float, target: float) -> bool:
    return price >= target if side == 1 else price <= target


def _finish(position: dict[str, Any], *, i: int, stamp: pd.Timestamp, price: float,
            reason: str, censored: bool, precision: str, prepared: fixed.PreparedArm,
            target: float, stop_target_ambiguous: bool = False) -> dict[str, Any]:
    """Set terminal accounting using the original 0.1% entry/exit cost legs."""
    position["last_exit_i"], position["last_exit_time"] = i, stamp
    position["last_exit_price"], position["last_exit_reason"] = price, reason
    if not censored:
        fraction = float(position["qty_remaining"])
        side, entry = int(position["side"]), float(position["entry_price"])
        gross = fraction * side * (float(price) / entry - 1.0)
        position["qty_realized"] = float(position["qty_realized"]) + fraction
        position["qty_remaining"] = 0.0
        position["realized_gross_return"] = float(position["realized_gross_return"]) + gross
        position["realized_net_return"] = float(position["realized_net_return"]) + gross - fraction * fixed.base.EXIT_COST
    result = fixed.base._trade_row(position, censored=censored, precision=precision)
    result.update({
        "be_armed": False,
        "be_trigger_count": 0,
        "protection": float(position["protection"]),
        "four_r_target_price": float(target),
        "four_r_stop_target_ambiguous": bool(stop_target_ambiguous),
    })
    # Match the V12.8 attempt's excursion fields so the two treatments share
    # the same fully observed versus terminal-stop-ambiguous accounting.
    local_entry = int(prepared.frame.index.get_loc(pd.Timestamp(position["entry_time"])))
    local_mfe_result = result | {"entry_i": local_entry, "exit_i": i}
    result.update(recent._mfe_fields(local_mfe_result, prepared))
    if reason in {"target_4r", "target_4r_open_gap"}:
        # The target ends exposure at its fixed limit. A target candle's later
        # high/low (or opening price improvement) is not part of realized MFE.
        side, entry, risk = int(position["side"]), float(position["entry_price"]), float(position["initial_risk"])
        known, closes = TARGET_R, []
        for j in range(local_entry, i):
            if bool(prepared.gap[j]):
                break
            favorable = float(prepared.high[j]) if side == 1 else float(prepared.low[j])
            known = max(known, side * (favorable - entry) / risk)
            closes.append(side * (float(prepared.close[j]) - entry) / risk)
        result.update({"mfe_known_r": known, "mfe_upper_r": known,
                       "close_peak_r": max([0.0, *closes]), "stop_bar_excursion_ambiguous": False})
    return result


def replay_four_r(prepared: fixed.PreparedArm, initial_row: Mapping[str, Any] | pd.Series) -> dict[str, Any]:
    """Replay one next-open entry with only a fixed gross 4R limit added.

    The target is derived from ``entry_price`` and the original ``initial_risk``.
    Stop opening gaps retain priority, a favorable opening gap fills at the
    target price, and an already-pending raw reverse exits at the open before
    any intrabar target check. Within a bar, a known protection and target
    touched together are resolved stop-first and marked ambiguous.
    """
    context, frame, gap, raw_side, spec = (
        prepared.context, prepared.frame, prepared.gap, prepared.raw_side, prepared.spec
    )
    side = int(_row_value(initial_row, "side"))
    if side not in (-1, 1):
        raise ValueError("initial side must be +1 or -1")
    start = int(frame.index.get_loc(pd.Timestamp(_row_value(initial_row, "entry_time"))))
    entry = float(_row_value(initial_row, "entry_price"))
    risk = float(_row_value(initial_row, "initial_risk"))
    if not (math.isfinite(entry) and entry > 0 and math.isfinite(risk) and risk > 0):
        raise ValueError("initial entry and risk must be positive and finite")
    if not (math.isclose(float(spec.arm_r), 2.0) and math.isclose(float(spec.trail_atr), 4.0)):
        raise ValueError("prepared replay must retain its frozen 2R/4ATR trail")
    target = entry + side * TARGET_R * risk
    position_values = {
        key: _row_value(initial_row, key)
        for key in ("signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "entry_price",
                    "initial_stop", "initial_risk", "initial_risk_frac")
    }
    position_values["frozen_index_offset"] = int(position_values["entry_i"]) - start
    position = fixed.base._new_trade(
        position_values,
        trade_id=f"{context.key}:{prepared.arm}:four_r_fixed",
        cohort=prepared.cohort,
        policy="four_r",
        context=context,
    )
    position.update(protection=float(_row_value(initial_row, "initial_stop")), mfe_r=0.0, trail_armed=False)
    pending_reverse = False
    ambiguous = False

    for i in range(start, len(frame)):
        stamp = frame.index[i]
        if bool(gap[i]):
            return _finish(position, i=i, stamp=stamp, price=math.nan, reason="data_gap_censored",
                           censored=True, precision="unknown_gap", prepared=prepared, target=target)

        protection = float(position["protection"])
        bar_open = float(prepared.open[i])

        # A protection active before this open is always checked first.
        if _is_stop_open(side, bar_open, protection):
            reason = "trailing_stop_gap" if protection != float(position["initial_stop"]) else "initial_stop_gap"
            return _finish(position, i=i, stamp=stamp, price=bar_open, reason=reason, censored=False,
                           precision="bar_open_or_intrabar_window", prepared=prepared, target=target)

        # A resting limit already marketable at the open gets its conservative
        # limit price. This check precedes the pending reverse; reverses take
        # precedence over only the subsequent intrabar target check.
        if _is_target_open(side, bar_open, target):
            return _finish(position, i=i, stamp=stamp, price=target, reason="target_4r_open_gap",
                           censored=False, precision="bar_open_or_intrabar_window", prepared=prepared,
                           target=target)

        if pending_reverse:
            return _finish(position, i=i, stamp=stamp, price=bar_open, reason="opposite_v6_next_open",
                           censored=False, precision="bar_open_or_intrabar_window", prepared=prepared,
                           target=target)

        stop_hit = (float(prepared.low[i]) <= protection) if side == 1 else (float(prepared.high[i]) >= protection)
        target_hit = (float(prepared.high[i]) >= target) if side == 1 else (float(prepared.low[i]) <= target)
        if stop_hit and target_hit:
            ambiguous = True
            price = min(bar_open, protection) if side == 1 else max(bar_open, protection)
            reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
            return _finish(position, i=i, stamp=stamp, price=price, reason=reason, censored=False,
                           precision="bar_open_or_intrabar_window", prepared=prepared, target=target,
                           stop_target_ambiguous=ambiguous)
        if stop_hit:
            price = min(bar_open, protection) if side == 1 else max(bar_open, protection)
            reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
            return _finish(position, i=i, stamp=stamp, price=price, reason=reason, censored=False,
                           precision="bar_open_or_intrabar_window", prepared=prepared, target=target)
        if target_hit:
            return _finish(position, i=i, stamp=stamp, price=target, reason="target_4r",
                           censored=False, precision="bar_open_or_intrabar_window", prepared=prepared,
                           target=target)

        fixed._close_updates(position, high=float(prepared.high[i]), low=float(prepared.low[i]),
                             close=float(prepared.close[i]), atr=float(prepared.atr[i]), spec=spec,
                             enable_be=False, events=[], context=context, arm=prepared.arm,
                             policy="four_r", i=i)
        pending_reverse = int(raw_side[i]) == -side

    last = len(frame) - 1
    return _finish(position, i=last, stamp=frame.index[last], price=float(prepared.close[last]),
                   reason="boundary_mark", censored=True, precision="last_complete_close",
                   prepared=prepared, target=target)


def four_r_checkpoints(prepared: fixed.PreparedArm, result: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize first 4R observations and post-touch giveback on the result path.

    A terminal stop-bar wick that also reaches 4R is labelled ambiguous because
    OHLC cannot establish whether the limit or stop came first. Close checkpoints
    exclude any bar on which an intrabar or opening exit already ended exposure.
    Giveback compares each later bar's adverse extreme with the best favorable
    extreme established by a prior bar; it does not invent an order inside a bar.
    """
    side, entry, risk = int(result["side"]), float(result["entry_price"]), float(result["initial_risk"])
    target = entry + side * TARGET_R * risk
    frame = prepared.frame
    start = int(frame.index.get_loc(pd.Timestamp(result["entry_time"])))
    entry_offset = int(result.get("entry_i", start)) - start
    exit_local = int(result["exit_i"]) - entry_offset
    exit_local = min(max(exit_local, start), len(frame) - 1)
    reason, censored = str(result["exit_reason"]), bool(result["censored"])
    open_exit = reason.endswith("_gap") or reason == "opposite_v6_next_open" or reason == "target_4r_open_gap"
    boundary = censored and reason == "boundary_mark"
    stop_exit = (not censored) and reason in {"initial_stop", "trailing_stop", "initial_stop_gap", "trailing_stop_gap"}
    same_bar_conflict = bool(result.get("four_r_stop_target_ambiguous", False))

    first_touch_local: int | None = None
    first_touch_phase: str | None = None
    first_touch_ambiguous = False
    first_close_local: int | None = None
    last_scan = exit_local if boundary else exit_local

    for i in range(start, last_scan + 1):
        if bool(prepared.gap[i]):
            break
        is_exit = i == exit_local
        opening = float(prepared.open[i])
        if is_exit and open_exit:
            # A target limit at a favorable opening gap is observable at the
            # open, but an adverse stop gap was already terminal.
            if first_touch_local is None and (reason == "target_4r_open_gap" or
                                              (reason == "opposite_v6_next_open" and _is_target_open(side, opening, target))):
                first_touch_local, first_touch_phase = i, "open"
            # An adverse stop opening gap wins before any later wick.
            if reason.endswith("_gap") and reason != "target_4r_open_gap":
                break
            if first_touch_local is None:
                # No exposure after another opening exit, so ignore this bar's wick.
                break
            break

        high, low = float(prepared.high[i]), float(prepared.low[i])
        touched = high >= target if side == 1 else low <= target
        if touched and first_touch_local is None:
            first_touch_local, first_touch_phase = i, "intrabar"
            first_touch_ambiguous = bool(is_exit and stop_exit)
        elif touched and is_exit and stop_exit and i == first_touch_local:
            first_touch_ambiguous = True

        close_is_observed = not is_exit or boundary
        if close_is_observed:
            close_r = side * (float(prepared.close[i]) - entry) / risk
            if close_r >= TARGET_R and first_close_local is None:
                first_close_local = i

    def _ordinal(local_i: int | None) -> int | None:
        return None if local_i is None else local_i + entry_offset

    def _clock(local_i: int | None) -> pd.Timestamp | None:
        if local_i is None:
            return None
        minutes = int(prepared.context.minutes)
        return pd.Timestamp(frame.index[local_i]) + pd.Timedelta(minutes=minutes)

    def _touch_clock(local_i: int | None) -> pd.Timestamp | None:
        if local_i is None:
            return None
        stamp = pd.Timestamp(frame.index[local_i])
        return stamp if first_touch_phase == "open" else stamp + pd.Timedelta(minutes=int(prepared.context.minutes))

    features: dict[str, Any] | None = None
    max_giveback = math.nan
    if first_touch_local is not None:
        j = first_touch_local
        close_time = _clock(j)
        px_close = float(prepared.close[j])
        atr = float(prepared.atr[j])
        features = {
            "bar_open": pd.Timestamp(frame.index[j]),
            "bar_close": close_time,
            "open": float(prepared.open[j]),
            "high": float(prepared.high[j]),
            "low": float(prepared.low[j]),
            "close": px_close,
            "atr": atr,
            "atr_pct_of_close": atr / px_close if px_close > 0 and math.isfinite(atr) else math.nan,
            "observed_at": close_time,
        }
        if not first_touch_ambiguous:
            peak_r = max(TARGET_R, side * ((float(prepared.high[j]) if side == 1 else float(prepared.low[j])) - entry) / risk)
            giveback = 0.0
            for i in range(j + 1, exit_local + 1):
                if bool(prepared.gap[i]):
                    break
                is_exit = i == exit_local
                if is_exit and open_exit:
                    adverse_r = side * (float(result["exit_price"]) - entry) / risk
                    giveback = max(giveback, peak_r - adverse_r)
                    break
                if is_exit and stop_exit:
                    adverse_r = side * (float(result["exit_price"]) - entry) / risk
                    giveback = max(giveback, peak_r - adverse_r)
                    break
                adverse_price = float(prepared.low[i]) if side == 1 else float(prepared.high[i])
                adverse_r = side * (adverse_price - entry) / risk
                giveback = max(giveback, peak_r - adverse_r)
                favorable_price = float(prepared.high[i]) if side == 1 else float(prepared.low[i])
                peak_r = max(peak_r, side * (favorable_price - entry) / risk)
            max_giveback = max(0.0, giveback)

    state = "not_reached" if first_touch_local is None else "ambiguous" if first_touch_ambiguous else "known"
    return {
        "first_touch_i": _ordinal(first_touch_local),
        "first_touch_clock": _touch_clock(first_touch_local),
        "first_touch_phase": first_touch_phase,
        "first_close4_i": _ordinal(first_close_local),
        "first_close4_clock": _clock(first_close_local),
        "first4_observation": state,
        "first4_ambiguous": bool(first_touch_ambiguous or same_bar_conflict),
        "max_giveback_r": max_giveback,
        "first4_bar_close_features": features,
    }

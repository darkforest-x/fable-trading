"""Causal reference and next-open execution replay for default SPIKE V1+.

This module translates the *default* ``spike_burst_v1_plus.pine`` state
machine for research.  It deliberately keeps two clocks: Pine's signal-close
reference protection and a separate next-open simulated fill.  The latter
uses the former's already-known stop levels; it does not pretend that a
signal-close reference is an executable fill.  Only default direct-confirm
mode is supported: optional pullback, structural failure, stagnation, risk
cap and cooldown are disabled in the saved default and are rejected here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import (
    DENSE_CROSSES, DENSE_WIDTH, MIN_QUIET, NEAR_ATR, OPPORTUNITY,
    burst, features, launch_window, path_reference, pine_ge, pine_gt, pine_le,
    pine_lt, price_burst, risk_reference,
)

FEE = 0.002

TRADE_COLUMNS = (
    "trade_id", "signal_i", "signal_time", "signal_bar_open", "side", "entry_i", "entry_time",
    "entry_price", "reference_signal_close", "reference_initial_stop", "reference_risk",
    "actual_risk", "initial_risk", "actual_risk_frac", "tick", "mfe_r", "protection",
    "exit_i", "exit_time", "exit_price", "exit_reason", "gross_return", "net_return",
    "gross_r", "net_r", "censored",
)
FILL_COLUMNS = (
    "trade_id", "leg_no", "kind", "bar_open", "price", "reason", "execution_phase", "qty_fraction",
)


def _path_plus(side: int, entry: float, risk: float, protection: float, peak: float,
               armed: bool, stage1: bool, stage2: bool, o: float, h: float,
               l: float, c: float, atr: float, tick: float, enabled: bool):
    """Port the default direct-mode ``f_pathPlus`` exactly at close granularity."""
    stopped = pine_le(l, protection) if side == 1 else pine_ge(h, protection)
    price = (min(o, protection) if side == 1 else max(o, protection)) if stopped else math.nan
    current = side * ((price if stopped else c) - entry) / risk
    new_peak = peak if stopped else max(peak, side * ((h if side == 1 else l) - entry) / risk)
    new_armed = armed or (not stopped and pine_ge(current, 2.0))
    new_stage1, new_stage2, new_protection = stage1, stage2, protection
    if not stopped:
        new_stage1 = stage1 or (enabled and pine_ge(current, 1.0))
        new_stage2 = stage2 or (enabled and pine_ge(current, 2.0))
        candidates: list[float] = []
        if new_stage1:
            raw = entry - side * 0.5 * risk
            if raw < c if side == 1 else raw > c:
                candidates.append(raw)
        if new_stage2:
            raw = entry * (1.002 if side == 1 else 0.998)
            if raw > 0 and (raw < c if side == 1 else raw > c):
                candidates.append(raw)
        if new_armed and pine_gt(atr, 0):
            raw = c - side * 4.0 * atr
            if raw > 0 and (raw < c if side == 1 else raw > c):
                candidates.append(raw)
        if candidates:
            raw = max(candidates) if side == 1 else min(candidates)
            rounded = math.floor(raw / tick) * tick if side == 1 else math.ceil(raw / tick) * tick
            new_protection = max(protection, rounded) if side == 1 else min(protection, rounded)
    return (not stopped, new_protection, new_peak, current, price, new_armed, new_stage1, new_stage2)


def replay_references(ohlcv: pd.DataFrame, tick: float, *, enable_plus: bool) -> pd.DataFrame:
    """Replay closed-bar default V1+ references and return auditable per-bar state.

    ``enable_plus=False`` is the same bidirectional display-derived baseline:
    raw signals still consume episodes and use 2R/4ATR trailing, while the
    default joint-overheat rejection and staged protection are disabled.
    """
    if not math.isfinite(float(tick)) or tick <= 0:
        raise ValueError("positive finite tick required")
    # Frozen stream caches retain an authenticated causal prefix and the
    # Pine-derived fields seeded before that prefix.  Recomputing SMMA/EMA from
    # the cache start would reset indicator state at the study boundary.  Small
    # unit-test OHLC frames may omit these fields and are then derived locally.
    needed = {"md", "sb", "middle", "atr", "pastWidth", "pastCrosses", "ropeHigh", "ropeLow",
              "recentLow", "recentHigh", "rv", "expansion", "ready"}
    f = ohlcv.copy() if needed.issubset(ohlcv.columns) else features(ohlcv)
    quiet = pending = trend = 0
    frozen_band = quiet_high = quiet_low = math.nan
    launch_high = launch_low = launch_band = math.nan
    release_i: int | None = None
    entry_i: int | None = None
    entry = stop = risk = protection = current = math.nan
    peak, armed, stage1, stage2 = 0.0, False, False, False
    previous_md = previous_middle = previous_atr = math.nan
    rows: list[dict] = []
    for i, row in enumerate(f.itertuples(index=False)):
        o, h, l, c = (float(row.open), float(row.high), float(row.low), float(row.close))
        active_protection = protection if trend else math.nan
        event, raw_signal, event_side, event_reason, reverse = False, False, 0, "", False
        ref_exit, exit_reason, exit_price = False, "", math.nan
        ended = False
        # Pine permits an opposite-side setup after a protective stop on this
        # same bar.  Keep the side that ended rather than treating every event
        # as a blanket bar-level lockout.
        exit_side = 0
        if bool(row.ready):
            if trend and entry_i is not None and i > entry_i:
                alive, protection, peak, current, exit_price, armed, stage1, stage2 = _path_plus(
                    trend, entry, risk, protection, peak, armed, stage1, stage2,
                    o, h, l, c, float(row.atr), float(tick), enable_plus)
                if not alive:
                    ref_exit, exit_reason, ended = True, "protective_stop", True
                    exit_side = trend
                    trend, pending = 0, 0
            band = frozen_band if quiet >= MIN_QUIET and math.isfinite(frozen_band) else NEAR_ATR * previous_atr
            can_lead = quiet >= MIN_QUIET and pending == 0 and pine_le(row.pastWidth, DENSE_WIDTH) and pine_ge(row.pastCrosses, DENSE_CROSSES)
            lead_up = can_lead and (not ended or exit_side != 1) and price_burst(1, o,h,l,c,row.md,row.sb,row.middle,previous_middle,quiet_high,quiet_low,row.ropeHigh,row.ropeLow,row.rv,row.expansion)
            lead_down = can_lead and (not ended or exit_side != -1) and price_burst(-1, o,h,l,c,row.md,row.sb,row.middle,previous_middle,quiet_high,quiet_low,row.ropeHigh,row.ropeLow,row.rv,row.expansion)
            leading = 1 if lead_up else -1 if lead_down else 0
            near = pending == 0 and pine_le(max(abs(float(row.md)), abs(float(row.sb))), band)
            if leading and (trend == 0 or leading != trend):
                pending, release_i = leading, i
                launch_high, launch_low, launch_band = quiet_high, quiet_low, band
                quiet, frozen_band, quiet_high, quiet_low = 0, math.nan, math.nan, math.nan
            elif near:
                pending = 0; quiet += 1
                quiet_high = h if quiet == 1 else max(quiet_high, h)
                quiet_low = l if quiet == 1 else min(quiet_low, l)
                if quiet == MIN_QUIET: frozen_band = band
            else:
                if quiet >= MIN_QUIET:
                    dense = pine_le(row.pastWidth, DENSE_WIDTH) and pine_ge(row.pastCrosses, DENSE_CROSSES)
                    side = 1 if pine_gt(row.md, band) else -1 if pine_lt(row.md, -band) else 0
                    if dense and side and (not ended or side != exit_side) and (trend == 0 or side != trend):
                        pending, release_i = side, i
                        launch_high, launch_low, launch_band = quiet_high, quiet_low, band
                quiet, frozen_band, quiet_high, quiet_low = 0, math.nan, math.nan, math.nan
            if pending and (not ended or pending != exit_side):
                age = i - int(release_i)
                valid = bool(leading) or launch_window(age, OPPORTUNITY, pending, row.md, launch_band, c, launch_high, launch_low)
                if not valid:
                    pending = 0
                else:
                    fires = bool(leading) or burst(pending,o,h,l,c,row.md,previous_md,row.sb,launch_high,launch_low,row.ropeHigh,row.ropeLow,row.rv,row.expansion)
                    if fires:
                        side = pending
                        raw_signal = True
                        ref = risk_reference(side, c, float(row.recentLow) if side == 1 else float(row.recentHigh), float(row.atr), tick=float(tick))
                        overheated = enable_plus and pine_gt(row.rv, 50.0) and pine_gt(row.expansion, 10.0)
                        old = trend
                        reverse = old != 0 and old != side
                        if reverse:
                            ref_exit, exit_reason, exit_price, ended = True, "opposite_reference", c, True
                            exit_side = old
                            trend = 0
                        pending = 0
                        # Master-off preserves a visible invalid signal, but it has no tradable reference.
                        accepted = (not overheated and ref.valid and old != side)
                        event, event_side = accepted or ((not enable_plus) and not ref.valid and old != side), side
                        event_reason = "overheat_rejected" if overheated else "accepted" if accepted else "invalid_risk_visible"
                        if event:
                            entry, stop, risk, protection, entry_i = c, ref.stop, ref.risk, ref.stop, i
                            peak, current, armed, stage1, stage2 = 0.0, (0.0 if ref.valid else math.nan), False, False, False
                            trend = side if accepted else 0
        rows.append({"bar_open": f.index[i], "signal": event, "raw_signal": raw_signal, "side": event_side, "signal_reason": event_reason,
                     "reference_reverse": reverse, "reference_exit": ref_exit, "reference_exit_reason": exit_reason,
                     "reference_exit_price": exit_price, "reference_exit_side": exit_side, "trend_side": trend, "signal_i": i if event else math.nan,
                     "signal_close": entry if event else math.nan, "reference_initial_stop": stop if event else math.nan,
                     "reference_risk": risk if event else math.nan, "reference_current_r": current,
                     "reference_peak_r": peak, "active_reference_protection": active_protection,
                     "reference_protection_after_close": protection if trend else math.nan,
                     "stage1_latched": stage1, "stage2_latched": stage2, "trail_armed": armed})
        previous_md, previous_middle, previous_atr = float(row.md), float(row.middle), float(row.atr)
    return pd.DataFrame(rows).set_index("bar_open")


def simulate_next_open(
    ohlcv: pd.DataFrame, refs: pd.DataFrame, *, tick: float, trade_id_prefix: str = "v1plus"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Execute close references on the next open with an independent stop clock.

    Per-bar order is intentionally explicit: a carried position may gap through
    its *own already-known* protection; a queued reversal then exits at open;
    a queued entry is filled; finally the current bar can hit that position's
    stop.  Close-derived protection is usable only on the following bar.
    """
    if not refs.index.equals(ohlcv.index):
        raise ValueError("reference and OHLC clocks must match")
    trades: list[dict] = []
    fills: list[dict] = []
    pos: dict | None = None
    pending_entry: dict | None = None
    pending_reverse = False
    next_id = 0

    def close_position(px: float, why: str, i: int, stamp, phase: str, *, censored: bool = False) -> None:
        nonlocal pos
        assert pos is not None
        gross = pos["side"] * (px / pos["entry_price"] - 1.0)
        net = gross - FEE
        trades.append({**pos, "exit_i": i, "exit_time": stamp, "exit_price": px,
                       "exit_reason": why, "gross_return": gross, "net_return": net,
                       "gross_r": gross / pos["actual_risk_frac"],
                       "net_r": net / pos["actual_risk_frac"], "censored": censored})
        fills.append({"trade_id": pos["trade_id"], "leg_no": 2,
                      "kind": "censor" if censored else "exit", "bar_open": stamp,
                      "price": px, "reason": why, "execution_phase": phase,
                      "qty_fraction": 1.0})
        pos = None

    for i, stamp in enumerate(ohlcv.index):
        r, bar = refs.iloc[i], ohlcv.iloc[i]
        o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))

        # 1. Only an opening gap is known before the queued market decisions.
        if pos is not None:
            protection = float(pos["protection"])
            gaps = o <= protection if pos["side"] == 1 else o >= protection
            if gaps:
                close_position(o, "protective_stop_gap", i, stamp, "open")
                pending_reverse = False

        # 2. A reference reversal observed at the prior close exits before
        # this bar's high/low is available.
        if pos is not None and pending_reverse:
            close_position(o, "opposite_reference_next_open", i, stamp, "open")
            pending_reverse = False

        # 3. The next-open entry itself may be stopped inside this same bar.
        if pending_entry is not None and pos is None:
            sig = pending_entry
            stop = float(sig["reference_initial_stop"])
            risk = abs(o - stop)
            side = int(sig.side)
            through_stop = o <= stop if side == 1 else o >= stop
            if risk > 0 and stop > 0 and not through_stop:
                next_id += 1
                trade_id = f"{trade_id_prefix}:{next_id}"
                pos = {"trade_id": trade_id, "signal_i": int(sig["signal_i"]),
                       "signal_time": sig.name, "signal_bar_open": sig.name,
                       "side": side, "entry_i": i, "entry_time": stamp,
                       "entry_price": o, "reference_signal_close": float(sig.signal_close),
                       "reference_initial_stop": stop, "reference_risk": float(sig.reference_risk),
                       "actual_risk": risk, "initial_risk": risk,
                       "actual_risk_frac": risk / o, "tick": tick, "mfe_r": 0.0,
                       "protection": stop}
                fills.append({"trade_id": trade_id, "leg_no": 1, "kind": "entry",
                              "bar_open": stamp, "price": o, "reason": "next_open_entry",
                              "execution_phase": "open", "qty_fraction": 1.0})
            elif through_stop:
                fills.append({"trade_id": f"{trade_id_prefix}:rejected:{int(sig['signal_i'])}",
                              "leg_no": 0, "kind": "rejected_entry", "bar_open": stamp,
                              "price": o, "reason": "entry_gap_through_initial_stop",
                              "execution_phase": "open", "qty_fraction": 0.0})
            pending_entry = None

        # 4. Intrabar path checks the position's saved protection, including a
        # position created at this bar's open.  Stop wins over favorable MFE.
        if pos is not None:
            protection = float(pos["protection"])
            stopped = l <= protection if pos["side"] == 1 else h >= protection
            if stopped:
                close_position(protection, "protective_stop", i, stamp, "intrabar")
            else:
                favorable = h if pos["side"] == 1 else l
                pos["mfe_r"] = max(float(pos["mfe_r"]), pos["side"] *
                                   (favorable - pos["entry_price"]) / pos["actual_risk"])
                # 5. The reference close may tighten this same-side position
                # for the next bar. A closing reference exit never rewrites a
                # pending actual position's protection.
                next_protection = float(r.reference_protection_after_close)
                if (not bool(r.reference_exit) and int(r.trend_side) == pos["side"]
                        and math.isfinite(next_protection)):
                    pos["protection"] = next_protection

        # 6. Closed-bar reference decisions become executable at next open.
        if bool(r.reference_exit) and str(r.reference_exit_reason) == "opposite_reference":
            pending_reverse = True
        if bool(r.signal) and str(r.signal_reason) == "accepted":
            pending_entry = r

    if pos is not None:
        last = ohlcv.iloc[-1]
        close_position(float(last.close), "boundary_mark", len(ohlcv) - 1,
                       ohlcv.index[-1], "close", censored=True)
    return pd.DataFrame(trades, columns=TRADE_COLUMNS), pd.DataFrame(fills, columns=FILL_COLUMNS)

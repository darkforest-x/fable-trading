"""Chart-reference replay for SPIKE V12.8's two add-on *hints*.

This is a research transcription of ``spike_burst_v12_8.pine``'s ``f_risk``,
``f_path`` and ``BEGIN V128 ROLL_HINTS`` sections, with Pine's early-exit
feature left at its V12.8 default of ``false``.  It deliberately models the
indicator's reference frame at the signal close: it is neither a next-open
fill simulator nor a pyramiding/accounting model.  In particular, ``net_r``
is ``NaN`` because no cost or fill model belongs to this chart reference.

The caller supplies the already-computed V9 facts.  ``facts['frame']`` is the
chart OHLCV frame (its UTC index is bar *open*), with ``atr``.  ``side`` is the
raw side that can reverse an existing frame even when V9 rejects it; ``v9`` is
the final V9 admission.  ``ready`` and ``gap`` may be frame columns or facts
arrays.  Only bars at or before the current bar are used.  For 15m charts the
last complete H1 is visible at a chart bar's open and a candidate is marked at
that chart bar's close, exactly as Pine's ``[1] + lookahead_on`` tuple.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

HOUR = pd.Timedelta(hours=1)

FRAME_COLUMNS = [
    "frame_key", "entry_i", "signal_close", "side", "entry_price",
    "initial_stop", "initial_risk", "exit_i", "exit_time", "exit_reason",
    "censored", "gross_r", "mfe_r", "net_r", "hint_count",
    "last_structural_reference", "structural_update_count", "hint_paused", "hint_invalid",
]
HINT_COLUMNS = [
    "frame_key", "signal_close", "h1_close_time", "ordinal", "price",
    "reference_price", "status", "reason",
]


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _utc_index(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    out = frame.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        raise TypeError(f"{label} must have a DatetimeIndex of bar-open times")
    if out.index.tz is None:
        raise ValueError(f"{label} index must be timezone-aware UTC")
    out.index = out.index.tz_convert("UTC")
    if not out.index.is_monotonic_increasing or out.index.has_duplicates:
        raise ValueError(f"{label} index must be strictly increasing")
    return out


def _fact(facts: Mapping, frame: pd.DataFrame, key: str, alternatives: tuple[str, ...], default):
    for candidate in (key,) + alternatives:
        if candidate in facts:
            value = facts[candidate]
            break
        if candidate in frame:
            value = frame[candidate].to_numpy()
            break
    else:
        value = default
    value = np.asarray(value)
    if value.shape != (len(frame),):
        raise ValueError(f"fact {key!r} must have one value per chart bar")
    return value


def _floor_tick(value: float, tick: float) -> float:
    return math.floor(value / tick) * tick


def _ceil_tick(value: float, tick: float) -> float:
    return math.ceil(value / tick) * tick


def _risk(side: int, entry: float, extreme: float, atr: float, tick: float) -> tuple[float, float] | None:
    """Pine ``f_risk`` with the V12.8 inherited defaults: 2 ATR / 0.2 ATR."""
    if side not in (1, -1) or entry <= 0 or atr <= 0 or tick <= 0 or not math.isfinite(extreme):
        return None
    candidate = min(extreme - 0.2 * atr, entry - 2.0 * atr) if side == 1 else max(extreme + 0.2 * atr, entry + 2.0 * atr)
    stop = _floor_tick(candidate, tick) if side == 1 else _ceil_tick(candidate, tick)
    risk = side * (entry - stop)
    return (stop, risk) if stop > 0 and risk > 0 else None


def _complete_h1(base5m: pd.DataFrame) -> dict[pd.Timestamp, tuple[float, float, float, float]]:
    """Return only contiguous, complete UTC-hour OHLC tuples from 5m source bars."""
    bars = _utc_index(base5m, "base5m")
    required = {"open", "high", "low", "close"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"base5m missing columns: {sorted(missing)}")
    rows: dict[pd.Timestamp, tuple[float, float, float, float]] = {}
    expected_step = pd.Timedelta(minutes=5)
    for hour, group in bars.groupby(bars.index.floor("h"), sort=True):
        if len(group) != 12 or group.index[0] != hour or not group.index.equals(pd.date_range(hour, periods=12, freq="5min", tz="UTC")):
            continue
        values = group[["open", "high", "low", "close"]].to_numpy(dtype=float)
        if (not np.isfinite(values).all() or np.any(values[:, 0] <= 0) or np.any(values[:, 2] <= 0)
                or np.any(values[:, 1] < values[:, 2]) or np.any(values[:, 1] < values[:, 0])
                or np.any(values[:, 1] < values[:, 3]) or np.any(values[:, 2] > values[:, 0])
                or np.any(values[:, 2] > values[:, 3])):
            continue
        rows[hour] = (float(values[0, 0]), float(values[:, 1].max()), float(values[:, 2].min()), float(values[-1, 3]))
    return rows


def _visible_h1(minutes: int, chart_open: pd.Timestamp, h1: dict[pd.Timestamp, tuple[float, float, float, float]]):
    """The exact H1 tuple and its close timestamp visible at this chart bar open."""
    hour = chart_open.floor("h") if minutes == 60 else chart_open.floor("h") - HOUR
    item = h1.get(hour)
    return (hour, hour + HOUR, item) if item is not None else None


def replay_reference_and_hints(facts: Mapping, base5m: pd.DataFrame, tick: float, minutes: int,
                               start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replay V12.8 chart frames and its non-executable H1 add-on hints.

    ``start``/``end`` are UTC signal-close bounds.  The full prefix is always
    replayed so a frame entered before ``start`` can still emit a visible hint
    inside the requested window.  The returned frame table includes only
    entries whose signal close lies in ``[start, end)``.  It is unsuitable for
    next-open fills, costs, position sizes, realised PnL, or production use.
    """
    if minutes not in (15, 60):
        raise ValueError("V12.8 hints support only 15m or 60m chart frames")
    if not isinstance(facts, Mapping) or "frame" not in facts:
        raise TypeError("facts must be a mapping containing frame")
    if not (math.isfinite(tick) and tick > 0):
        raise ValueError("tick must be positive")
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start.tz is None or end.tz is None:
        raise ValueError("start and end must be timezone-aware UTC")
    start, end = start.tz_convert("UTC"), end.tz_convert("UTC")
    if end <= start:
        raise ValueError("end must be after start")

    frame = _utc_index(facts["frame"], "facts['frame']")
    required = {"open", "high", "low", "close", "atr"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"facts['frame'] missing columns: {sorted(missing)}")
    n = len(frame)
    if n == 0:
        return _empty(FRAME_COLUMNS), _empty(HINT_COLUMNS)
    expected = pd.Timedelta(minutes=minutes)
    o, h, lo, c, atr = (frame[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close", "atr"))
    ready = _fact(facts, frame, "ready", (), np.ones(n, dtype=bool)).astype(bool)
    gap = _fact(facts, frame, "gap", ("data_gap",), np.zeros(n, dtype=bool)).astype(bool)
    # ``dataGap`` is a chart-time property.  A missing input bar must clear the
    # Pine reference even when a caller has not materialised a separate gap
    # array; no post-gap close is a tradable/reference exit price.
    prices_valid = (np.isfinite(np.c_[o, h, lo, c]).all(axis=1) & (o > 0) & (lo > 0)
                    & (h >= lo) & (h >= o) & (h >= c) & (lo <= o) & (lo <= c))
    gap = gap | ~prices_valid | np.r_[False, np.diff(frame.index.asi8) != expected.value]
    raw = _fact(facts, frame, "side", ("raw_side", "rawSide"), np.zeros(n, dtype=int)).astype(int)
    v9 = _fact(facts, frame, "v9", ("final_v9", "v9_allowed"), np.zeros(n, dtype=bool)).astype(bool)
    signal = np.where(v9, raw, 0)
    h1 = _complete_h1(base5m)
    recent_low = pd.Series(lo).rolling(5, min_periods=5).min().to_numpy()
    recent_high = pd.Series(h).rolling(5, min_periods=5).max().to_numpy()

    records: list[dict] = []
    hints: list[dict] = []
    trend = 0
    active: dict | None = None

    def finish(i: int, reason: str, price: float | None, *, censored: bool = False) -> None:
        nonlocal trend, active
        assert active is not None
        active["exit_i"] = i
        active["exit_time"] = frame.index[i] + expected
        active["exit_reason"] = reason
        active["censored"] = censored
        active["gross_r"] = (math.nan if censored else active["side"] * (float(price) - active["entry_price"]) / active["initial_risk"])
        active["net_r"] = math.nan
        records.append(active)
        trend, active = 0, None

    for i, bar_open in enumerate(frame.index):
        close_time = bar_open + expected
        ended = False
        exit_side = 0
        if gap[i] and active is not None:
            # Pine clears before ``endedThisBar`` is created.  A raw signal on
            # this same bar may therefore establish a fresh reference, but the
            # vanished parent has no observable close and remains censored.
            finish(i, "data_gap", None, censored=True)
        if ready[i] and active is not None and i > active["entry_i"]:
            old_protection = active["protection"]
            stopped = lo[i] <= old_protection if trend == 1 else h[i] >= old_protection
            exit_price = min(o[i], old_protection) if trend == 1 else max(o[i], old_protection)
            current_r = trend * ((exit_price if stopped else c[i]) - active["entry_price"]) / active["initial_risk"]
            if not stopped:
                peak = trend * ((h[i] if trend == 1 else lo[i]) - active["entry_price"]) / active["initial_risk"]
                active["mfe_r"] = max(active["mfe_r"], peak)
            armed = active["armed"] or (not stopped and current_r >= 2.0)
            if not stopped and armed and atr[i] > 0:
                candidate = _floor_tick(c[i] - trend * 4.0 * atr[i], tick) if trend == 1 else _ceil_tick(c[i] - trend * 4.0 * atr[i], tick)
                active["protection"] = max(old_protection, candidate) if trend == 1 else min(old_protection, candidate)
            active["armed"] = armed
            if stopped:
                exit_side, ended = trend, True
                finish(i, "protection_touch", exit_price)

        opposite_stop = ended and exit_side != 0 and raw[i] != exit_side
        if raw[i] != 0 and (not ended or opposite_stop) and raw[i] != trend:
            geometry = _risk(int(raw[i]), c[i], recent_low[i] if raw[i] == 1 else recent_high[i], atr[i], tick)
            if active is not None:
                finish(i, "reverse_confirmation", c[i])
            if signal[i] != 0 and geometry is not None:
                stop, risk = geometry
                key = f"{close_time.isoformat()}:{i}"
                active = {
                    "frame_key": key, "entry_i": i, "signal_close": close_time, "side": int(signal[i]),
                    "entry_price": float(c[i]), "initial_stop": stop, "initial_risk": risk,
                    "exit_i": math.nan, "exit_time": pd.NaT, "exit_reason": None, "censored": True,
                    "gross_r": math.nan, "mfe_r": 0.0, "net_r": math.nan, "hint_count": 0,
                    "protection": stop, "armed": False,
                    # V128 state bound once to this original long reference.
                    "v128_last_hour": None, "v128_seeded": False, "v128_invalid": False,
                    "v128_paused": False, "v128_set_i": None, "v128_stop": stop,
                    "v128_high": float(c[i]), "v128_previous_close": math.nan,
                    "v128_pullback_low": math.nan, "v128_hurdle": math.nan,
                    "v128_in_pullback": False, "v128_last_candidate": float(c[i]),
                    "last_structural_reference": stop, "structural_update_count": 0,
                    "hint_paused": False, "hint_invalid": False,
                }
                trend = int(signal[i])

        # The Pine block runs after the inherited reference block.  Only an
        # active long frame can own V12.8 state, and a same-bar newly opened
        # frame only binds state; it cannot consume a pre-entry H1.
        if active is None or trend != 1:
            continue
        state = active
        if state["v128_set_i"] is not None and not state["v128_paused"] and i > state["v128_set_i"] and lo[i] <= state["v128_stop"]:
            state["v128_paused"] = True
            state["hint_paused"] = True
        visible = _visible_h1(minutes, bar_open, h1)
        if visible is None:
            continue
        hour_open, hour_close, candle = visible
        if state["v128_last_hour"] == hour_close:
            continue
        frame_close = state["signal_close"]
        expected_first = frame_close.ceil("h")
        before_frame = hour_open < expected_first
        first_gap = not state["v128_seeded"] and not before_frame and hour_open > expected_first
        later_gap = state["v128_seeded"] and state["v128_last_hour"] is not None and hour_close != state["v128_last_hour"] + HOUR
        state["v128_last_hour"] = hour_close
        if first_gap or later_gap:
            state["v128_invalid"] = True
            state["hint_invalid"] = True
            continue
        if before_frame or state["v128_invalid"] or state["v128_paused"]:
            continue
        _, hour_high, hour_low, hour_end_close = candle
        if not state["v128_seeded"]:
            state["v128_high"] = max(state["entry_price"], hour_high)
            state["v128_previous_close"] = hour_end_close
            state["v128_seeded"] = True
            continue
        if not state["v128_in_pullback"]:
            if hour_end_close < state["v128_previous_close"]:
                state["v128_in_pullback"] = True
                state["v128_hurdle"] = state["v128_high"]
                state["v128_pullback_low"] = hour_low
            else:
                state["v128_high"] = max(state["v128_high"], hour_high)
        else:
            state["v128_pullback_low"] = min(state["v128_pullback_low"], hour_low)
            if hour_end_close > state["v128_hurdle"]:
                proposal = _floor_tick(state["v128_pullback_low"], tick) - tick
                improves = proposal > state["v128_stop"] and proposal > 0 and proposal < hour_end_close
                availability_cross = minutes != 60 and improves and lo[i] <= proposal
                if improves:
                    state["v128_stop"] = proposal
                    state["v128_set_i"] = i
                    state["last_structural_reference"] = proposal
                    state["structural_update_count"] += 1
                if availability_cross:
                    state["v128_paused"] = True
                    state["hint_paused"] = True
                eligible = (improves and not availability_cross and c[i] >= state["entry_price"] + 2 * state["initial_risk"]
                            and hour_end_close >= state["entry_price"] + 2 * state["initial_risk"]
                            and c[i] > state["v128_last_candidate"] and c[i] > proposal)
                if eligible and state["hint_count"] < 2:
                    state["hint_count"] += 1
                    state["v128_last_candidate"] = float(c[i])
                    if start <= close_time < end:
                        hints.append({"frame_key": state["frame_key"], "signal_close": close_time,
                                      "h1_close_time": hour_close, "ordinal": state["hint_count"],
                                      "price": float(c[i]), "reference_price": proposal,
                                      "status": "candidate", "reason": "eligible"})
                state["v128_high"] = max(state["v128_high"], hour_high)
                state["v128_in_pullback"] = False
                state["v128_pullback_low"] = math.nan
                state["v128_hurdle"] = math.nan
        state["v128_previous_close"] = hour_end_close

    if active is not None:
        active["gross_r"] = trend * (c[-1] - active["entry_price"]) / active["initial_risk"]
        active["net_r"] = math.nan
        active["exit_reason"] = "censored"
        records.append(active)
    clean = [{k: row[k] for k in FRAME_COLUMNS} for row in records]
    frames = _empty(FRAME_COLUMNS) if not clean else pd.DataFrame(clean, columns=FRAME_COLUMNS)
    if not frames.empty:
        frames = frames.loc[(frames.signal_close >= start) & (frames.signal_close < end)].reset_index(drop=True)
    out_hints = _empty(HINT_COLUMNS) if not hints else pd.DataFrame(hints, columns=HINT_COLUMNS)
    return frames, out_hints

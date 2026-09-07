"""Causal IMACD zero-departure monitor with Pine V2.2 diagnostic overlays.

Source: yoyo/evaluation/pine/imacd_dense_mtf_v2_2.pine. OHLC through the
confirmed local bar supplies IMACD 34/9, SMA/EMA 20/60/120 and SMA-seeded
ATR14. Formation uses the preceding 12 widths and 15-pair cross counts;
recent formation uses 34 bars. Focus uses ATR[1] * .10 and freezes its band
on the twelfth consecutive near-zero bar. Wick retests use current/prior
SMA20 and current OHLC/prior close only. HTF uses the most recent expected
closed higher bar at or before the local OPEN, with independent warmup.

The owner's corrected monitor signal is the first confirmed nonzero md bar
immediately after exactly zero md: no density, ATR band, signal-line cross,
HTF or trend gate. The earlier dense system remains an observation, exiting
on md returning to zero or reversing; focus release and retest are separate
observations. Only zero_breakout is the canonical notification event. HTF
permission is annotation only. This module places no orders, trains no
model, consumes no outcomes, and imports no production execution layer.

Recurrences start at the supplied history's first bar. 340 bars are required
before events; this reduces seed sensitivity but does not promise equality
with TradingView's longer history. Restarting from a sliding finite window
can change state and marginal values. Callers should retain a stable history
origin, pass confirmed continuous candles, and deduplicate events by candle.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd
from yoyo.monitor import SIGNAL_PROTOCOL

PROTOCOL_VERSION = SIGNAL_PROTOCOL
WARMUP = 340
TIMEFRAMES = {"1H": 3_600_000, "4H": 14_400_000, "1Dutc": 86_400_000}
HIGHER_TIMEFRAME = {"1H": "4H", "4H": "1Dutc"}
PROTOCOL = {
    "version": PROTOCOL_VERSION,
    "source": "yoyo/evaluation/pine/imacd_dense_mtf_v2_2.pine",
    "mode": "zero_departure",
    "notification_event": "zero_breakout",
    "notification_rule": "confirmed and ready and previous_md == 0 and md != 0; first departure bar only",
    "notification_filters": [],
    "diagnostic_events": ["entry", "release", "exit", "retest"],
    "diagnostic_system_mode": "dense",
    "length_ma": 34,
    "length_signal": 9,
    "min_zero_bars": 1,
    "dense_window": 12,
    "dense_max_width_atr": 3.0,
    "dense_min_pair_crosses": 2,
    "dense_memory": 34,
    "focus_min_bars": 12,
    "focus_atr_band": 0.10,
    "retest_ma": "SMA20",
    "warmup_index": WARMUP,
    "higher_timeframes": dict(HIGHER_TIMEFRAME),
    "htf_permission": "same md or same sh or exactly zero md",
    "htf_filters_default_entries": False,
    "htf_available_by": "local_bar_open",
    "event_price": "confirmed_signal_candle_close_not_fill",
    "system_exit": "md_returns_to_zero_or_reverses",
    "orders_enabled": False,
}


def _validated(candles: list[dict], duration: int) -> dict[str, np.ndarray]:
    """Require finite ordinary OHLCV and exact continuous, aligned open times."""
    values: dict[str, list] = {k: [] for k in ("t", "o", "h", "l", "c", "v")}
    previous = None
    for i, candle in enumerate(candles):
        try:
            raw_time = float(candle["t"])
            if not np.isfinite(raw_time) or raw_time != int(raw_time):
                raise ValueError("open timestamp must be finite integer milliseconds")
            t = int(raw_time)
            row = {k: float(candle[k]) for k in ("o", "h", "l", "c", "v")}
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"invalid candle at index {i}: {exc}") from exc
        if t < 0 or t % duration or (previous is not None and t - previous != duration):
            raise ValueError(f"candles must be aligned, unique and gap-free at index {i}")
        if not all(np.isfinite(x) for x in row.values()):
            raise ValueError(f"nonfinite OHLCV at index {i}")
        if (row["l"] <= 0 or row["v"] < 0 or row["h"] < max(row["o"], row["c"])
                or row["l"] > min(row["o"], row["c"])):
            raise ValueError(f"invalid OHLCV range at index {i}")
        if "confirm" in candle and str(candle["confirm"]) not in ("1", "True"):
            raise ValueError(f"unconfirmed candle at index {i}")
        values["t"].append(t)
        for k, x in row.items():
            values[k].append(x)
        previous = t
    return {k: np.asarray(v, dtype=np.int64 if k == "t" else float) for k, v in values.items()}


def _smma(a: np.ndarray, length: int) -> np.ndarray:
    """Pine f_smma: first length observations seed an SMA, then recurrence."""
    out = np.full(len(a), np.nan)
    if len(a) >= length:
        out[length - 1] = a[:length].mean()
        for i in range(length, len(a)):
            out[i] = (out[i - 1] * (length - 1) + a[i]) / length
    return out


def _compute(b: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """All windows end at this bar; formation explicitly shifts one bar."""
    c, h, l = b["c"], b["h"], b["l"]
    close = pd.Series(c)
    src = pd.Series((h + l + c) / 3)
    e1 = src.ewm(span=34, adjust=False).mean()
    mi = (2 * e1 - e1.ewm(span=34, adjust=False).mean()).to_numpy()
    hi, lo = _smma(h, 34), _smma(l, 34)
    md = np.where(mi > hi, mi - hi, np.where(mi < lo, mi - lo, 0.0))
    sb = pd.Series(md).rolling(9).mean().to_numpy()
    prev_close = np.r_[np.nan, c[:-1]]
    tr = np.fmax(h - l, np.fmax(np.abs(h - prev_close), np.abs(l - prev_close)))
    atr = _smma(tr, 14)
    out = {"hi": hi, "lo": lo, "mi": mi, "md": md, "sb": sb, "sh": md - sb, "atr": atr}
    ma_names = []
    for length in (20, 60, 120):
        for prefix, values in (("sma", close.rolling(length).mean()),
                               ("ema", close.ewm(span=length, adjust=False).mean())):
            name = f"{prefix}{length}"
            out[name] = values.to_numpy()
            ma_names.append(name)
    mas = np.stack([out[name] for name in ma_names])
    out["rope_high"], out["rope_low"] = mas.max(axis=0), mas.min(axis=0)
    width = np.divide(out["rope_high"] - out["rope_low"], atr,
                      out=np.full(len(c), np.nan), where=atr > 0)
    flips = np.zeros(len(c))
    for a, d in combinations(ma_names, 2):
        diff = out[a] - out[d]
        prior = np.r_[np.nan, diff[:-1]]
        flips += ((diff > 0) & (prior <= 0)) | ((diff < 0) & (prior >= 0))
    out["prior_width_atr"] = pd.Series(width).shift().rolling(12).mean().to_numpy()
    out["prior_crosses"] = pd.Series(flips).shift().rolling(12).sum().to_numpy()
    out["dense"] = (out["prior_width_atr"] <= 3) & (out["prior_crosses"] >= 2)
    out["dense_recent"] = pd.Series(out["dense"]).rolling(34, min_periods=1).max().eq(1).to_numpy()
    out["ready"] = ((np.arange(len(c)) >= WARMUP) & np.isfinite(out["prior_width_atr"])
                    & np.isfinite(sb) & np.isfinite(md))
    return out


def _number(value: Any) -> float | None:
    return float(value) if value is not None and np.isfinite(value) else None


def _side(value: float) -> str:
    return "long" if value > 0 else "short" if value < 0 else "flat"


def _permission(info: dict, side: int) -> bool | None:
    if not info["htf_known"] or not side:
        return None
    return bool(info["htf_md"] * side > 0 or info["htf_sh"] * side > 0 or info["htf_md"] == 0)


def _higher_at(open_ms: int, high: dict[str, np.ndarray], hf: dict[str, np.ndarray] | None,
               higher_duration: int) -> dict:
    """Use a closed HTF at local OPEN, never the HTF closing with this bar.

    An absent expected HTF bar is marked unknown instead of silently using
    stale data. Input gap validation and this last-bar check fail closed.
    """
    info = {"htf_known": False, "htf_side": "unknown", "htf_md": None, "htf_sh": None,
            "htf_bar_close_ms": None, "htf_long_allowed": None, "htf_short_allowed": None}
    if hf is None or not len(high["t"]):
        return info
    closes = high["t"] + higher_duration
    j = int(np.searchsorted(closes, open_ms, side="right") - 1)
    if j < WARMUP or closes[j] != (open_ms // higher_duration) * higher_duration:
        return info
    hm, hs = hf["md"][j], hf["sh"][j]
    if not np.isfinite(hm) or not np.isfinite(hs):
        return info
    info.update(htf_known=True, htf_side=_side(hm), htf_md=float(hm), htf_sh=float(hs),
                htf_bar_close_ms=int(closes[j]))
    info["htf_long_allowed"] = _permission(info, 1)
    info["htf_short_allowed"] = _permission(info, -1)
    return info


def analyze(candles: list[dict], higher: list[dict], timeframe: str) -> dict:
    """Analyze confirmed continuous OHLCV histories; timestamps are open ms.

    Return full event/chart history without freshness filtering or side
    effects. The caller owns quote confirmation, persistence, historical
    replay labeling, deduplication and delivery freshness. Zero-breakout,
    entry and release zero_bars describe the run before the event;
    chart/state zero_bars is the current run. Zero-breakout/release
    near_zero_bars preserve the preceding focus run, before this update.
    """
    if timeframe not in HIGHER_TIMEFRAME:
        raise ValueError("timeframe must be 1H or 4H")
    duration = TIMEFRAMES[timeframe]
    higher_timeframe = HIGHER_TIMEFRAME[timeframe]
    higher_duration = TIMEFRAMES[higher_timeframe]
    b = _validated(candles, duration)
    hb = _validated(higher, higher_duration)
    empty = {"phase": "loading", "near_zero_bars": 0, "zero_bars": 0, "dense": False,
             "htf_side": "unknown", "htf_allowed": None, "price": None,
             "bar_open_ms": None, "bar_close_ms": None, "bars": 0, "ready": False,
             "focus": False, "trend_side": "flat", "zero_breakout_side": None, "timeframe": timeframe,
             "higher_timeframe": higher_timeframe, "protocol_version": PROTOCOL_VERSION}
    if not len(b["t"]):
        return {"events": [], "state": empty, "chart": [], "protocol": dict(PROTOCOL)}
    f = _compute(b)
    hf = _compute(hb) if len(hb["t"]) else None
    events, chart = [], []
    zero_run = trend = focus_run = 0
    qualified = False
    frozen_band = zone_high = zone_low = None
    focus_start_ms = focus_qualified_ms = None
    last_release_index, last_release_side = -100, 0
    state = empty
    for i, raw_t in enumerate(b["t"]):
        t, close_ms = int(raw_t), int(raw_t) + duration
        md, sb = f["md"][i], f["sb"][i]
        ready, dense = bool(f["ready"][i]), bool(f["dense"][i])
        htf = _higher_at(t, hb, hf, higher_duration)
        prior_zero = zero_run
        # The canonical monitor event is independent of all overlay states.
        previous_md = f["md"][i - 1] if i else np.nan
        zero_breakout_side = (1 if md > 0 else -1 if md < 0 else 0) if ready and previous_md == 0 else 0
        entry_side = exit_side = release_side = retest_side = 0
        prior_focus = focus_run
        release_band = release_high = release_low = None
        release_start = release_qualified = None
        if trend and md * trend <= 0:
            exit_side, trend = trend, 0
        anchor = ready and i > 0 and f["md"][i - 1] == 0 and prior_zero >= 1
        if trend == 0 and anchor and dense and md != 0:
            entry_side = trend = 1 if md > 0 else -1
        zero_run = (zero_run + 1 if md == 0 and np.isfinite(f["hi"][i])
                    and np.isfinite(f["lo"][i]) else 0)

        candidate_band = .10 * f["atr"][i - 1] if i else np.nan
        if ready and np.isfinite(candidate_band):
            magnitude = max(abs(md), abs(sb))
            if qualified:
                if magnitude <= frozen_band:
                    focus_run += 1
                    zone_high = max(zone_high, b["h"][i])
                    zone_low = min(zone_low, b["l"][i])
                else:
                    release_side = 1 if md > frozen_band else -1 if md < -frozen_band else 0
                    release_band, release_high, release_low = frozen_band, zone_high, zone_low
                    release_start, release_qualified = focus_start_ms, focus_qualified_ms
                    if release_side:
                        last_release_index, last_release_side = i, release_side
                    qualified, focus_run = False, 0
                    frozen_band = zone_high = zone_low = None
                    focus_start_ms = focus_qualified_ms = None
            elif magnitude <= candidate_band:
                focus_run += 1
                if focus_run == 1:
                    zone_high, zone_low, focus_start_ms = b["h"][i], b["l"][i], t
                else:
                    zone_high = max(zone_high, b["h"][i])
                    zone_low = min(zone_low, b["l"][i])
                if focus_run >= 12:
                    qualified, frozen_band, focus_qualified_ms = True, float(candidate_band), t
            else:
                focus_run = 0
                zone_high = zone_low = None
                focus_start_ms = focus_qualified_ms = None

        ma = f["sma20"][i]
        if ready and qualified and i > 0 and np.isfinite(ma) and np.isfinite(f["sma20"][i - 1]):
            body_low, body_high = min(b["o"][i], b["c"][i]), max(b["o"][i], b["c"][i])
            if (b["c"][i - 1] > f["sma20"][i - 1] and b["l"][i] <= ma
                    and body_low >= ma and b["c"][i] > ma and b["l"][i] < body_low):
                retest_side = 1
            elif (b["c"][i - 1] < f["sma20"][i - 1] and b["h"][i] >= ma
                    and body_high <= ma and b["c"][i] < ma and b["h"][i] > body_high):
                retest_side = -1

        def event(kind: str, side: int, reason: str) -> dict:
            return {"bar_open_ms": t, "bar_close_ms": close_ms, "kind": kind,
                    "side": _side(side), "price": float(b["c"][i]),
                    "zero_bars": int(prior_zero if kind in ("zero_breakout", "entry", "release") else zero_run),
                    "near_zero_bars": int(prior_focus if kind in ("zero_breakout", "release") else focus_run),
                    "dense": dense, "dense_recent": bool(f["dense_recent"][i]),
                    "htf_allowed": _permission(htf, side), **htf,
                    "timeframe": timeframe, "higher_timeframe": higher_timeframe,
                    "md": _number(md), "sb": _number(sb),
                    "prior_width_atr": _number(f["prior_width_atr"][i]),
                    "prior_crosses": _number(f["prior_crosses"][i]),
                    "reason": reason, "protocol_version": PROTOCOL_VERSION,
                    "confirmed": True, "price_basis": "signal_candle_close",
                    "is_monitor_signal": kind == "zero_breakout",
                    "is_system_entry": kind == "entry"}

        if zero_breakout_side:
            breakout = event("zero_breakout", zero_breakout_side,
                             "IMACD 主线由精确零轴首次转正或转负，本根收盘确认；均线密集、近零区和高周期仅作背景。")
            breakout["previous_md"] = _number(previous_md)
            events.append(breakout)
        if exit_side:
            events.append(event("exit", exit_side, "IMACD 主线回到零轴或反向，原系统趋势结束。"))
        if entry_side:
            events.append(event("entry", entry_side, "至少一根精确零轴后离零，前 12 根满足六均线密集；原系统确认启动。"))
        if release_side:
            release = event("release", release_side, "连续近零蓄势后主线越过冻结阈值；这是视觉释放，独立于原系统入场。")
            release.update(focus_band=_number(release_band), zone_high=_number(release_high),
                           zone_low=_number(release_low), focus_start_ms=release_start,
                           focus_qualified_ms=release_qualified,
                           zone_end_ms=t, system_entry_same_bar=entry_side == release_side)
            events.append(release)
        if retest_side:
            retest = event("retest", retest_side, "已确认蓄势区内影线触及 SMA20，实体守在线外，收盘回到原侧。")
            retest.update(retest_ma="SMA20", retest_price=_number(ma), focus_band=_number(frozen_band))
            events.append(retest)
        row = {k: (int(b[k][i]) if k == "t" else float(b[k][i])) for k in ("t", "o", "h", "l", "c", "v")}
        row.update({name: _number(f[name][i]) for name in
                    ("md", "sb", "sh", "atr", "sma20", "ema20", "sma60", "ema60",
                     "sma120", "ema120", "prior_width_atr", "prior_crosses")})
        row.update(bar_close_ms=close_ms, ready=ready, focus=bool(qualified),
                   focus_band=_number(frozen_band), focus_start_ms=focus_start_ms,
                   focus_qualified_ms=focus_qualified_ms, zone_high=_number(zone_high),
                   zone_low=_number(zone_low), near_zero_bars=int(focus_run), zero_bars=int(zero_run),
                   dense=dense, dense_recent=bool(f["dense_recent"][i]),
                   retest_side=_side(retest_side) if retest_side else None,
                   release_side=_side(release_side) if release_side else None,
                   zero_breakout_side=_side(zero_breakout_side) if zero_breakout_side else None,
                   entry_side=_side(entry_side) if entry_side else None,
                   exit_side=_side(exit_side) if exit_side else None,
                   trend_side=_side(trend),
                   glow_side=(_side(last_release_side) if ready and i - last_release_index < 12
                              and md * last_release_side > 0 and not qualified else None))
        chart.append(row)
        phase = ("loading" if not ready else "ready" if qualified else "building" if focus_run
                 else _side(trend) if trend else "neutral")
        direction = trend or (1 if md > 0 else -1 if md < 0 else 0)
        state = {"phase": phase, "near_zero_bars": int(focus_run), "zero_bars": int(zero_run),
                 "dense": dense, "dense_recent": bool(f["dense_recent"][i]),
                 "htf_allowed": _permission(htf, direction), **htf,
                 "price": float(b["c"][i]), "bar_open_ms": t, "bar_close_ms": close_ms,
                 "bars": i + 1, "ready": ready, "focus": bool(qualified),
                 "focus_band": _number(frozen_band), "focus_start_ms": focus_start_ms,
                 "focus_qualified_ms": focus_qualified_ms, "zone_high": _number(zone_high),
                 "zone_low": _number(zone_low), "trend_side": _side(trend),
                 "md": _number(md), "sb": _number(sb), "atr": _number(f["atr"][i]),
                 "prior_width_atr": _number(f["prior_width_atr"][i]),
                 "prior_crosses": _number(f["prior_crosses"][i]),
                 "retest_side": row["retest_side"], "release_side": row["release_side"],
                 "zero_breakout_side": row["zero_breakout_side"],
                 "timeframe": timeframe, "higher_timeframe": higher_timeframe,
                 "protocol_version": PROTOCOL_VERSION}
    return {"events": events, "state": state, "chart": chart, "protocol": dict(PROTOCOL)}

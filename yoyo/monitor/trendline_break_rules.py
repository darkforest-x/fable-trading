"""Project the 4H main resistance line onto 1H closes.

The source is the default resistance engine in
``yoyo/evaluation/pine/spike_signal_cloud_v1_9.pine`` (4H line-selection V1.3).
This module only detects the first 1H close crossing each known, still-active
line. Two 4H closes can retire the line under Pine's rules; the hourly signal
itself needs only one confirmed crossover and no cloud filter.

Input bars are mappings with ``time`` (UTC bar-open milliseconds) and numeric
``open/high/low/close`` values. Only bars whose full interval has closed by
``asof_ms`` participate. Every returned event is an observation, not an order.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

FOUR_HOUR_MS = 4 * 60 * 60 * 1000
ONE_HOUR_MS = 60 * 60 * 1000
WINDOW = 360
SWING = 3
MIN_SPAN = 72
TOUCH_GAP = 24
NEAR_ATR = 0.65
MAX_GAP_ATR = 5.0
BREAK_BUFFER_ATR = 0.15
PREBREAK_LOW_BARS = 11


@dataclass(frozen=True)
class _Bar:
    time: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class _Pivot:
    x: int
    time: int
    price: float
    atr: float


@dataclass
class _Candidate:
    a: _Pivot
    b: _Pivot
    touches: int
    fit: float = 0.0

    def level_at_index(self, x: float) -> float:
        return self.b.price + (self.b.price - self.a.price) * (x - self.b.x) / (self.b.x - self.a.x)

    def level_at_time(self, timestamp: int) -> float:
        return self.b.price + (self.b.price - self.a.price) * (timestamp - self.b.time) / (self.b.time - self.a.time)


@dataclass
class _Line:
    a: _Pivot
    b: _Pivot
    born_ms: int
    known_ms: int
    retired_ms: int | None = None

    def level_at_time(self, timestamp: int) -> float:
        return self.b.price + (self.b.price - self.a.price) * (timestamp - self.b.time) / (self.b.time - self.a.time)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field} must be an integer")
    return parsed


def _closed_bars(rows: Sequence[Mapping[str, Any]], period_ms: int, asof_ms: int) -> list[_Bar]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise ValueError("bars must be a sequence of OHLC mappings")
    bars: list[_Bar] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("each bar must be an OHLC mapping")
        timestamp = _integer(row.get("time"), "bar time")
        if timestamp + period_ms > asof_ms:
            continue
        values: dict[str, float] = {}
        for name in ("open", "high", "low", "close"):
            try:
                number = float(row[name])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"bar {name} must be finite") from exc
            if not math.isfinite(number):
                raise ValueError(f"bar {name} must be finite")
            values[name] = number
        if values["high"] < max(values["open"], values["close"], values["low"]):
            raise ValueError("bar high must be at least open, low, and close")
        if values["low"] > min(values["open"], values["close"], values["high"]):
            raise ValueError("bar low must be at most open, high, and close")
        bars.append(_Bar(timestamp, **values))

    bars.sort(key=lambda bar: bar.time)
    if any(left.time == right.time for left, right in zip(bars, bars[1:])):
        raise ValueError("bar times must be unique")
    return bars


def _pine_atr(bars: Sequence[_Bar], length: int = 14) -> np.ndarray:
    """Pine ta.atr(14): SMA seed of true range, then Wilder RMA."""
    n = len(bars)
    out = np.full(n, np.nan, dtype=float)
    if n == 0:
        return out
    high = np.fromiter((bar.high for bar in bars), dtype=float, count=n)
    low = np.fromiter((bar.low for bar in bars), dtype=float, count=n)
    close = np.fromiter((bar.close for bar in bars), dtype=float, count=n)
    previous = np.r_[np.nan, close[:-1]]
    true_range = np.where(np.isnan(previous), high - low,
                          np.maximum(high - low, np.maximum(np.abs(high - previous), np.abs(low - previous))))
    if n < length:
        return out
    out[length - 1] = true_range[:length].mean()
    for i in range(length, n):
        out[i] = out[i - 1] + (true_range[i] - out[i - 1]) / length
    return out


def _confirmed_pivots(high: np.ndarray) -> np.ndarray:
    """Return pivot highs on their confirmation bar; TradingView allows left ties."""
    confirmed = np.zeros(len(high), dtype=bool)
    for pivot in range(SWING, len(high) - SWING):
        left = high[pivot - SWING:pivot]
        right = high[pivot + 1:pivot + SWING + 1]
        if high[pivot] >= left.max() and high[pivot] > right.max():
            confirmed[pivot + SWING] = True
    return confirmed


def _outranks(candidate: _Candidate, other: _Candidate | None) -> bool:
    return other is None or candidate.touches > other.touches or (
        candidate.touches == other.touches and candidate.fit < other.fit)


def _select_lines(bars: Sequence[_Bar], asof_ms: int) -> list[_Line]:
    """Causal port of V1.9's default 4H resistance line state machine."""
    n = len(bars)
    if n < 2 * SWING + 1:
        return []
    high = np.fromiter((bar.high for bar in bars), dtype=float, count=n)
    open_ = np.fromiter((bar.open for bar in bars), dtype=float, count=n)
    close = np.fromiter((bar.close for bar in bars), dtype=float, count=n)
    atr = _pine_atr(bars)
    confirmed = _confirmed_pivots(high)
    pivots: list[_Pivot] = []
    pending: _Candidate | None = None
    due: int | None = None
    current: _Candidate | None = None
    current_line: _Line | None = None
    ended_at_index: int | None = None
    above_count = 0
    lines: list[_Line] = []

    def clean(candidate: _Candidate, x: int) -> bool:
        idx = np.arange(candidate.a.x, x + 1)
        levels = candidate.level_at_index(idx)
        atr_window = atr[idx]
        wick_bad = high[idx] > levels + 0.30 * atr_window
        body_bad = np.maximum(open_[idx], close[idx]) > levels + 0.10 * atr_window
        if bool(np.any(wick_bad | body_bad)):
            return False
        valid = np.isfinite(atr_window) & (atr_window > 0)
        gaps = np.maximum(levels[valid] - high[idx][valid], 0.0) / atr_window[valid]
        candidate.fit = float(gaps.mean()) if len(gaps) else 0.0
        return candidate.fit <= MAX_GAP_ATR

    def pick(b: _Pivot, closing: float, x: int) -> _Candidate | None:
        best: _Candidate | None = None
        for idx, a in enumerate(pivots):
            if idx == len(pivots) - 1:
                break
            if b.x - a.x < max(MIN_SPAN, 2 * TOUCH_GAP) or a.price - b.price < 2 * b.atr:
                continue
            test = _Candidate(a, b, 2)
            last_touch = a.x
            for middle in pivots[idx + 1:]:
                if middle.x > b.x - TOUCH_GAP:
                    break
                if (middle.x - last_touch >= TOUCH_GAP and
                        abs(middle.price - test.level_at_index(middle.x)) <= NEAR_ATR * middle.atr):
                    test.touches += 1
                    last_touch = middle.x
            if test.touches >= 3 and closing <= test.level_at_index(x) and clean(test, x):
                if _outranks(test, best):
                    best = test
        return best

    for x, bar in enumerate(bars):
        closing = bar.close
        if current is not None and ended_at_index is None:
            value = current.level_at_index(x)
            above_count = above_count + 1 if closing > value + BREAK_BUFFER_ATR * atr[x] else 0
            broken = above_count >= 2
            aged = x - current.a.x >= WINDOW
            if broken or aged:
                if current_line is not None:
                    current_line.retired_ms = bar.time + FOUR_HOUR_MS
                ended_at_index = x
                above_count = 0
                pending = None
                due = None

        if confirmed[x] and math.isfinite(float(atr[x - SWING])):
            pivot = _Pivot(x - SWING, bars[x - SWING].time, float(high[x - SWING]), float(atr[x - SWING]))
            while pivots and x - pivots[0].x > WINDOW:
                pivots.pop(0)
            active = current is not None and ended_at_index is None
            search = (current.level_at_index(x) - closing > 3 * atr[x] if active
                      else ended_at_index is None or pivot.x - ended_at_index >= TOUCH_GAP)
            if search and len(pivots) >= 2:
                candidate = pick(pivot, closing, x)
                if candidate is not None:
                    distinct = (not active or (candidate.a.x > current.b.x and
                                               candidate.level_at_index(x) < current.level_at_index(x)))
                    if distinct and _outranks(candidate, pending):
                        if pending is None:
                            due = x + 2 * SWING
                        pending = candidate
            pivots.append(pivot)

        if pending is not None and due is not None and x >= due:
            active = current is not None and ended_at_index is None
            still_relevant = (not active or
                              (current.level_at_index(x) - closing > 3 * atr[x] and
                               pending.level_at_index(x) < current.level_at_index(x)))
            if (still_relevant and x - pending.a.x <= WINDOW and
                    closing <= pending.level_at_index(x) and clean(pending, x)):
                # The replacement is not knowable until this 4H bar closes.
                known_ms = bar.time + FOUR_HOUR_MS
                if active and current_line is not None:
                    current_line.retired_ms = known_ms
                current = pending
                current_line = _Line(pending.a, pending.b, bar.time, known_ms)
                lines.append(current_line)
                ended_at_index = None
                above_count = 0
            pending = None
            due = None

    # Do not expose source state after asof, even if the caller passed extra bars.
    return [line for line in lines if line.known_ms <= asof_ms]


def _lines_by_contiguous_4h_segment(bars: Sequence[_Bar], asof_ms: int) -> list[_Line]:
    """Reset the Pine-style state at every missing or misaligned 4H bar."""
    if not bars:
        return []
    segments: list[list[_Bar]] = []
    current = [bars[0]]
    for bar in bars[1:]:
        if bar.time - current[-1].time == FOUR_HOUR_MS:
            current.append(bar)
        else:
            segments.append(current)
            current = [bar]
    segments.append(current)

    lines: list[_Line] = []
    for index, segment in enumerate(segments):
        # Treat source gaps as missing market data: reset the ATR warmup as well
        # as pivot/line state so old prices cannot seed a new segment.
        segment_lines = _select_lines(segment, asof_ms)
        # A segment is valid only through the close of the immediately
        # following 4H interval. This lets completed 1H candles inside a
        # currently forming 4H bar use the last known line, but prevents a
        # stale tail or a long 4H gap from extending it indefinitely. Subtract
        # 1ms so a crossing at the missing 4H close itself is not certified.
        valid_until_ms = segment[-1].time + 2 * FOUR_HOUR_MS - 1
        if index + 1 < len(segments):
            valid_until_ms = min(valid_until_ms, segments[index + 1][0].time - 1)
        for line in segment_lines:
            if line.retired_ms is None or line.retired_ms > valid_until_ms:
                line.retired_ms = valid_until_ms
        lines.extend(segment_lines)
    return lines


def events(symbol: str, four_hour: Sequence[Mapping[str, Any]], hourly: Sequence[Mapping[str, Any]],
           asof_ms: int) -> list[dict[str, Any]]:
    """Return first 1H close-crossing events for known, active 4H resistance lines.

    A line becomes knowable at the close of its 4H birth bar. A line stops being
    eligible after the close that confirms a 4H break/age-out or publishes its
    replacement (the end close itself is included, matching the research scan).
    To certify the *first* cross, hourly history must include the bar opening one
    hour before birth-close and remain gap-free from there through the candidate.
    Otherwise the line emits no event: a missing earlier close or gap could hide
    an earlier crossing. The line level is evaluated at each 1H bar's open time.
    """
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("symbol must be a non-empty string")
    cutoff = _integer(asof_ms, "asof_ms")
    source = _closed_bars(four_hour, FOUR_HOUR_MS, cutoff)
    chart = _closed_bars(hourly, ONE_HOUR_MS, cutoff)
    if not chart:
        return []
    # A source gap starts a new lifecycle segment. Historical signals before
    # an old gap remain available; each segment's end is separately bounded.
    if not source:
        return []
    lines = _lines_by_contiguous_4h_segment(source, cutoff)
    if not lines:
        return []

    found: list[dict[str, Any]] = []
    hourly_index = {bar.time: i for i, bar in enumerate(chart)}
    for line in lines:
        if line.a.time >= line.b.time:
            continue
        boundary_previous_ms = line.known_ms - ONE_HOUR_MS
        previous_index = hourly_index.get(boundary_previous_ms)
        if previous_index is None:
            # No known previous close at the instant the line becomes visible.
            continue
        for current_index in range(previous_index + 1, len(chart)):
            previous, current = chart[previous_index], chart[current_index]
            if current.time != previous.time + ONE_HOUR_MS:
                # A gap could contain an earlier first cross. Never resume this line.
                break
            candle_ms = current.time
            confirmed_ms = candle_ms + ONE_HOUR_MS
            if candle_ms < line.known_ms:
                previous_index = current_index
                continue
            if line.retired_ms is not None and confirmed_ms > line.retired_ms:
                break
            previous_level = line.level_at_time(previous.time)
            level = line.level_at_time(candle_ms)
            if previous.close <= previous_level and current.close > level:
                identity = f"trendline-v1.9-4h-to-1h|{symbol}|{line.a.time}|{line.b.time}|{candle_ms}"
                prebreak_start_index = max(0, current_index - PREBREAK_LOW_BARS + 1)
                prebreak_window = chart[prebreak_start_index:current_index + 1]
                has_prebreak_window = (len(prebreak_window) == PREBREAK_LOW_BARS and all(
                    right.time - left.time == ONE_HOUR_MS
                    for left, right in zip(prebreak_window, prebreak_window[1:])))
                found.append({
                    "event_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                    "symbol": symbol,
                    "confirmed_ms": confirmed_ms,
                    "candle_ms": candle_ms,
                    "level": level,
                    "close": current.close,
                    "a_ms": line.a.time,
                    "a_price": line.a.price,
                    "b_ms": line.b.time,
                    "b_price": line.b.price,
                    # Freeze the local structural floor at the breakout close.
                    # The feed checks this floor against the first +10% rally after confirmation.
                    "prebreak_low": (min(bar.low for bar in prebreak_window)
                                     if has_prebreak_window else None),
                    "prebreak_low_start_ms": (prebreak_window[0].time if has_prebreak_window else None),
                    "prebreak_low_end_ms": candle_ms,
                    "prebreak_low_bars": PREBREAK_LOW_BARS if has_prebreak_window else len(prebreak_window),
                })
                break
            previous_index = current_index
    return sorted(found, key=lambda event: (event["confirmed_ms"], event["event_id"]))

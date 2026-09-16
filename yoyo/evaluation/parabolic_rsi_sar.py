"""Faithful port of the Parabolic SAR on RSI from Parabolic RSI [ChartPrime].

Source: ``experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/
sources/NI0Qhwy7.pine`` (MPL-2.0, SHA256 ca5785ff...51098), lines 28-100.  The
port keeps three things the published script does and a careless rewrite drops:

* the initialisation branch runs while ``bar_index <= len + 2``, where ``len``
  is the RSI length closed over from the outer scope, not the SAR's own input;
* ``isBelow`` means the SAR sits below the RSI, i.e. the bullish state, and a
  signal is the bar where that state flips;
* the published "strong" diamond tests the SAR VALUE against the 30/70
  thresholds (``sar_rsi <= lower_``), not the RSI value.  Those are different
  series and only the first is what the chart's big ◈ marks.

Pine's na semantics are reproduced explicitly: a comparison against na is
false, arithmetic propagates na, and math.max/math.min return na if either
argument is na.  Values therefore stay undefined through the RSI warmup
instead of silently seeding the recursion with a substituted number.
"""
from __future__ import annotations

import math

import numpy as np


def _gt(a: float, b: float) -> bool:
    return not (math.isnan(a) or math.isnan(b)) and a > b


def _lt(a: float, b: float) -> bool:
    return not (math.isnan(a) or math.isnan(b)) and a < b


def _max(a: float, b: float) -> float:
    return math.nan if math.isnan(a) or math.isnan(b) else max(a, b)


def _min(a: float, b: float) -> float:
    return math.nan if math.isnan(a) or math.isnan(b) else min(a, b)


def pine_sar(src, *, start: float = 0.02, increment: float = 0.02,
             maximum: float = 0.2, init_bars: int = 16) -> tuple[np.ndarray, np.ndarray]:
    """Return the SAR series and its ``isBelow`` (bullish) state per bar.

    ``init_bars`` is the published ``len + 2`` bound with the default RSI
    length 14.  ``src_high``/``src_low`` are ``src ± 1`` exactly as the source
    defines them for an oscillator that has no high/low of its own.
    """
    if not (0 < start and 0 < increment and start <= maximum and init_bars >= 0):
        raise ValueError("start, increment and maximum must be positive with start <= maximum")
    values = np.asarray(src, dtype=float)
    if values.ndim != 1:
        raise ValueError("src must be one dimensional")
    high, low = values + 1.0, values - 1.0
    result = max_min = acceleration = math.nan
    is_below = False
    out_sar = np.full(len(values), math.nan)
    out_below = np.zeros(len(values), dtype=bool)
    for i in range(len(values)):
        first_trend_bar = False
        if i <= init_bars:
            if i > 0 and _gt(values[i], values[i - 1]):
                is_below, max_min, result = True, high[i], low[i - 1]
            else:
                is_below, max_min, result = False, low[i], high[i - 1] if i > 0 else math.nan
            first_trend_bar, acceleration = True, start
        result = result + acceleration * (max_min - result)
        if is_below:
            if _gt(result, low[i]):
                first_trend_bar, is_below = True, False
                result, max_min, acceleration = _max(high[i], max_min), low[i], start
        elif _lt(result, high[i]):
            first_trend_bar, is_below = True, True
            result, max_min, acceleration = _min(low[i], max_min), high[i], start
        if not first_trend_bar:
            if is_below:
                if _gt(high[i], max_min):
                    max_min, acceleration = high[i], min(acceleration + increment, maximum)
            elif _lt(low[i], max_min):
                max_min, acceleration = low[i], min(acceleration + increment, maximum)
        if is_below:
            if i >= 1:
                result = _min(result, low[i - 1])
            if i >= 2:
                result = _min(result, low[i - 2])
        else:
            if i >= 1:
                result = _max(result, high[i - 1])
            if i >= 2:
                result = _max(result, high[i - 2])
        out_sar[i], out_below[i] = result, is_below
    return out_sar, out_below


def diamonds(sar: np.ndarray, is_below: np.ndarray, *, lower: float = 30.0,
             upper: float = 70.0) -> dict[str, np.ndarray]:
    """Return the published flip and strong-flip events, on confirmed bars.

    ``flip_up``/``flip_dn`` are ``sig_up``/``sig_dn``; ``strong_up``/
    ``strong_dn`` are ``s_sig_up``/``s_sig_dn``, the big ◈ the script draws on
    the price chart.  The strong test compares the SAR value, as published.
    """
    sar = np.asarray(sar, dtype=float)
    is_below = np.asarray(is_below, dtype=bool)
    if sar.shape != is_below.shape or sar.ndim != 1:
        raise ValueError("aligned one dimensional sar and state arrays are required")
    if not lower < upper:
        raise ValueError("lower must be below upper")
    changed = np.r_[False, is_below[1:] != is_below[:-1]]
    flip_up, flip_dn = changed & is_below, changed & ~is_below
    finite = np.isfinite(sar)
    return dict(flip_up=flip_up, flip_dn=flip_dn,
                strong_up=flip_up & finite & (sar <= lower),
                strong_dn=flip_dn & finite & (sar >= upper))


def within(event: np.ndarray, bars: int) -> np.ndarray:
    """True where ``event`` occurred on this bar or the ``bars - 1`` before it.

    The window is causal and inclusive of the current bar; ``bars=1`` is the
    same-bar test.  No future bar can turn an earlier False into True.
    """
    event = np.asarray(event, dtype=bool)
    if not isinstance(bars, int) or isinstance(bars, bool) or bars < 1:
        raise ValueError("bars must be a positive integer")
    out = np.zeros(len(event), dtype=bool)
    for shift in range(min(bars, len(event))):
        out[shift:] |= event[:len(event) - shift] if shift else event
    return out

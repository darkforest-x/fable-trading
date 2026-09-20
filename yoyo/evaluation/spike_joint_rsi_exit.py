"""Causal ChartPrime RSI exit features for offline SPIKE replay research.

This module is deliberately an adapter, not a new execution engine.  It
segments the shared ChartPrime RSI14 / Parabolic-SAR calculation at supplied
or observed bar boundaries, then overlays confirmed RSI exits onto the frozen
long-only frontend's raw reverse stream.  It does not choose a diamond count,
timeframe, profit gate, or any live-policy default.
"""
from __future__ import annotations

from dataclasses import replace
from numbers import Integral

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_increment as increment
from yoyo.evaluation.parabolic_rsi_sar import diamonds, pine_sar
from yoyo.evaluation.spike_v1_v8_be05 import PreparedArm
from yoyo.evaluation.spike_v6_bb_squeeze import _rsi_wilder


RSI_PERIOD = 14
LOWER, UPPER = 30.0, 70.0
SAR_START, SAR_INCREMENT, SAR_MAXIMUM = 0.02, 0.02, 0.2


def _breaks(frame: pd.DataFrame, gap: np.ndarray, minutes: int) -> tuple[np.ndarray, np.ndarray]:
    """Return region starts and rows which cannot carry an oscillator value."""
    close = frame.close.to_numpy(float)
    invalid = ~np.isfinite(close)
    discontinuity = np.zeros(len(frame), dtype=bool)
    if len(frame) > 1:
        delta = frame.index[1:] - frame.index[:-1]
        discontinuity[1:] = delta != pd.Timedelta(minutes=minutes)
    starts = gap | discontinuity
    # A non-finite close cannot be in either region.  Its successor (if any)
    # begins with a fresh Wilder/SAR state.
    starts[1:] |= invalid[:-1]
    return starts, invalid


def chartprime_strong_side(frame: pd.DataFrame, *, gap: np.ndarray | None = None,
                           minutes: int) -> pd.DataFrame:
    """Return aligned, causal ChartPrime strong-diamond features per closed bar.

    ``frame`` supplies a timezone-aware ``DatetimeIndex`` of bar-open times
    and a numeric ``close`` column.  ``minutes`` is the explicit bar width;
    a timestamp discontinuity, a true supplied ``gap`` row, or a non-finite
    close starts a fresh region.  Each valid region uses only its own current
    and prior closes: shared Wilder RSI14, RSI Parabolic SAR (.02/.02/.2), and
    ChartPrime's SAR-value thresholds 30/70.  The same index is returned with
    ``rsi``, ``sar``, integer ``strong_side`` (+1/-1/0), and ``known`` where
    both RSI and SAR are finite.  No later row can revise an earlier output.
    """
    if not isinstance(frame, pd.DataFrame) or "close" not in frame:
        raise ValueError("frame requires a close column")
    if (not isinstance(minutes, Integral) or isinstance(minutes, bool) or minutes <= 0):
        raise ValueError("minutes must be a positive integer")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("frame requires a timezone-aware DatetimeIndex")
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("frame index must be unique and chronological")
    n = len(frame)
    if gap is None:
        gap_a = np.zeros(n, dtype=bool)
    else:
        gap_a = np.asarray(gap)
        if gap_a.ndim != 1 or len(gap_a) != n or gap_a.dtype != np.dtype(bool):
            raise ValueError("gap must be an aligned one-dimensional bool array")
        gap_a = gap_a.copy()

    rsi = np.full(n, np.nan)
    sar = np.full(n, np.nan)
    strong_side = np.zeros(n, dtype=int)
    starts, invalid = _breaks(frame, gap_a, int(minutes))
    # A finite gap/discontinuity row starts, and belongs to, its new region.
    # A non-finite row is undefined; its successor is reseeded above.
    i = 0
    while i < n:
        if invalid[i]:
            i += 1
            continue
        end = i + 1
        while end < n and not invalid[end] and not starts[end]:
            end += 1
        close = frame.close.iloc[i:end].astype(float)
        segment_rsi = _rsi_wilder(close, RSI_PERIOD).to_numpy(float)
        segment_sar, below = pine_sar(segment_rsi, start=SAR_START,
                                      increment=SAR_INCREMENT, maximum=SAR_MAXIMUM,
                                      init_bars=RSI_PERIOD + 2)
        events = diamonds(segment_sar, below, lower=LOWER, upper=UPPER)
        rsi[i:end] = segment_rsi
        sar[i:end] = segment_sar
        strong_side[i:end] = np.where(events["strong_up"], 1,
                                      np.where(events["strong_dn"], -1, 0))
        i = end
    return pd.DataFrame({"rsi": rsi, "sar": sar, "strong_side": strong_side,
                         "known": np.isfinite(rsi) & np.isfinite(sar)}, index=frame.index)


def attempt_with_rsi_exit(prepared: PreparedArm, i: int,
                          rsi_exit_at_close: np.ndarray) -> tuple[str, dict | None]:
    """Replay one frozen long entry with confirmed RSI exits overlaid at close.

    ``rsi_exit_at_close`` is an aligned one-dimensional ``bool`` array.  A
    true value becomes a raw short (-1) only in a temporary ``PreparedArm``;
    caller-owned arrays and the frozen engine are unchanged.  The frozen
    engine therefore fills a surviving close trigger at the next bar open and
    retains its original gap, stop, and raw-opposite precedence.
    """
    if not isinstance(prepared, PreparedArm):
        raise ValueError("prepared must be the frozen PreparedArm")
    mask = np.asarray(rsi_exit_at_close)
    if mask.ndim != 1 or len(mask) != len(prepared.frame) or mask.dtype != np.dtype(bool):
        raise ValueError("rsi_exit_at_close must be an aligned one-dimensional bool array")
    raw_side = np.asarray(prepared.raw_side)
    if raw_side.ndim != 1 or len(raw_side) != len(prepared.frame):
        raise ValueError("prepared raw_side is not aligned to its frame")
    overlaid = raw_side.copy()
    overlaid[mask] = -1
    status, result = increment.attempt(replace(prepared, raw_side=overlaid), i)
    if result is None or result.get("exit_reason") != "opposite_v6_next_open":
        return status, result
    trigger_i = int(result["exit_i"]) - 1
    if trigger_i < 0 or not mask[trigger_i] or raw_side[trigger_i] == -1:
        return status, result
    relabelled = dict(result)
    relabelled["exit_reason"] = "rsi_seventh_reverse_next_open"
    relabelled["rsi_exit_trigger_i"] = trigger_i
    relabelled["rsi_exit_trigger_close_time"] = (
        prepared.frame.index[trigger_i] + pd.Timedelta(minutes=prepared.context.minutes)
    )
    return status, relabelled

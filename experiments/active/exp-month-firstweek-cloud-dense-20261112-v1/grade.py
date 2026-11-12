"""Vectorized causal port of the MA Dense Launch V1 hard gates.

``grade_hits`` returns confirmed signals on their fifth release-bar close.  It
mirrors ``yoyo/evaluation/pine/ma_dense_launch_v1.pine`` (four- and five-bar
cores, then five confirmation bars), including V1's same-direction 240-minute
cooldown.  It does not draw/backfill a label to the core or make V2 scores.

Input rows are continuous, already-closed float64 OHLC bars in
``[open_time_ms, open, high, low, close]`` order.  All feature windows for a
returned index end at that index; later bars are never read.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


_ATR_LENGTH = 14
_COOLDOWN_MS = 240 * 60 * 1000
_CHUNK = 65_536
_MA_PERIODS = (20, 60, 120)


def _pine_rma(values: np.ndarray, length: int) -> np.ndarray:
    """Wilder RMA with the Pine/reference SMA seed, matching the V1 mirror."""
    out = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) < length:
        return out
    out[length - 1] = float(np.mean(values[:length]))
    for i in range(length, len(values)):
        out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return out


def _features(bars: np.ndarray) -> tuple[tuple[np.ndarray, ...], np.ndarray]:
    """Close SMA/EMA 20/60/120 and Pine ATR14; formula order matches V1."""
    high, low, close = bars[:, 2], bars[:, 3], bars[:, 4]
    close_series = pd.Series(close, copy=False)
    mas = tuple(close_series.rolling(period).mean().to_numpy(dtype=np.float64, copy=False)
                for period in _MA_PERIODS)
    mas += tuple(close_series.ewm(span=period, adjust=False).mean().to_numpy(
        dtype=np.float64, copy=False) for period in _MA_PERIODS)

    tr = high - low
    if len(close) > 1:
        tr[1:] = np.maximum(tr[1:], np.abs(high[1:] - close[:-1]))
        tr[1:] = np.maximum(tr[1:], np.abs(low[1:] - close[:-1]))
    atr = _pine_rma(tr, _ATR_LENGTH)
    return mas, atr


def _ma_rows(mas: tuple[np.ndarray, ...], indices: np.ndarray) -> np.ndarray:
    """Return close-MA rows with shape (bar, six), in Pine's V1 order."""
    return np.stack([line[indices] for line in mas], axis=-1)


def _pairwise_flips(flip_mas: np.ndarray) -> np.ndarray:
    """Count carried pair-order flips over (core start - 1) through core end.

    ``flip_mas`` has shape (time, candidate, six).  Zero differences carry
    the preceding non-zero ordering, as in both the Pine loop and Python V1
    reference.
    """
    count = np.zeros(flip_mas.shape[1], dtype=np.int16)
    for left in range(5):
        for right in range(left + 1, 6):
            previous = np.zeros(flip_mas.shape[1], dtype=np.int8)
            pair_count = np.zeros(flip_mas.shape[1], dtype=np.int8)
            for t in range(flip_mas.shape[0]):
                delta = flip_mas[t, :, left] - flip_mas[t, :, right]
                current = previous.copy()
                current[delta > 0.0] = 1
                current[delta < 0.0] = -1
                if t:
                    pair_count += ((current != previous) & (current != 0)
                                   & (previous != 0))
                previous = current
            count += pair_count
    return count


def _grade_block(
    bars: np.ndarray,
    mas: tuple[np.ndarray, ...],
    atr: np.ndarray,
    indices: np.ndarray,
    sign: float,
    core_length: int,
) -> np.ndarray:
    """Evaluate one fixed core length over a vector of confirmation indices."""
    n = len(indices)
    end_i = indices - 5
    start_i = indices - (core_length + 4)
    anchor_atr = atr[indices - 3]  # core end + 2 bars, as in V1/Pine.
    valid = np.isfinite(anchor_atr) & (anchor_atr > 0.0)
    safe_atr = np.where(valid, anchor_atr, 1.0)

    core_offsets = range(core_length)
    core_open = np.stack([bars[indices - (core_length + 4) + j, 1]
                          for j in core_offsets], axis=0)
    core_high = np.stack([bars[indices - (core_length + 4) + j, 2]
                          for j in core_offsets], axis=0)
    core_low = np.stack([bars[indices - (core_length + 4) + j, 3]
                         for j in core_offsets], axis=0)
    core_close = np.stack([bars[indices - (core_length + 4) + j, 4]
                           for j in core_offsets], axis=0)
    core_ma = np.stack([_ma_rows(mas, indices - (core_length + 4) + j)
                        for j in core_offsets], axis=0)
    core_ma_hi = core_ma.max(axis=2)
    core_ma_lo = core_ma.min(axis=2)

    env = (core_ma_hi.max(axis=0) - core_ma_lo.min(axis=0)) / safe_atr
    end_spread = (core_ma_hi[-1] - core_ma_lo[-1]) / safe_atr
    core_progress = sign * (core_close[-1] - core_close[0]) / safe_atr
    core_max_body = np.abs(core_close - core_open).max(axis=0) / safe_atr

    close_to_ma = np.abs(core_close[:, :, None] - core_ma).min(axis=(0, 2)) / safe_atr
    close_to_envelope = np.maximum(
        np.maximum(core_ma_lo - core_close, core_close - core_ma_hi), 0.0
    ).max(axis=0) / safe_atr
    body_high = np.maximum(core_open, core_close)
    body_low = np.minimum(core_open, core_close)
    body_to_envelope = np.maximum(
        np.maximum(core_ma_lo - body_high, body_low - core_ma_hi), 0.0
    ).max(axis=0) / safe_atr

    start_ma = core_ma[0]
    end_ma = core_ma[-1]
    slope_sum = np.zeros(n, dtype=np.float64)
    for column in range(6):
        slope_sum += (end_ma[:, column] - start_ma[:, column]) / safe_atr
    slope1 = sign * slope_sum / 6.0

    # Fifth release close is indices[i]; post offsets 1..5 are i-4..i.
    post_close = np.stack([bars[indices - 5 + j, 4] for j in range(6)], axis=0)
    post_progress = sign * (post_close - core_close[-1]) / safe_atr
    p1, p2, p3, p5 = (post_progress[1], post_progress[2],
                      post_progress[3], post_progress[5])

    stage1 = (valid & (env <= 1.5) & (end_spread <= 1.1)
              & (core_max_body <= 1.2)
              & (core_progress >= -0.6) & (core_progress <= 1.3)
              & (p1 >= 0.0) & (p2 >= 1.0) & (p3 >= 1.25) & (p5 >= 1.75)
              & (slope1 >= 0.03) & (close_to_ma <= 1.0)
              & (close_to_envelope <= 1.9) & (body_to_envelope <= 1.5))
    selected = np.flatnonzero(stage1)
    result = np.zeros(n, dtype=bool)
    if selected.size == 0:
        return result

    # The second pass is evaluated only for stage-1 survivors.  This keeps the
    # fixed 4/5-bar and 12-bar windows vectorized without per-candidate loops.
    idx = indices[selected]
    atr2 = safe_atr[selected]
    env2 = env[selected]
    spread2 = end_spread[selected]
    progress2 = core_progress[selected]
    body2 = core_max_body[selected]

    core_open2 = core_open[:, selected]
    core_high2 = core_high[:, selected]
    core_low2 = core_low[:, selected]
    core_close2 = core_close[:, selected]
    core_hi2 = core_ma_hi[:, selected]
    core_lo2 = core_ma_lo[:, selected]
    start_ma2 = start_ma[selected]
    end_ma2 = end_ma[selected]
    ma_mid_start = start_ma2.sum(axis=1) / 6.0
    ma_mid_end = end_ma2.sum(axis=1) / 6.0
    slope2 = sign * (ma_mid_end - ma_mid_start) / atr2 / max(core_length - 1, 1)

    start_width = core_hi2[0] - core_lo2[0]
    end_width = core_hi2[-1] - core_lo2[-1]
    ratio = end_width / np.maximum(start_width, 1.0e-12)
    decreases = (core_hi2[1:] - core_lo2[1:] < core_hi2[:-1] - core_lo2[:-1]).sum(axis=0)
    flip_mas = np.stack([_ma_rows(mas, idx - (core_length + 5) + j)
                         for j in range(core_length + 1)], axis=0)
    flips = _pairwise_flips(flip_mas)
    topology = ((ratio <= 0.9) & (decreases >= 2)) | ((ratio <= 1.15) & (flips >= 3))

    touch = ((core_high2 >= core_lo2) & (core_low2 <= core_hi2)).sum(axis=0) / core_length
    close_dist = np.maximum(
        np.maximum(core_lo2 - core_close2, core_close2 - core_hi2), 0.0
    ) / atr2
    close_q75 = np.quantile(close_dist, 0.75, axis=0, method="linear")
    wick = ((core_high2 - np.maximum(core_open2, core_close2))
            + (np.minimum(core_open2, core_close2) - core_low2)) / atr2
    wick_q90 = np.quantile(wick, 0.90, axis=0, method="linear")
    core_reverse = (sign * (core_close2 - core_open2) / atr2 < -0.20).sum(axis=0)

    # Twelve closed bars before the core; oldest-to-newest ordering matches V1.
    pre_start = idx - (core_length + 16)
    pre_open = np.stack([bars[pre_start + j, 1] for j in range(12)], axis=0)
    pre_close = np.stack([bars[pre_start + j, 4] for j in range(12)], axis=0)
    pre_body_q90 = np.quantile(np.abs(pre_close - pre_open) / atr2,
                               0.90, axis=0, method="linear")
    pre_path = np.abs(np.diff(pre_close, axis=0)).sum(axis=0) / atr2
    pre_last3 = sign * (bars[idx - (core_length + 5), 4]
                        - bars[idx - (core_length + 8), 4]) / atr2
    pre_favourable = np.maximum(
        0.0, (sign * (pre_close - pre_close[0]) / atr2).max(axis=0)
    )

    path_peak = np.maximum.accumulate(post_progress[:, selected], axis=0)
    post_retrace = (path_peak - post_progress[:, selected]).max(axis=0)
    post_min_progress = post_progress[:, selected].min(axis=0)
    positive_steps = (sign * np.diff(post_close[:, selected], axis=0) > 0.0).sum(axis=0)
    post_open = np.stack([bars[idx - 4 + j, 1] for j in range(5)], axis=0)
    post_close_bars = np.stack([bars[idx - 4 + j, 4] for j in range(5)], axis=0)
    post_body = sign * (post_close_bars - post_open) / atr2
    post_reverse = (post_body < -0.20).sum(axis=0)
    max_opposite_post = np.maximum(0.0, -post_body.min(axis=0))

    stage2 = ((spread2 <= 0.95) & (env2 <= 1.5)
              & (progress2 >= -0.6) & (progress2 <= 1.0)
              & (slope2 >= 0.02) & topology & (touch >= 0.4)
              & (close_q75 <= 1.5) & (pre_body_q90 <= 1.1)
              & (pre_path <= 5.5) & (pre_last3 <= 1.0)
              & (pre_favourable <= 3.0) & (wick_q90 <= 2.0)
              & (core_reverse <= 1) & (body2 <= 1.2)
              & (p1[selected] >= 0.0) & (p2[selected] >= 1.0)
              & (p3[selected] >= 1.25) & (p5[selected] >= 1.75)
              & (post_min_progress >= 0.0) & (positive_steps >= 3)
              & (post_retrace <= 0.75) & (post_reverse <= 1)
              & (max_opposite_post <= 0.8))
    result[selected] = stage2
    return result


def _raw_length(
    bars: np.ndarray,
    mas: tuple[np.ndarray, ...],
    atr: np.ndarray,
    start: int,
    stop: int,
    sign: float,
    core_length: int,
) -> np.ndarray:
    """Return raw (pre-cooldown) hits in [start, stop), in that local shape."""
    result = np.zeros(stop - start, dtype=bool)
    # SMA120 needs 120 closes at the oldest pre-core bar.  The offset guard
    # mirrors Pine's ``bar_index > winO + 1`` as well.
    first_ready = max(core_length + 18, 119 + core_length + 16)
    first = max(start, first_ready)
    for block_start in range(first, stop, _CHUNK):
        block_stop = min(block_start + _CHUNK, stop)
        indices = np.arange(block_start, block_stop, dtype=np.int64)
        result[block_start - start:block_stop - start] = _grade_block(
            bars, mas, atr, indices, sign, core_length
        )
    return result


def _direction_sign(direction: int | str) -> float:
    if direction in (1, "LONG", "long", "Long"):
        return 1.0
    if direction in (-1, "SHORT", "short", "Short"):
        return -1.0
    raise ValueError("direction must be +1/-1 or LONG/SHORT")


def grade_hits(bars: np.ndarray, direction: int | str, lo: int, hi: int) -> np.ndarray:
    """Return a bool array marking new V1 confirmation closes in ``[lo, hi)``.

    ``bars`` must be a continuous float64 ``[ms, open, high, low, close]``
    array.  The full-length result is false outside the requested interval.
    For same-direction duplicate control, V1 keeps the first signal and allows
    another only when its *core-end open time* is more than four hours later.
    At a range boundary, only the prior four-hour signal context is graded to
    seed that cooldown; no earlier hit is returned or backfilled.
    """
    sign = _direction_sign(direction)
    source = np.asarray(bars, dtype=np.float64)
    if source.ndim != 2 or source.shape[1] != 5:
        raise ValueError("bars must have shape (n, 5): [ms, open, high, low, close]")
    n = len(source)
    if not (0 <= lo <= hi <= n):
        raise ValueError("require 0 <= lo <= hi <= len(bars)")
    out = np.zeros(n, dtype=bool)
    if lo == hi or n < 140:
        return out

    # Every feature used at i is causal, so discard rows at/after hi up front.
    view = source[:hi]
    times = view[:, 0]
    if len(times) > 1:
        steps = np.diff(times)
        if steps[0] <= 0 or not np.all(steps == steps[0]):
            raise ValueError("bars must have strictly increasing, continuous timestamps")
    mas, atr = _features(view)

    # A prior accepted V1 signal can suppress an in-range hit only when its
    # core-end time falls within 240 minutes before the first in-range core.
    context_start = lo
    if lo >= 5:
        first_core_time = times[lo - 5]
        first_relevant_core = int(np.searchsorted(
            times[:lo - 5], first_core_time - _COOLDOWN_MS, side="left"
        ))
        context_start = min(lo, first_relevant_core + 5)

    def raw_window(start: int, stop: int) -> np.ndarray:
        return (_raw_length(view, mas, atr, start, stop, sign, 4)
                | _raw_length(view, mas, atr, start, stop, sign, 5))

    raw = raw_window(context_start, hi)

    # Cooldown is greedy, so an event more than four hours before ``lo`` can
    # still suppress a later raw event that would otherwise suppress an
    # in-range hit.  Walk backward through those chains until a full four-hour
    # raw-free interval proves that the earlier greedy state cannot matter.
    while context_start > 0 and lo > context_start:
        prior_positions = np.flatnonzero(raw[:lo - context_start])
        if prior_positions.size == 0:
            break
        earliest = context_start + int(prior_positions[0])
        earliest_core = earliest - 5
        cutoff = times[earliest_core] - _COOLDOWN_MS
        previous_start = int(np.searchsorted(
            times[:earliest_core], cutoff, side="left"
        )) + 5
        previous_start = max(0, min(context_start, previous_start))
        if previous_start >= context_start:
            break
        added = raw_window(previous_start, context_start)
        raw = np.concatenate((added, raw))
        context_start = previous_start
        if not added.any():
            break

    candidate_indices = np.flatnonzero(raw) + context_start
    if candidate_indices.size == 0:
        return out

    # Greedy V1 cooldown in core-end time. Searchsorted skips every raw event
    # inside the current four-hour cluster, so work scales with retained events.
    core_times = times[candidate_indices - 5]
    j = 0
    while j < len(candidate_indices):
        index = int(candidate_indices[j])
        if index >= lo:
            out[index] = True
        j = int(np.searchsorted(core_times, core_times[j] + _COOLDOWN_MS,
                                side="right"))
    return out

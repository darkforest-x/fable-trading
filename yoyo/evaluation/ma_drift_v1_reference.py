"""Read-only closed-bar replay of ``spike_ma_drift_short_v1.pine`` defaults.

The source frame must contain ``open_time, open, high, low, close``. Each row
is one completed bar. The caller supplies the chart's ``bar_minutes`` cadence;
``open_time`` resets the Pine-equivalent contiguous segment at a missing
interval. Cadence is never inferred from later rows.

Causal source windows: SMA/EMA 20/60/120 and Wilder ATR14 use current and
earlier closes/OHLC only. A compact run uses widths from offsets 1..12, each
divided by that bar's prior ATR. The six-bar drift uses current bars 0..5:
three-bar high/low means, close[5], six close-vs-rope observations, six TR/prior
ATR observations, and the current trailing six-bar low. Confirmation uses only
the warning's frozen trailing low and the current close/rope; no output reads a
future row. This is failure-attribution research, not an indicator, strategy,
or economic evaluation.
"""
from __future__ import annotations

from collections import deque

import numpy as np
import pandas as pd


ATR_LEN = 14
FAST_LEN, MID_LEN, SLOW_LEN = 20, 60, 120
BLOCK_LEN, DRIFT_LEN = 3, 6
COMPACT_LOOKBACK, COMPACT_BARS = 12, 3
WARMUP_BARS = max(SLOW_LEN + ATR_LEN, 3 * SLOW_LEN)
COMPACT_WIDTH_ATR, MAX_TR_ATR, MAX_GAP_ATR = 3.0, 1.8, 1.5
MAX_WAIT, COOLDOWN_BARS = 8, 12
REQUIRED_COLUMNS = ("open_time", "open", "high", "low", "close")


def _require_source(frame: pd.DataFrame) -> pd.DatetimeIndex:
    missing = set(REQUIRED_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(sorted(missing)))
    if not frame.columns.is_unique:
        raise ValueError("Source columns must be unique")
    times = pd.DatetimeIndex(pd.to_datetime(frame["open_time"], utc=True, errors="raise"))
    if not times.is_monotonic_increasing or times.has_duplicates:
        raise ValueError("open_time must be strictly increasing")
    return times


def _pine_sma(values: np.ndarray, length: int) -> np.ndarray:
    """Pine-style SMA for finite source values; missing values are ignored."""
    result = np.full(len(values), np.nan)
    window: deque[float] = deque()
    total = 0.0
    for i, value in enumerate(values):
        if np.isfinite(value):
            if len(window) == length:
                total -= window.popleft()
            value = float(value)
            window.append(value)
            total += value
        if len(window) == length:
            result[i] = total / length
    return result


def _pine_ema(values: np.ndarray, length: int) -> np.ndarray:
    """Pine EMA recurrence with its first finite source value as the seed."""
    result = np.full(len(values), np.nan)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for i, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[i] = previous
    return result


def _pine_atr(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return Pine ``ta.tr(true)`` and ``ta.atr(14)`` / Wilder RMA values."""
    tr = np.full(len(close), np.nan)
    atr = np.full(len(close), np.nan)
    seed: deque[float] = deque(maxlen=ATR_LEN)
    previous_close = np.nan
    previous_atr = np.nan
    for i, (open_value, high_value, low_value, close_value) in enumerate(zip(open_, high, low, close)):
        valid = all(np.isfinite(v) for v in (open_value, high_value, low_value, close_value))
        if valid:
            current = float(high_value - low_value) if not np.isfinite(previous_close) else max(
                float(high_value - low_value), abs(float(high_value - previous_close)), abs(float(low_value - previous_close)))
            tr[i] = current
            seed.append(current)
            if not np.isfinite(previous_atr) and len(seed) == ATR_LEN:
                previous_atr = float(np.mean(seed))
            elif np.isfinite(previous_atr):
                previous_atr = (previous_atr * (ATR_LEN - 1) + current) / ATR_LEN
            atr[i] = previous_atr
        else:
            atr[i] = previous_atr
        previous_close = float(close_value) if np.isfinite(close_value) else np.nan
    return tr, atr


def _rolling_mean(values: np.ndarray, length: int) -> np.ndarray:
    return pd.Series(values).rolling(length, min_periods=length).mean().to_numpy()


def _rolling_lowest(values: np.ndarray, length: int) -> np.ndarray:
    return pd.Series(values).rolling(length, min_periods=length).min().to_numpy()


def _rolling_highest(values: np.ndarray, length: int) -> np.ndarray:
    return pd.Series(values).rolling(length, min_periods=length).max().to_numpy()


def _step(
    pending: bool,
    warn_bar: int | None,
    frozen_low: float,
    last_end: int | None,
    valid: bool,
    setup: bool,
    reclaim: bool,
    breakout: bool,
    setup_low: float,
    bar: int,
) -> tuple[bool, int | None, float, int | None, bool, bool, bool, bool]:
    """Mirror V1's pure Pine f_step, including its terminal precedence."""
    warning = confirmation = cancel = expire = False
    if not valid:
        return False, None, np.nan, last_end, warning, confirmation, cancel, expire
    if pending:
        assert warn_bar is not None
        age = bar - warn_bar
        if age > MAX_WAIT:
            return False, None, np.nan, bar, warning, confirmation, cancel, True
        if reclaim:
            return False, None, np.nan, bar, warning, confirmation, True, expire
        if bar > warn_bar and breakout:
            return False, None, np.nan, bar, warning, True, cancel, expire
        return pending, warn_bar, frozen_low, last_end, warning, confirmation, cancel, expire
    cooldown_done = last_end is None or bar - last_end > COOLDOWN_BARS
    if setup and cooldown_done:
        return True, bar, setup_low, last_end, True, confirmation, cancel, expire
    return pending, warn_bar, frozen_low, last_end, warning, confirmation, cancel, expire


def replay(frame: pd.DataFrame, *, bar_minutes: int = 1) -> pd.DataFrame:
    """Replay the default V1 short morphology without mutating ``frame``.

    ``bar_minutes`` is the fixed chart interval and must be positive. Returned
    columns preserve the source plus named values and boolean gates.
    ``pending`` and ``frozen_low`` are post-close state; ``breakout`` is tested
    against the prior pending state's frozen low, exactly as the Pine call is.
    """
    if not isinstance(bar_minutes, int) or isinstance(bar_minutes, bool) or bar_minutes <= 0:
        raise ValueError("bar_minutes must be a positive integer")
    times = _require_source(frame)
    result = frame.copy(deep=True)
    n = len(result)
    for name in ("open", "high", "low", "close"):
        result[name] = pd.to_numeric(result[name], errors="coerce")
    open_, high, low, close = (result[name].to_numpy(dtype=float) for name in ("open", "high", "low", "close"))
    cadence = pd.Timedelta(minutes=bar_minutes)

    s20, e20 = _pine_sma(close, FAST_LEN), _pine_ema(close, FAST_LEN)
    s60, e60 = _pine_sma(close, MID_LEN), _pine_ema(close, MID_LEN)
    s120, e120 = _pine_sma(close, SLOW_LEN), _pine_ema(close, SLOW_LEN)
    rope_high = np.nanmax(np.column_stack((s20, e20, s60, e60, s120, e120)), axis=1)
    rope_low = np.nanmin(np.column_stack((s20, e20, s60, e60, s120, e120)), axis=1)
    rope_high[np.isnan(s120)] = np.nan
    rope_low[np.isnan(s120)] = np.nan
    tr, atr = _pine_atr(open_, high, low, close)
    atr_prior = np.r_[np.nan, atr[:-1]]
    rope_width_prior_atr = np.where(atr_prior > 0, (rope_high - rope_low) / atr_prior, np.nan)
    tr_prior_atr = np.where(atr_prior > 0, tr / atr_prior, np.nan)
    drift_low = _rolling_lowest(low, DRIFT_LEN)
    max_drift_tr = _rolling_highest(tr_prior_atr, DRIFT_LEN)
    high_now, low_now = _rolling_mean(high, BLOCK_LEN), _rolling_mean(low, BLOCK_LEN)
    high_prior = np.r_[np.full(BLOCK_LEN, np.nan), high_now[:-BLOCK_LEN]]
    low_prior = np.r_[np.full(BLOCK_LEN, np.nan), low_now[:-BLOCK_LEN]]

    prices_valid = np.isfinite(open_) & np.isfinite(high) & np.isfinite(low) & np.isfinite(close) & (low > 0) & (high >= low) & (high >= np.maximum(open_, close)) & (low <= np.minimum(open_, close))
    data_gap = np.zeros(n, dtype=bool)
    if n > 1:
        expected = np.diff(times.asi8) == cadence.value
        data_gap[1:] = ~prices_valid[1:] | ~prices_valid[:-1] | ~expected
    segment_bars = np.zeros(n, dtype=int)
    for i in range(n):
        segment_bars[i] = 0 if not prices_valid[i] else 1 if data_gap[i] else (segment_bars[i - 1] + 1 if i else 1)
    ready = prices_valid & ~data_gap & (segment_bars >= WARMUP_BARS) & np.isfinite(atr_prior) & (atr_prior > 0)

    recent_compact = np.zeros(n, dtype=bool)
    for i in range(n):
        for offset in range(1, COMPACT_LOOKBACK - COMPACT_BARS + 2):
            positions = (i - offset, i - offset - 1, i - offset - 2)
            if min(positions) >= 0 and np.all(rope_width_prior_atr[list(positions)] <= COMPACT_WIDTH_ATR):
                recent_compact[i] = True
                break
    fast_down = (s20 - np.r_[np.full(BLOCK_LEN, np.nan), s20[:-BLOCK_LEN]] < 0) & (e20 - np.r_[np.full(BLOCK_LEN, np.nan), e20[:-BLOCK_LEN]] < 0)
    descending_blocks = (high_now < high_prior) & (low_now < low_prior)
    close_prior_five = np.r_[np.full(DRIFT_LEN - 1, np.nan), close[: -(DRIFT_LEN - 1)]]
    net_close_down = close < close_prior_five
    under_rope = np.where(np.isfinite(rope_low), (close < rope_low).astype(float), np.nan)
    under_rope_count = pd.Series(under_rope).rolling(DRIFT_LEN, min_periods=DRIFT_LEN).sum().to_numpy()
    mostly_under_rope = under_rope_count >= np.ceil(DRIFT_LEN * 0.60)
    contained_drift = max_drift_tr <= MAX_TR_ATR
    close_under_rope = close < rope_low
    rope_gap_atr = np.where(atr_prior > 0, (rope_low - close) / atr_prior, np.nan)
    close_near_rope = (rope_gap_atr >= 0) & (rope_gap_atr <= MAX_GAP_ATR)
    setup = ready & recent_compact & fast_down & descending_blocks & net_close_down & mostly_under_rope & contained_drift & close_under_rope & close_near_rope
    reclaim = close >= rope_low

    pending = np.zeros(n, dtype=bool)
    frozen_low = np.full(n, np.nan)
    warning = np.zeros(n, dtype=bool)
    confirmation = np.zeros(n, dtype=bool)
    cancel = np.zeros(n, dtype=bool)
    expire = np.zeros(n, dtype=bool)
    breakout = np.zeros(n, dtype=bool)
    warn_bar_state = np.full(n, np.nan)
    last_end_state = np.full(n, np.nan)
    warn_bar: int | None = None
    state_pending, state_frozen, last_end = False, np.nan, None
    for i in range(n):
        breakout[i] = bool(np.isfinite(state_frozen) and close[i] < state_frozen and close[i] < rope_low[i])
        state_pending, warn_bar, state_frozen, last_end, warning[i], confirmation[i], cancel[i], expire[i] = _step(
            state_pending, warn_bar, state_frozen, last_end, bool(ready[i]), bool(setup[i]), bool(reclaim[i]), bool(breakout[i]), float(drift_low[i]), i)
        pending[i] = state_pending
        frozen_low[i] = state_frozen
        warn_bar_state[i] = np.nan if warn_bar is None else warn_bar
        last_end_state[i] = np.nan if last_end is None else last_end

    result["s20"], result["e20"], result["s60"], result["e60"], result["s120"], result["e120"] = s20, e20, s60, e60, s120, e120
    result["rope_high"], result["rope_low"], result["tr"], result["atr"] = rope_high, rope_low, tr, atr
    result["rope_width_prior_atr"], result["tr_prior_atr"], result["drift_low"] = rope_width_prior_atr, tr_prior_atr, drift_low
    result["price_valid"], result["data_gap"], result["segment_bars"], result["ready"] = prices_valid, data_gap, segment_bars, ready
    result["recent_compact"], result["fast_down"], result["descending_blocks"] = recent_compact, fast_down, descending_blocks
    result["net_close_down"], result["mostly_under_rope"], result["contained_drift"] = net_close_down, mostly_under_rope, contained_drift
    result["close_under_rope"], result["close_near_rope"], result["setup"] = close_under_rope, close_near_rope, setup
    result["reclaim"], result["breakout"], result["pending"], result["frozen_low"] = reclaim, breakout, pending, frozen_low
    result["warn_bar"], result["last_end"] = warn_bar_state, last_end_state
    result["warning"], result["confirmation"], result["cancel"], result["expire"] = warning, confirmation, cancel, expire
    result["open_time"] = times
    return result

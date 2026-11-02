"""Causal H1 features and outside-Monday-1.618 Fanshen signal candidates.

Input is validated five-minute OHLCV with a UTC bar-open index.  Only complete
UTC hours made from exactly twelve aligned five-minute bars are retained.  The
returned index is each H1 bar's UTC open; ``close_at`` is when that bar and its
features become available, and an executable study should schedule entry after
that close (for example, at the first subsequent five-minute open).

The Stoch arrows reuse ``spike_fanshen_exit.compute_signals``: K is SMA3 of
5-bar stochastic, D is SMA3 of K, and crossings require strict K/D <20 for a
long or >80 for a short.  The Monday range is the high/low of a complete
New-York calendar Monday (288 five-minute bars from local 00:00 inclusive to
Tuesday 00:00 exclusive).  It is exposed only once that local Tuesday boundary
has closed; the outside signal uses the H1 wick, strictly below/above the
outer 1.618 level.

Feature windows use only the current closed H1 bar and earlier bars in its
continuous segment: ATR14 is a simple mean of 14 true ranges, RSI14 is Wilder
RSI, volume ratio divides current volume by the preceding 20-hour mean, and
Bollinger re-entry uses 20 closes with population standard deviation and 2σ.
RSI divergence compares the current low/high with the preceding 20 bars and
the RSI at the most recent matching extreme.  Four-hour trend uses only fully
closed, complete 4-hour candles whose close time is no later than ``close_at``;
EMA20 is updated from those completed closes only.  ``eligible`` additionally
requires a complete Monday range, a New-York Tuesday-Friday bar open, and 50
consecutive complete H1 bars through the current bar to avoid gap warmup.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_fanshen_exit import compute_signals


_NY = "America/New_York"
_FIVE_MINUTES = pd.Timedelta(minutes=5)
_ONE_HOUR = pd.Timedelta(hours=1)
_FOUR_HOURS = pd.Timedelta(hours=4)
_BARS_PER_HOUR = 12
_BARS_PER_MONDAY = 288


def _validate_5m(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns):
        raise ValueError("frame must contain open, high, low, close, and volume")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("frame index must be timezone-aware UTC bar-open times")
    if str(frame.index.tz) != "UTC":
        raise ValueError("frame index must be UTC")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("frame index must be unique and chronological")
    if not frame.index.equals(frame.index.floor("5min")):
        raise ValueError("frame index must lie on the five-minute UTC grid")

    bars = frame.loc[:, ["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="coerce"
    ).astype(float)
    values = bars.to_numpy()
    if not np.isfinite(values).all():
        raise ValueError("OHLCV values must be finite")
    if (bars.low > bars.high).any():
        raise ValueError("low cannot exceed high")
    if (bars.volume < 0).any():
        raise ValueError("volume cannot be negative")
    return bars


def _aggregate_h1(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                             index=pd.DatetimeIndex([], tz="UTC", name=bars.index.name))
        empty["close_at"] = pd.Series(index=empty.index, dtype="datetime64[ns, UTC]")
        return empty
    hour = bars.index.floor("1h")
    grouped = bars.groupby(hour, sort=True)
    out = grouped.agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"), count=("close", "size"),
    )
    # Input bars are on the UTC 5m grid, so twelve members is equivalent to
    # the exact [hour, hour+1h) grid with no duplicate or off-grid timestamp.
    out = out.loc[out["count"].eq(_BARS_PER_HOUR), ["open", "high", "low", "close", "volume"]].copy()
    out.index.name = bars.index.name
    out["close_at"] = out.index + _ONE_HOUR
    return out


def _week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _complete_monday_ranges(bars: pd.DataFrame) -> dict[date, tuple[float, float, pd.Timestamp]]:
    """Return only Monday ranges backed by the exact complete NY 5m grid."""
    if bars.empty:
        return {}
    local = bars.index.tz_convert(_NY)
    ranges: dict[date, tuple[float, float, pd.Timestamp]] = {}
    monday_positions = np.flatnonzero(np.asarray(local.dayofweek) == 0)
    if not len(monday_positions):
        return ranges
    monday_days = np.asarray([local[position].date() for position in monday_positions], dtype=object)
    by_monday = pd.Series(monday_positions).groupby(monday_days, sort=True)
    for monday, positions in by_monday:
        local_start = pd.Timestamp(monday).tz_localize(_NY)
        local_end = pd.Timestamp(monday + timedelta(days=1)).tz_localize(_NY)
        start = local_start.tz_convert("UTC")
        end = local_end.tz_convert("UTC")
        expected = pd.date_range(start, end, freq=_FIVE_MINUTES, inclusive="left")
        if len(expected) != _BARS_PER_MONDAY:
            continue
        positions = positions.to_numpy(dtype=int)
        monday_index = bars.index.take(positions)
        if len(positions) != _BARS_PER_MONDAY or not monday_index.equals(expected):
            continue
        monday_bars = bars.iloc[positions]
        ranges[monday] = (float(monday_bars.high.max()), float(monday_bars.low.min()), end)
    return ranges


def _wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI with an SMA seed from the first ``period`` close changes."""
    values = close.to_numpy(dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return pd.Series(result, index=close.index, name="rsi14")
    delta = np.diff(values)
    gain = np.maximum(delta, 0.0)
    loss = np.maximum(-delta, 0.0)
    avg_gain = float(gain[:period].mean())
    avg_loss = float(loss[:period].mean())

    def rsi_value(up: float, down: float) -> float:
        if up == 0.0 and down == 0.0:
            return np.nan
        if down == 0.0:
            return 100.0
        if up == 0.0:
            return 0.0
        relative_strength = up / down
        return 100.0 - 100.0 / (1.0 + relative_strength)

    result[period] = rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = ((period - 1) * avg_gain + gain[i - 1]) / period
        avg_loss = ((period - 1) * avg_loss + loss[i - 1]) / period
        result[i] = rsi_value(avg_gain, avg_loss)
    return pd.Series(result, index=close.index, name="rsi14")


def _rsi_divergences(seg: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Compare current extremes/RSI with the latest equal extreme in prior20 bars."""
    n = len(seg)
    long = np.zeros(n, dtype=bool)
    short = np.zeros(n, dtype=bool)
    if n <= 20:
        return (pd.Series(long, index=seg.index), pd.Series(short, index=seg.index))

    lows = seg.low.to_numpy(dtype=float)
    highs = seg.high.to_numpy(dtype=float)
    rsi = seg.rsi14.to_numpy(dtype=float)
    low_windows = np.lib.stride_tricks.sliding_window_view(lows[:-1], 20)
    high_windows = np.lib.stride_tricks.sliding_window_view(highs[:-1], 20)
    low_min = low_windows.min(axis=1)
    high_max = high_windows.max(axis=1)
    # Use the most recent bar when an extreme is tied.
    low_latest_offset = 19 - np.argmax(low_windows[:, ::-1] == low_min[:, None], axis=1)
    high_latest_offset = 19 - np.argmax(high_windows[:, ::-1] == high_max[:, None], axis=1)
    starts = np.arange(len(low_windows))
    current = starts + 20
    prior_low_i = starts + low_latest_offset
    prior_high_i = starts + high_latest_offset
    long[current] = ((lows[current] < low_min) & np.isfinite(rsi[current]) &
                     np.isfinite(rsi[prior_low_i]) & (rsi[current] > rsi[prior_low_i]))
    short[current] = ((highs[current] > high_max) & np.isfinite(rsi[current]) &
                      np.isfinite(rsi[prior_high_i]) & (rsi[current] < rsi[prior_high_i]))
    return pd.Series(long, index=seg.index), pd.Series(short, index=seg.index)


def _completed_4h_trend(seg: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Map complete 4h close/EMA20 states to H1 close times without lookahead."""
    index = seg.index
    long = np.zeros(len(seg), dtype=bool)
    short = np.zeros(len(seg), dtype=bool)
    h4 = seg.groupby(index.floor("4h"), sort=True).agg(
        close=("close", "last"), count=("close", "size"),
    )
    # The segment is already a continuous H1 grid. A four-row UTC bucket is
    # therefore exactly the full [bucket, bucket+4h) candle.
    h4 = h4.loc[h4["count"].eq(4), ["close"]]
    if h4.empty:
        return pd.Series(long, index=index), pd.Series(short, index=index)
    available_at = h4.index + _FOUR_HOURS
    closes = h4.close.to_numpy(dtype=float)
    ema = h4.close.ewm(span=20, adjust=False, min_periods=1).mean().to_numpy()
    h1_close_times = index + _ONE_HOUR
    positions = available_at.searchsorted(h1_close_times, side="right") - 1
    known = positions >= 0
    if known.any():
        close_values = closes[positions[known]]
        ema_values = ema[positions[known]]
        long[known] = close_values > ema_values
        short[known] = close_values < ema_values
    return pd.Series(long, index=index), pd.Series(short, index=index)


def _add_segment_features(seg: pd.DataFrame) -> pd.DataFrame:
    out = seg.copy()
    signals = compute_signals(out[["high", "low", "close"]])
    out["arrow_long"] = signals.arrow_long.to_numpy(dtype=bool)
    out["arrow_short"] = signals.arrow_short.to_numpy(dtype=bool)

    previous_close = out.close.shift(1)
    true_range = pd.concat(
        [out.high - out.low, (out.high - previous_close).abs(), (out.low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    out["atr14"] = true_range.rolling(14, min_periods=14).mean()
    out["rsi14"] = _wilder_rsi(out.close)
    prior_volume_mean = out.volume.shift(1).rolling(20, min_periods=20).mean()
    out["volume_ratio"] = out.volume.div(prior_volume_mean.where(prior_volume_mean.ne(0)))

    bb_mid = out.close.rolling(20, min_periods=20).mean()
    bb_std = out.close.rolling(20, min_periods=20).std(ddof=0)
    bb_upper, bb_lower = bb_mid + 2.0 * bb_std, bb_mid - 2.0 * bb_std
    out["bb_reentry_long"] = (out.close.shift(1) < bb_lower.shift(1)) & (out.close > bb_lower)
    out["bb_reentry_short"] = (out.close.shift(1) > bb_upper.shift(1)) & (out.close < bb_upper)
    out["bb_reentry_long"] = out.bb_reentry_long.fillna(False).astype(bool)
    out["bb_reentry_short"] = out.bb_reentry_short.fillna(False).astype(bool)

    out["trend4h_long"], out["trend4h_short"] = _completed_4h_trend(out)
    out["rsi_div_long"], out["rsi_div_short"] = _rsi_divergences(out)
    return out


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Build causal H1 bars, weekly levels, arrow signals, and confluence features.

    ``frame`` must contain finite ``open/high/low/close/volume`` values indexed
    by unique, increasing UTC five-minute bar opens aligned to the five-minute
    grid.  Incomplete hours are dropped.  The output H1 index is the bar open;
    ``close_at = index + 1 hour`` is its signal/feature availability time.
    ``signal_long``/``signal_short`` are strict outside-wick arrow candidates,
    with no first-bars or future-outcome filter.
    """
    bars = _validate_5m(frame)
    h1 = _aggregate_h1(bars)
    columns = [
        "monday_high", "monday_low", "upper", "lower", "week", "arrow_long",
        "arrow_short", "eligible", "atr14", "rsi14", "volume_ratio",
        "bb_reentry_long", "bb_reentry_short", "trend4h_long", "trend4h_short",
        "rsi_div_long", "rsi_div_short", "signal_long", "signal_short",
    ]
    for column in columns:
        if column not in h1:
            h1[column] = pd.Series(index=h1.index, dtype=object if column == "week" else float)
    if h1.empty:
        for column in ("arrow_long", "arrow_short", "eligible", "bb_reentry_long", "bb_reentry_short",
                       "trend4h_long", "trend4h_short", "rsi_div_long", "rsi_div_short", "signal_long", "signal_short"):
            h1[column] = h1[column].astype(bool)
        h1["week"] = h1["week"].astype("string")
        return h1

    completed_ranges = _complete_monday_ranges(bars)
    local = h1.index.tz_convert(_NY)
    week_dates = [_week_monday(stamp.date()) for stamp in local]
    h1["week"] = [day.isoformat() for day in week_dates]
    range_table = pd.DataFrame.from_dict(
        {monday: (values[0], values[1], values[2]) for monday, values in completed_ranges.items()},
        orient="index", columns=["monday_high", "monday_low", "monday_end"],
    )
    if range_table.empty:
        h1["monday_high"] = np.nan
        h1["monday_low"] = np.nan
        h1["upper"] = np.nan
        h1["lower"] = np.nan
    else:
        range_table.index = pd.Index(range_table.index, dtype=object)
        range_table["upper"] = range_table.monday_low + 1.618 * (range_table.monday_high - range_table.monday_low)
        range_table["lower"] = range_table.monday_high - 1.618 * (range_table.monday_high - range_table.monday_low)
        mapped = range_table.reindex(pd.Index(week_dates, dtype=object))
        ready = h1.close_at.to_numpy() >= mapped.monday_end.to_numpy()
        for name in ("monday_high", "monday_low", "upper", "lower"):
            h1[name] = mapped[name].where(ready).to_numpy(dtype=float)

    # Reset rolling windows and all recursive indicators at every missing H1.
    contiguous = h1.index.to_series().diff().eq(_ONE_HOUR)
    segment_id = (~contiguous).cumsum().to_numpy()
    h1["eligible"] = False
    h1["arrow_long"] = False
    h1["arrow_short"] = False
    for name in ("bb_reentry_long", "bb_reentry_short", "trend4h_long", "trend4h_short",
                 "rsi_div_long", "rsi_div_short"):
        h1[name] = False
    h1["atr14"] = np.nan
    h1["rsi14"] = np.nan
    h1["volume_ratio"] = np.nan
    for _, positions in pd.Series(np.arange(len(h1)), index=h1.index).groupby(segment_id, sort=False):
        locs = positions.to_numpy(dtype=int)
        seg = _add_segment_features(h1.iloc[locs])
        for name in ("arrow_long", "arrow_short", "atr14", "rsi14", "volume_ratio",
                     "bb_reentry_long", "bb_reentry_short", "trend4h_long", "trend4h_short",
                     "rsi_div_long", "rsi_div_short"):
            h1.iloc[locs, h1.columns.get_loc(name)] = seg[name].to_numpy()
        run_length = np.arange(1, len(locs) + 1)
        h1.iloc[locs, h1.columns.get_loc("eligible")] = run_length >= 50

    weekdays = np.asarray(local.dayofweek)
    has_range = h1.monday_high.notna().to_numpy() & h1.monday_low.notna().to_numpy()
    h1["eligible"] = (h1.eligible.to_numpy(dtype=bool) & has_range &
                       np.isin(weekdays, [1, 2, 3, 4]))
    h1["signal_long"] = (h1.eligible & h1.arrow_long & (h1.low < h1.lower)).fillna(False).astype(bool)
    h1["signal_short"] = (h1.eligible & h1.arrow_short & (h1.high > h1.upper)).fillna(False).astype(bool)

    for column in ("arrow_long", "arrow_short", "eligible", "bb_reentry_long", "bb_reentry_short",
                   "trend4h_long", "trend4h_short", "rsi_div_long", "rsi_div_short", "signal_long", "signal_short"):
        h1[column] = h1[column].fillna(False).astype(bool)
    h1["week"] = h1["week"].astype("string")
    return h1

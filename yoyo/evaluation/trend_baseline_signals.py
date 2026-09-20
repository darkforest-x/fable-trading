"""Causal chart-bar trend baseline events for public research.

The two Donchian-style breakout families are independently adapted from the
published Original Turtle rules, https://www.tradingblox.com/originalturtles/originalturtlerules.htm.
The SMA crossover family is a simple trend-following comparison in the spirit
of Ed Seykota's trend-following material,
https://www.seykota.com/tribe/TSP/EA/Exponential/index.htm.  These are
deliberately fixed chart-*bar* rules: ``20`` and ``55`` mean bars rather than
the original Turtle day settings, and ``sma20_60`` is not a parameter search.

``signal_families`` reads ``open``, ``high``, ``low``, and ``close`` only.
At bar ``t``, Donchian channels read prior highs/lows in ``[t-N, t-1]`` and
the SMA event reads closes through ``t`` plus the prior bar's completed SMAs.
It never fills missing values or reads later rows.  The input must be a UTC,
strictly increasing, unique ``DatetimeIndex`` aligned to ``minutes``; a gap or
an invalid OHLC bar starts a new segment.  All outputs share a 61-consecutive-
valid-bar warmup (including the current bar), so the families have the same
admission history.  Valid prices are finite and positive, with ``high`` no
lower than open/close/low and ``low`` no higher than open/close/high.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


_OHLC_COLUMNS = ("open", "high", "low", "close")
_COMMON_WARMUP_BARS = 61


def _validate_clock(frame: pd.DataFrame, minutes: int) -> pd.Timedelta:
    """Validate the fixed chart-bar clock while permitting whole-bar gaps."""

    if isinstance(minutes, bool) or not isinstance(minutes, (int, np.integer)) or minutes <= 0:
        raise ValueError("minutes must be a positive integer")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("frame requires a timezone-aware UTC DatetimeIndex")
    if str(frame.index.tz) != "UTC":
        raise ValueError("frame index timezone must be UTC")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("frame timestamps must be unique and increasing")

    bar = pd.Timedelta(minutes=int(minutes))
    timestamps = frame.index.as_unit("ns").asi8
    if len(timestamps) and (timestamps % bar.value != 0).any():
        raise ValueError("frame timestamps must align to the minutes clock")
    if len(timestamps) > 1:
        differences = np.diff(timestamps)
        if (differences % bar.value != 0).any():
            raise ValueError("frame timestamp differences must be whole chart bars")
    return bar


def _valid_ohlc(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Coerce OHLC for calculation and mark rows that meet the price contract."""

    missing = sorted(set(_OHLC_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"missing OHLC columns: {missing}")
    ohlc = frame.loc[:, _OHLC_COLUMNS].apply(pd.to_numeric, errors="coerce").astype(float)
    values = ohlc.to_numpy()
    finite_positive = np.isfinite(values).all(axis=1) & (values > 0.0).all(axis=1)
    open_, high, low, close = (ohlc[name] for name in _OHLC_COLUMNS)
    range_valid = (
        high.ge(open_)
        & high.ge(close)
        & high.ge(low)
        & low.le(open_)
        & low.le(close)
        & low.le(high)
    )
    return ohlc, pd.Series(finite_positive, index=frame.index) & range_valid


def _segments(index: pd.DatetimeIndex, valid: pd.Series, bar: pd.Timedelta) -> pd.Series:
    """Assign a fresh segment after a gap or invalid row, without imputation."""

    starts = np.ones(len(index), dtype=bool)
    if len(index) > 1:
        contiguous = np.diff(index.as_unit("ns").asi8) == bar.value
        starts[1:] = (~contiguous) | (~valid.to_numpy()[:-1])
    starts |= ~valid.to_numpy()
    return pd.Series(np.cumsum(starts), index=index)


def _rolling_by_segment(values: pd.Series, segments: pd.Series, window: int, *, prior: bool = False,
                        maximum: bool = False, minimum: bool = False) -> pd.Series:
    """Calculate one causal rolling statistic independently in each segment."""

    def calculate(group: pd.Series) -> pd.Series:
        source = group.shift(1) if prior else group
        rolling = source.rolling(window, min_periods=window)
        if maximum:
            return rolling.max()
        if minimum:
            return rolling.min()
        return rolling.mean()

    return values.groupby(segments, group_keys=False).transform(calculate)


def signal_families(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Return fixed causal Donchian and SMA crossover event families.

    Output columns ``dc20``, ``dc55``, and ``sma20_60`` use ``int8`` values in
    ``{-1, 0, 1}``; ``common_ready`` is true only after 61 consecutive valid
    chart bars.  Donchian events require the current close to be *strictly*
    beyond the previous N highs (long) or lows (short), and can occur on every
    qualifying bar.  SMA events occur only on a strict 20/60 cross, with
    equality producing no event.  Signals before common readiness are zero.
    """

    bar = _validate_clock(frame, minutes)
    ohlc, valid = _valid_ohlc(frame)
    segments = _segments(frame.index, valid, bar)
    close = ohlc["close"]

    # Every invalid row starts its own segment, so group-local row counts are
    # exactly the consecutive valid-bar history available at each decision.
    segment_positions = pd.Series(1, index=frame.index).groupby(segments).cumsum()
    common_ready = valid & segment_positions.ge(_COMMON_WARMUP_BARS)

    dc_signals: dict[str, pd.Series] = {}
    for window in (20, 55):
        prior_high = _rolling_by_segment(ohlc["high"], segments, window, prior=True, maximum=True)
        prior_low = _rolling_by_segment(ohlc["low"], segments, window, prior=True, minimum=True)
        dc_signals[f"dc{window}"] = (close.gt(prior_high).astype(np.int8) - close.lt(prior_low).astype(np.int8))

    fast = _rolling_by_segment(close, segments, 20)
    slow = _rolling_by_segment(close, segments, 60)
    previous_fast = fast.groupby(segments, group_keys=False).shift(1)
    previous_slow = slow.groupby(segments, group_keys=False).shift(1)
    crossed_up = fast.gt(slow) & previous_fast.le(previous_slow)
    crossed_down = fast.lt(slow) & previous_fast.ge(previous_slow)
    sma_signal = crossed_up.astype(np.int8) - crossed_down.astype(np.int8)

    out = pd.DataFrame(index=frame.index)
    for name, signal in dc_signals.items():
        out[name] = signal.where(common_ready, 0).astype(np.int8)
    out["sma20_60"] = sma_signal.where(common_ready, 0).astype(np.int8)
    out["common_ready"] = common_ready.astype(bool)
    return out

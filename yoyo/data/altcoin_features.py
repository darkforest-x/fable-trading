"""Pure, parameterized IMACD and causal altcoin context; no market IO.

Source contract: Pine ``imacd_dense_mtf_v2_6.pine``, monitor ``_compute`` and
``imacd_startup_quality.build_features``. Defaults reproduce the monitor's
34/9 and visible-focus state; importing or calling this module never mutates
those modules. Recurrences start at the supplied history origin. Callers must
supply confirmed, ordinary, continuous UTC OHLCV with open-time stamps. This
pure function cannot establish exchange confirmation from a wall clock; an
optional ``confirm`` column, when supplied, must contain confirmed values.

At row t, IMACD/ATR14/SMA and EMA20/60/120 use OHLC through t. Formation uses
the preceding 12 bandwidths and 15 pair-cross counts, with 34-bar memory.
Focus qualification uses max(abs(md), abs(sb)) <= band * ATR[t-1], then freezes
the band. The release range covers the entire preceding near-zero segment,
including its pre-qualification bars but excluding the release candle. Only
qualified rows with index >= max(340, 10 * length_ma) can emit an event.

Additional context, available at the current candle CLOSE:
* relative_volume = volume[t] / median(volume[t-20:t]); units remain the input
  exchange's volume units. This is within-symbol relative activity, not USD.
* tr_expansion = TR[t] / ATR[t-1]; body_fraction = abs(close-open) / TR[t];
  close_location = (close-low)/(high-low); momentum3_atr = (c[t]-c[t-3])/ATR[t-1].
* bb_width20 = 4 * population_std(close[t-19:t+1]) / mean(close[t-19:t+1]).
  bb_width_rank_prior240 ranks width[t-1] among widths[t-240:t], using average
  tie ranks divided by 240. Both its value and its reference window exclude t.
* atr_pct = ATR[t]/close[t]. prior7d_atr_pct_mean averages the preceding exact
  seven-day interval (excluding t); prior7d_return = c[t-1]/c[t-1-seven_days]-1.
  Both require complete history. Bar duration must divide seven days exactly.

No future values, fill-forward, fitting, labels, model, HTTP, storage, or live
service state are used. Zero denominators and unavailable windows yield NaN.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from numbers import Integral, Real

import numpy as np
import pandas as pd


MA_COLUMNS = ("sma20", "ema20", "sma60", "ema60", "sma120", "ema120")
BAR_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class IMACDParams:
    """Explicit research inputs, constrained to the existing Pine input bounds."""

    length_ma: int = 34
    length_signal: int = 9
    focus_min_bars: int = 12
    focus_atr_band: float = 0.10

    def __post_init__(self) -> None:
        for name, lower, upper in (("length_ma", 2, 200), ("length_signal", 1, 100),
                                   ("focus_min_bars", 2, 200)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral) or not lower <= value <= upper:
                raise ValueError(f"{name} must be an integer from {lower} to {upper}")
        band = self.focus_atr_band
        if isinstance(band, bool) or not isinstance(band, Real) or not np.isfinite(band) or not 0 <= band <= 1:
            raise ValueError("focus_atr_band must be finite and between 0 and 1")


def _validate(bars: pd.DataFrame) -> tuple[np.ndarray, int]:
    """Validate confirmed UTC open-stamped OHLCV and infer exact seven-day bars."""
    if not isinstance(bars, pd.DataFrame) or len(bars) < 2:
        raise ValueError("at least two bars are required to establish the interval")
    idx = bars.index
    if not isinstance(idx, pd.DatetimeIndex) or str(idx.tz) not in ("UTC", "Etc/UTC", "GMT", "Etc/GMT", "UTC+00:00"):
        raise ValueError("bars require a UTC DatetimeIndex")
    if idx.hasnans or not idx.is_unique or not idx.is_monotonic_increasing:
        raise ValueError("bar times must be finite, increasing and unique")
    times = idx.as_unit("ns").asi8
    steps = np.diff(times)
    duration = int(steps[0])
    week = pd.Timedelta(days=7).value
    if duration <= 0 or np.any(steps != duration) or np.any(times % duration):
        raise ValueError("bar times must be aligned and gap-free with equal intervals")
    if week % duration or duration > week:
        raise ValueError("bar interval must evenly divide seven days")
    if not bars.columns.is_unique or not set(BAR_COLUMNS).issubset(bars.columns):
        raise ValueError("bars require unique open/high/low/close/volume columns")
    if "confirm" in bars and not bars["confirm"].map(lambda v: str(v) in ("1", "True")).all():
        raise ValueError("bars must be confirmed")
    try:
        values = bars.loc[:, BAR_COLUMNS].to_numpy(dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError("OHLCV must be finite numeric values") from error
    if not np.isfinite(values).all():
        raise ValueError("OHLCV must be finite numeric values")
    o, h, l, c, v = values.T
    if np.any((l <= 0) | (v < 0) | (h < np.maximum(o, c)) | (l > np.minimum(o, c))):
        raise ValueError("invalid OHLCV range")
    return values, week // duration


def _smma(values: np.ndarray, length: int) -> np.ndarray:
    """SMA-seeded recurrence, identical to the existing monitor/Pine contract."""
    result = np.full(len(values), np.nan)
    if len(values) >= length:
        result[length - 1] = values[:length].mean()
        for i in range(length, len(values)):
            result[i] = (result[i - 1] * (length - 1) + values[i]) / length
    return result


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Division only at known nonzero denominators; never return infinities."""
    result = numerator / denominator.where(denominator.ne(0))
    return result.where(np.isfinite(result))


def build_altcoin_features(bars: pd.DataFrame, params: IMACDParams = IMACDParams()) -> pd.DataFrame:
    """Return causal features on the unchanged open-time index, without IO.

    See the module docstring for every source column and window. Focus fields
    describe state after t; on a release, near_zero_bars/focus_start_i preserve
    the prior segment while release_band/release_zone_high/release_zone_low
    snapshot its frozen tolerance and pre-release range. Release-only values
    are NaN on all other rows. OHLCV inputs themselves are not returned.
    """
    if not isinstance(params, IMACDParams):
        raise TypeError("params must be IMACDParams")
    values, seven_days = _validate(bars)
    o, h, l, c, v = values.T
    n = len(values)
    close = pd.Series(c, index=bars.index)
    src = pd.Series((h + l + c) / 3, index=bars.index)
    first = src.ewm(span=params.length_ma, adjust=False).mean()
    mi = (2 * first - first.ewm(span=params.length_ma, adjust=False).mean()).to_numpy()
    hi, lo = _smma(h, params.length_ma), _smma(l, params.length_ma)
    md = np.where(mi > hi, mi - hi, np.where(mi < lo, mi - lo, 0.0))
    sb = pd.Series(md).rolling(params.length_signal).mean().to_numpy()
    previous_close = np.r_[np.nan, c[:-1]]
    tr = np.fmax(h - l, np.fmax(np.abs(h - previous_close), np.abs(l - previous_close)))
    atr = _smma(tr, 14)
    out = pd.DataFrame({"hi": hi, "lo": lo, "mi": mi, "md": md, "sb": sb,
                        "sh": md - sb, "atr": atr}, index=bars.index.copy())
    for length in (20, 60, 120):
        out[f"sma{length}"] = close.rolling(length).mean()
        out[f"ema{length}"] = close.ewm(span=length, adjust=False).mean()
    mas = out.loc[:, MA_COLUMNS].to_numpy()
    out["rope_high"], out["rope_low"] = mas.max(axis=1), mas.min(axis=1)
    width = _ratio(out.rope_high - out.rope_low, out.atr)
    flips = np.zeros(n)
    for a, b in combinations(MA_COLUMNS, 2):
        diff = out[a] - out[b]
        previous = diff.shift(1)
        flips += ((diff > 0) & (previous <= 0)) | ((diff < 0) & (previous >= 0))
    out["prior_width_atr"] = width.shift(1).rolling(12).mean()
    out["prior_crosses"] = pd.Series(flips, index=bars.index).shift(1).rolling(12).sum()
    out["dense"] = (out.prior_width_atr <= 3) & (out.prior_crosses >= 2)
    out["dense_recent"] = out.dense.rolling(34, min_periods=1).max().eq(1)
    out["ready"] = ((np.arange(n) >= max(340, params.length_ma * 10))
                    & np.isfinite(out.prior_width_atr) & np.isfinite(sb) & np.isfinite(md))

    run, start, qualified = 0, -1, False
    band = zone_high = zone_low = np.nan
    release_side = np.zeros(n, dtype=np.int8)
    near_zero_bars = np.zeros(n, dtype=np.int64)
    focus_start = np.full(n, -1)
    qualified_rows = np.zeros(n, dtype=bool)
    focus_band, release_band, release_high, release_low = (np.full(n, np.nan) for _ in range(4))
    ready = out.ready.to_numpy()
    for i in range(n):
        prior_run, prior_start = run, start
        candidate = params.focus_atr_band * atr[i - 1] if i else np.nan
        if ready[i] and np.isfinite(candidate):
            magnitude = max(abs(md[i]), abs(sb[i]))
            if qualified:
                if magnitude <= band:
                    run += 1
                    zone_high, zone_low = max(zone_high, h[i]), min(zone_low, l[i])
                else:
                    side = 1 if md[i] > band else -1 if md[i] < -band else 0
                    if side:
                        release_side[i], release_band[i] = side, band
                        release_high[i], release_low[i] = zone_high, zone_low
                    qualified, run, start = False, 0, -1
                    band = zone_high = zone_low = np.nan
            elif magnitude <= candidate:
                run += 1
                if run == 1:
                    start, zone_high, zone_low = i, h[i], l[i]
                else:
                    zone_high, zone_low = max(zone_high, h[i]), min(zone_low, l[i])
                if run >= params.focus_min_bars:
                    qualified, band = True, float(candidate)
            else:
                run, start = 0, -1
                zone_high = zone_low = np.nan
        near_zero_bars[i], focus_start[i] = ((prior_run, prior_start) if release_side[i] else (run, start))
        qualified_rows[i], focus_band[i] = qualified, band
    out["release_side"] = release_side
    out["near_zero_bars"] = near_zero_bars
    out["focus_start_i"] = pd.array(np.where(focus_start >= 0, focus_start, None), dtype="Int64")
    out["qualified"], out["focus_band"], out["release_band"] = qualified_rows, focus_band, release_band
    out["release_zone_high"], out["release_zone_low"] = release_high, release_low

    previous_atr = out.atr.shift(1)
    volume = pd.Series(v, index=bars.index)
    true_range = pd.Series(tr, index=bars.index)
    out["relative_volume"] = _ratio(volume, volume.shift(1).rolling(20).median())
    out["tr_expansion"] = _ratio(true_range, previous_atr)
    out["body_fraction"] = _ratio(pd.Series(abs(c - o), index=bars.index), true_range)
    out["close_location"] = _ratio(pd.Series(c - l, index=bars.index), pd.Series(h - l, index=bars.index))
    out["momentum3_atr"] = _ratio(close.diff(3), previous_atr)
    out["bb_width20"] = _ratio(4 * close.rolling(20).std(ddof=0), close.rolling(20).mean())
    out["bb_width_rank_prior240"] = out.bb_width20.shift(1).rolling(240).rank(method="average", pct=True)
    out["atr_pct"] = _ratio(out.atr, close)
    out["prior7d_atr_pct_mean"] = out.atr_pct.shift(1).rolling(seven_days).mean()
    out["prior7d_return"] = _ratio(close.shift(1), close.shift(seven_days + 1)) - 1
    return out

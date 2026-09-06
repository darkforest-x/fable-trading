"""Causal bar features for the hourly impulse / lower-timeframe exit study.

The owner's proposed entry is a real bullish/bearish candle or engulfing body
crossing a moving average. Indicator colour is HL2 versus MA, not candle direction.
Utilities are pure dataframe transforms: they never fetch, score, or open data.
Every rolling/recursive feature restarts at a missing-bar boundary. ATR follows
Pine RMA's arithmetic-mean seed and Wilder recursion; EMA seeds at the first
available HL2, while SMA requires a complete window.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


BAR_COLUMNS = ["open_time", "open", "high", "low", "close", "volume"]
ENTRY_COLUMNS = [
    "event_id", "signal_time", "decision_time", "direction", "signal_open",
    "signal_high", "signal_low", "signal_close", "initial_stop", "signal_atr",
    "ma", "ma_side", "body_ratio", "range_atr", "close_location", "volume_ratio",
    "ma_slope_atr", "cross_count24", "efficiency24", "is_engulf", "breakout20",
    "extension_atr",
]
ENTRY_DEFAULTS: Dict[str, Any] = {
    "shape": "large_or_engulf",
    "body_ratio_min": 0.65,
    "range_atr_min": 1.0,
    "engulf_range_atr_min": 0.65,
    "close_location_min": 0.70,
    "min_volume_ratio": 0.0,
    "max_cross_count": float("inf"),
    "min_efficiency": 0.0,
    "require_ma_slope": False,
    "require_breakout20": False,
    "min_extension_atr": 0.0,
    "max_extension_atr": float("inf"),
    "side": "both",
}


def _validated_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate only supplied OHLCV/timestamps; never reorder or impute bars."""
    missing = set(BAR_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError("missing bar columns: %s" % sorted(missing))
    result = frame.copy().reset_index(drop=True)
    if pd.api.types.is_numeric_dtype(result["open_time"].dtype) and len(result):
        raise ValueError("normalize numeric open_time epochs to UTC datetimes first")
    result["open_time"] = pd.to_datetime(result["open_time"], utc=True, errors="raise")
    times = result["open_time"]
    if times.isna().any() or not times.is_monotonic_increasing or times.duplicated().any():
        raise ValueError("open_time must be non-null, monotonic, and unique")
    for name in BAR_COLUMNS[1:]:
        result[name] = pd.to_numeric(result[name], errors="raise").astype(float)
    numbers = result[BAR_COLUMNS[1:]].to_numpy(dtype=float)
    if not np.isfinite(numbers).all():
        raise ValueError("OHLCV must be finite")
    invalid = (
        (result[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (result["volume"] < 0)
        | (result["low"] > result[["open", "close"]].min(axis=1))
        | (result["high"] < result[["open", "close"]].max(axis=1))
        | (result["low"] > result["high"])
    )
    if invalid.any():
        raise ValueError("invalid OHLC bounds, non-positive price, or negative volume")
    return result


def resample_complete(raw: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate complete, epoch-aligned UTC buckets from five-minute OHLCV.

    Uses only the 1/3/12 raw five-minute bars belonging to the requested
    5/15/60-minute bucket. Incomplete buckets are dropped, including edge buckets.
    ``open_time`` is the bucket start, not availability time: its OHLCV is usable
    only at ``open_time + minutes``. ``segment_id`` increments whenever retained
    bucket starts are not exactly ``minutes`` apart. No missing bar is fabricated.
    """
    if isinstance(minutes, bool) or minutes not in (5, 15, 60):
        raise ValueError("minutes must be one of 5, 15, 60")
    bars = _validated_bars(raw)
    if not bars["open_time"].eq(bars["open_time"].dt.floor("5min")).all():
        raise ValueError("raw timestamps must be aligned five-minute bar starts")
    if bars.empty:
        result = bars[BAR_COLUMNS].copy()
        result["segment_id"] = pd.Series(dtype="int64")
    else:
        grouped = bars.groupby(bars["open_time"].dt.floor("%dmin" % minutes), sort=True)
        result = grouped.agg(
            open=("open", "first"), high=("high", "max"), low=("low", "min"),
            close=("close", "last"), volume=("volume", "sum"), count=("open", "size"),
        )
        result = result.loc[result["count"].eq(minutes // 5)].drop(columns="count")
        result.index.name = "open_time"
        result = result.reset_index()
        gap = result["open_time"].diff().ne(pd.Timedelta(minutes=minutes))
        result["segment_id"] = gap.cumsum().sub(1).astype("int64")
    result.attrs["bar_minutes"] = minutes
    return result


def _rma(values: pd.Series, length: int = 14) -> pd.Series:
    """Pine-style RMA: first ``length`` observations seed a simple mean."""
    array = values.to_numpy(dtype=float)
    output = np.full(len(array), np.nan)
    if len(array) >= length:
        output[length - 1] = array[:length].mean()
        for i in range(length, len(array)):
            output[i] = (output[i - 1] * (length - 1) + array[i]) / length
    return pd.Series(output, index=values.index)


def add_features(
    frame: pd.DataFrame, ma_kind: str = "SMA", ma_length: int = 40
) -> pd.DataFrame:
    """Add closed-bar features using only each row and its segment's past.

    Added columns and windows:
    * hl2, body_ratio, long_close_location, short_close_location: current OHLC.
    * ma: SMA(ma_length) or EMA(alpha=2/(ma_length+1)) of HL2.
    * atr: RMA14 of max(high-low, abs(high-prev_close), abs(low-prev_close));
      the first segment bar uses high-low. range_atr uses current range / ATR.
    * ma_side: +1 when current HL2 >= available MA, -1 below, 0 unavailable.
    * ma_slope_atr: (MA - MA three completed bars ago) / (3 * current ATR).
    * volume_ratio: current volume / mean of preceding 20 volumes.
    * bullish_engulf/bearish_engulf: current and preceding real open/close;
      prior opposite body, body contains prior body, at least one strict edge.
    * cross_count24: sign-flip events on preceding 24 completed bars, where
      a flip requires both sides available. The current flip is excluded.
    * efficiency24: abs(close - close[24]) / sum(abs(close.diff()), 24).
    * prior_high20, prior_low20, prior_range_median20: preceding 20 bars,
      explicitly excluding the current bar.

    All windows and recursive seeds reset on segment_id. A constant-price
    efficiency window is 0; undefined ratios/insufficient windows remain NaN.
    """
    if ma_kind not in ("SMA", "EMA"):
        raise ValueError("ma_kind must be SMA or EMA")
    if isinstance(ma_length, bool) or not isinstance(ma_length, int) or ma_length < 1:
        raise ValueError("ma_length must be a positive integer")
    if "segment_id" not in frame.columns:
        raise ValueError("segment_id is required; use resample_complete first")
    result = _validated_bars(frame)
    if result["segment_id"].isna().any():
        raise ValueError("segment_id must not be null")
    segment_runs = result["segment_id"].ne(result["segment_id"].shift()).cumsum()
    if result.groupby("segment_id", sort=False)["open_time"].size().size != segment_runs.nunique():
        raise ValueError("segment_id must describe contiguous, unrepeated runs")
    parts = []
    for _, part in result.groupby("segment_id", sort=False):
        part = part.copy()
        o, h, low, c, v = [part[name] for name in BAR_COLUMNS[1:]]
        span = h - low
        safe_span = span.replace(0, np.nan)
        part["hl2"] = (h + low) / 2
        if ma_kind == "SMA":
            part["ma"] = part["hl2"].rolling(ma_length, min_periods=ma_length).mean()
        else:
            part["ma"] = part["hl2"].ewm(span=ma_length, adjust=False).mean()
        tr = pd.concat([span, (h - c.shift()).abs(), (low - c.shift()).abs()], axis=1).max(axis=1)
        part["atr"] = _rma(tr)
        safe_atr = part["atr"].replace(0, np.nan)
        part["ma_side"] = np.where(part["ma"].isna(), 0, np.where(part["hl2"] >= part["ma"], 1, -1))
        part["ma_slope_atr"] = part["ma"].diff(3) / (3 * safe_atr)
        part["body_ratio"] = (c - o).abs() / safe_span
        part["range_atr"] = span / safe_atr
        part["long_close_location"] = (c - low) / safe_span
        part["short_close_location"] = (h - c) / safe_span
        prior_volume = v.shift().rolling(20, min_periods=20).mean()
        part["volume_ratio"] = v / prior_volume.replace(0, np.nan)
        part["bullish_engulf"] = (
            (c > o) & (c.shift() < o.shift()) & (o <= c.shift()) & (c >= o.shift())
            & ((o < c.shift()) | (c > o.shift()))
        )
        part["bearish_engulf"] = (
            (c < o) & (c.shift() > o.shift()) & (o >= c.shift()) & (c <= o.shift())
            & ((o > c.shift()) | (c < o.shift()))
        )
        side = part["ma_side"]
        flips = ((side * side.shift()) < 0).astype(float)
        part["cross_count24"] = flips.shift().rolling(24, min_periods=24).sum()
        denominator = c.diff().abs().rolling(24, min_periods=24).sum()
        part["efficiency24"] = c.diff(24).abs() / denominator.replace(0, np.nan)
        part.loc[denominator.eq(0), "efficiency24"] = 0.0
        part["prior_high20"] = h.shift().rolling(20, min_periods=20).max()
        part["prior_low20"] = low.shift().rolling(20, min_periods=20).min()
        part["prior_range_median20"] = span.shift().rolling(20, min_periods=20).median()
        parts.append(part)
    if parts:
        result = pd.concat(parts, axis=0).sort_index()
    else:
        for column in (
            "hl2", "ma", "atr", "ma_side", "ma_slope_atr", "body_ratio", "range_atr",
            "long_close_location", "short_close_location", "volume_ratio", "bullish_engulf",
            "bearish_engulf", "cross_count24", "efficiency24", "prior_high20", "prior_low20",
            "prior_range_median20",
        ):
            result[column] = pd.Series(dtype=float)
    result.attrs.update(frame.attrs)
    result.attrs.update({"ma_kind": ma_kind, "ma_length": ma_length})
    return result


def make_entries(hourly_featured: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """Select hour-close impulse entries with no future bars or K2 requirement.

    Uses only current OHLC/MA/ATR/shape and the historical features documented
    by add_features. Body crossing is strict open < MA < close (long), mirrored
    for short; current HL2 colour must agree. Initial stop is current K1 extreme.
    Signal time is hour open, decision time is hour open + one hour, and any fill
    must be sourced separately at/after decision_time. ``ma_slope_atr`` and
    ``extension_atr`` are signed by trade direction. ``breakout20`` means close
    beyond the preceding 20 highs (long) or lows (short). Zero/default optional
    floors do not add warmup filters. Unknown parameter keys raise rather than
    silently changing the experiment contract.
    """
    unknown = set(params) - set(ENTRY_DEFAULTS)
    if unknown:
        raise ValueError("unknown entry parameters: %s" % sorted(unknown))
    chosen = dict(ENTRY_DEFAULTS, **params)
    if chosen["shape"] not in ("large_only", "engulf_only", "large_or_engulf"):
        raise ValueError("invalid shape")
    if chosen["side"] not in ("both", "long", "short"):
        raise ValueError("invalid side")
    bars = hourly_featured
    if bars.attrs.get("bar_minutes", 60) != 60:
        raise ValueError("make_entries requires hourly bars")
    if len(bars) and not bars["open_time"].eq(bars["open_time"].dt.floor("60min")).all():
        raise ValueError("make_entries requires UTC-aligned hourly open_time")
    results = []
    for direction, name in ((1, "long"), (-1, "short")):
        if chosen["side"] not in ("both", name):
            continue
        location = bars["%s_close_location" % name]
        engulf = bars["bullish_engulf" if direction == 1 else "bearish_engulf"]
        large_ok = (bars["body_ratio"] >= chosen["body_ratio_min"]) & (bars["range_atr"] >= chosen["range_atr_min"])
        engulf_ok = engulf & (bars["range_atr"] >= chosen["engulf_range_atr_min"])
        shape_ok = large_ok if chosen["shape"] == "large_only" else engulf_ok if chosen["shape"] == "engulf_only" else large_ok | engulf_ok
        extension = direction * (bars["close"] - bars["ma"]) / bars["atr"]
        slope = direction * bars["ma_slope_atr"]
        breakout = bars["close"] > bars["prior_high20"] if direction == 1 else bars["close"] < bars["prior_low20"]
        keep = (
            shape_ok & (location >= chosen["close_location_min"])
            & (direction * (bars["open"] - bars["ma"]) < 0)
            & (direction * (bars["close"] - bars["ma"]) > 0)
            & bars["ma_side"].eq(direction) & (bars["atr"] > 0)
            & (extension >= chosen["min_extension_atr"])
            & (extension <= chosen["max_extension_atr"])
        )
        if chosen["min_volume_ratio"] > 0:
            keep &= bars["volume_ratio"] >= chosen["min_volume_ratio"]
        if np.isfinite(chosen["max_cross_count"]):
            keep &= bars["cross_count24"] <= chosen["max_cross_count"]
        if chosen["min_efficiency"] > 0:
            keep &= bars["efficiency24"] >= chosen["min_efficiency"]
        if chosen["require_ma_slope"]:
            keep &= slope > 0
        if chosen["require_breakout20"]:
            keep &= breakout
        selected = bars.loc[keep].copy()
        if selected.empty:
            continue
        selected["signal_time"] = selected["open_time"]
        selected["decision_time"] = selected["open_time"] + pd.Timedelta(hours=1)
        selected["direction"] = direction
        selected["event_id"] = selected["open_time"].map(lambda value: value.isoformat()) + ("_L" if direction == 1 else "_S")
        for column in ("open", "high", "low", "close"):
            selected["signal_%s" % column] = selected[column]
        selected["initial_stop"] = selected["low" if direction == 1 else "high"]
        selected["signal_atr"] = selected["atr"]
        selected["close_location"] = location.loc[keep]
        selected["ma_slope_atr"] = slope.loc[keep]
        selected["is_engulf"] = engulf.loc[keep]
        selected["breakout20"] = breakout.loc[keep]
        selected["extension_atr"] = extension.loc[keep]
        results.append(selected[ENTRY_COLUMNS])
    if not results:
        return pd.DataFrame(columns=ENTRY_COLUMNS)
    return pd.concat(results, ignore_index=True).sort_values("signal_time").reset_index(drop=True)

"""Prior-context-only four-hour features for the hourly impulse study.

This module never reads files or evaluates outcomes. Native UTC four-hour bars
require all 48 five-minute observations. Features reuse the reviewed SMA40 HL2
and RMA14 ATR implementation in ``hourly_impulse.add_features``. Joining at K1
OPEN deliberately excludes K1's own impulse from its entry environment.

Pandas 2.3.3 sources for named aggregation and backward availability joins:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.core.groupby.DataFrameGroupBy.agg.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge_asof.html
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS, add_features, resample_complete


CONTEXT_COLUMNS = [
    "context_available", "context_side", "context_slope_atr", "context_valid",
]
FOUR_HOURS = pd.Timedelta(hours=4)
FIVE_MINUTES = pd.Timedelta(minutes=5)


def _aggregate_4h(five: pd.DataFrame) -> pd.DataFrame:
    """Aggregate already validated five-minute bars, retaining raw continuity."""
    if five.empty:
        result = five[BAR_COLUMNS].copy()
        result["raw_segment_id"] = pd.Series(dtype="int64")
        result["segment_id"] = pd.Series(dtype="int64")
    else:
        grouped = five.groupby(five["open_time"].dt.floor("4h"), sort=True)
        result = grouped.agg(
            open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"),
            volume=("volume", "sum"), count=("open", "size"),
            raw_segment_id=("segment_id", "first"),
        )
        # Unique, five-minute-aligned timestamps make count=48 sufficient to
        # prove that every member of this four-hour UTC bucket is present.
        result = result.loc[result["count"].eq(48)].drop(columns="count")
        result.index.name = "open_time"
        result = result.reset_index()
        gap = result["open_time"].diff().ne(FOUR_HOURS)
        result["segment_id"] = gap.cumsum().sub(1).astype("int64")
    result.attrs["bar_minutes"] = 240
    return result


def complete_4h_bars(raw_5m: pd.DataFrame) -> pd.DataFrame:
    """Return complete native four-hour OHLCV bars; never impute missing data.

    Inputs are UTC-normalizable, unique, increasing ``open_time`` and finite
    valid ``open/high/low/close/volume`` five-minute bars. Only a bucket's own
    48 observations are used. Output ``open_time`` is the UTC bucket OPEN;
    the bar becomes available four hours later. ``raw_segment_id`` identifies
    uninterrupted source five-minute bars. ``segment_id`` resets whenever a
    retained four-hour bucket is missing, so every rolling warmup resets too.
    """
    return _aggregate_4h(resample_complete(raw_5m, 5))


def _hourly_starts(hourly_frame: pd.DataFrame) -> pd.Series:
    if "open_time" not in hourly_frame:
        raise ValueError("hourly_frame requires open_time")
    times = hourly_frame["open_time"].reset_index(drop=True)
    if len(times) and pd.api.types.is_numeric_dtype(times.dtype):
        raise ValueError("normalize numeric hourly open_time to UTC first")
    times = pd.to_datetime(times, utc=True, errors="raise")
    if times.isna().any() or not times.is_monotonic_increasing or times.duplicated().any():
        raise ValueError("hourly open_time must be non-null, monotonic, and unique")
    if not times.eq(times.dt.floor("1h")).all():
        raise ValueError("hourly open_time must be UTC-aligned hour starts")
    if hourly_frame.attrs.get("bar_minutes", 60) != 60:
        raise ValueError("hourly_frame must describe one-hour bars")
    return times


def add_prior_4h_context(raw_5m: pd.DataFrame, hourly_frame: pd.DataFrame) -> pd.DataFrame:
    """Append four causal context columns without dropping any hourly rows.

    Only ``hourly_frame.open_time`` is used, never K1 OHLC or hour-close time.
    Source columns/windows are four-hour OHLCV from each exact 48-bar five-minute
    bucket; SMA40 of current/past four-hour HL2; ATR14 with Pine RMA seed; and
    ``(MA - MA[3]) / (3 * ATR)``. All rolling/recursive state resets after an
    incomplete or absent four-hour bucket. At least 43 consecutive complete
    four-hour bars are therefore needed for a finite SMA40 three-bar slope.

    Added columns:
    * context_available: latest complete four-hour bar's OPEN + 4h, <= K1 OPEN;
      NaT if no fresh, uninterrupted source context exists. A warmup-only bar
      may have an availability timestamp while context_valid remains False.
    * context_side: +1 if that bar's HL2 >= SMA40, -1 otherwise; 0 if invalid.
    * context_slope_atr: that bar's unsigned-direction MA slope, NaN if invalid.
    * context_valid: finite MA/slope, positive ATR, age <4h, and uninterrupted
      five-minute source observations through the bar ending at K1 OPEN.

    Raw continuity is checked at K1 OPEN, not merely at the chosen 4h close:
    even one missing intervening five-minute observation invalidates carry.
    Data at or after K1 OPEN cannot affect these columns. Current incomplete
    four-hour candles never participate. Original hourly index, columns, and
    attrs are preserved; timestamps are normalized to UTC. Existing context
    columns are rejected rather than silently overwritten.
    """
    collisions = set(CONTEXT_COLUMNS).intersection(hourly_frame.columns)
    if collisions:
        raise ValueError("context columns already exist: %s" % sorted(collisions))
    times = _hourly_starts(hourly_frame)
    five = resample_complete(raw_5m, 5)
    four = add_features(_aggregate_4h(five), "SMA", 40)
    result = hourly_frame.copy()
    result["open_time"] = times.array
    result["context_available"] = pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns, UTC]")
    result["context_side"] = np.zeros(len(result), dtype="int64")
    result["context_slope_atr"] = np.nan
    result["context_valid"] = False
    if result.empty or four.empty:
        return result

    context = four[["open_time", "raw_segment_id", "ma", "atr", "ma_side", "ma_slope_atr"]].copy()
    context["context_available"] = context.pop("open_time") + FOUR_HOURS
    # Exact boundary matches are causal: the four-hour candle has just closed
    # when K1 starts. Backward never selects K1's eventual four-hour close.
    joined = pd.merge_asof(
        times.to_frame(), context, left_on="open_time", right_on="context_available",
        direction="backward", allow_exact_matches=True,
    )
    # The exact five-minute bar ending at K1 OPEN proves source coverage up to
    # that instant. Matching its segment to the chosen context catches gaps
    # after the context close, including those inside the unfinished 4h bar.
    raw_segments = pd.Series(
        five["segment_id"].to_numpy(), index=five["open_time"] + FIVE_MINUTES,
    )
    segment_at_open = times.map(raw_segments)
    age = times - joined["context_available"]
    fresh = (
        age.ge(pd.Timedelta(0)) & age.lt(FOUR_HOURS)
        & segment_at_open.notna() & segment_at_open.eq(joined["raw_segment_id"])
    )
    valid = (
        fresh & joined["ma_side"].isin((-1, 1)) & joined["atr"].gt(0)
        & np.isfinite(joined["ma"]) & np.isfinite(joined["ma_slope_atr"])
    )
    result["context_available"] = joined["context_available"].where(fresh).array
    result["context_side"] = joined["ma_side"].where(valid, 0).astype("int64").to_numpy()
    result["context_slope_atr"] = joined["ma_slope_atr"].where(valid).to_numpy()
    result["context_valid"] = valid.to_numpy(dtype=bool)
    return result

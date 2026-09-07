"""V26: fixed 40-hour HL2 entry reference, changing weighting only.

SMA is the frozen equal-weight reference; VWMA is sum(HL2*volume,40) /
sum(volume,40). TradingView's published example uses close; HL2 here deliberately
preserves the original K1 price source, and is not claimed as its default:
https://www.tradingview.com/support/solutions/43000592293-volume-weighted-moving-average-vwma/
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Series.rolling.html

Inputs: explicit UTC native-hour open_time and OHLC, optional volume/segment_id.
All features use only the current completed hour and its contiguous past.
Hourly gaps derive segment boundaries; supplied segment IDs must equal these
IDs (starting at zero), never raw5/another aggregation's numbering. No fills,
resampling, MA-length search, entries, labels, I/O or execution occur here.
Only _rma is reused from the immutable yoyo.data.hourly_impulse reference.
Common and MA-dependent formulas below deliberately mirror frozen add_features.

Bad/missing volume is NOT a reason to discard a price bar or reject SMA.
Invalid volume is NaN in volume-dependent arithmetic, never fake zero/one.
VWMA needs 40 finite nonnegative volumes with positive total; invalid windows
remain unknown. SMA's availability does not depend on these diagnostics.
Complete positive equal-volume windows use the mathematically identical SMA
mean so floating multiplication/division cannot flip exact HL2 == MA ties.
Legacy cross_count24 counts only flips between two available sides and excludes
the current bar, exactly as frozen SMA; unavailable side zero is not a flip.
Version contracts: pandas 2.3.3 / numpy 2.0.2; no new dependencies.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from numbers import Number, Real

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import _rma


LENGTH = 40
FEATURE_COLUMNS = (
    "hl2", "ma", "atr", "ma_side", "ma_slope_atr", "body_ratio", "range_atr",
    "long_close_location", "short_close_location", "volume_ratio", "bullish_engulf",
    "bearish_engulf", "cross_count24", "efficiency24", "prior_high20", "prior_low20",
    "prior_range_median20",
)
REFERENCE_COLUMNS = (
    "reference_known", "reference_reason", "reference_available_at",
    "reference_window_start", "reference_count", "reference_volume_valid",
    "reference_volume_valid_count", "reference_volume_invalid_count",
    "reference_volume_sum",
)
_OHLC = ("open", "high", "low", "close")


def _hour(value):
    if isinstance(value, (Number, np.bool_)) or not isinstance(value, (str, datetime, pd.Timestamp)):
        raise ValueError("Explicit UTC hour timestamps required")
    try:
        time = pd.Timestamp(value)
        if (pd.isna(time) or time.tzinfo is None or time.utcoffset() != timedelta(0)
                or time != time.floor("h")):
            raise ValueError("Explicit UTC hour timestamps required")
        return time.tz_convert("UTC").as_unit("ns")
    except (TypeError, OverflowError) as error:
        raise ValueError("Explicit UTC hour timestamps required") from error


def _normalized(hourly):
    if (not isinstance(hourly, pd.DataFrame) or not hourly.columns.is_unique
            or not {"open_time", *_OHLC}.issubset(hourly)):
        raise ValueError("Unique hourly open_time/OHLC schema required")
    if set(FEATURE_COLUMNS) & set(hourly) or any(str(c).startswith("reference_") for c in hourly):
        raise ValueError("Refusing precomputed feature/reference column overwrite")
    if hourly.attrs.get("bar_minutes", 60) != 60:
        raise ValueError("Only native-hour features are supported")
    result = hourly.copy(deep=True).reset_index(drop=True)
    result["open_time"] = pd.array([_hour(v) for v in result.open_time], dtype="datetime64[ns, UTC]")
    times = result.open_time
    if not times.is_unique or not times.is_monotonic_increasing:
        raise ValueError("Hour timestamps must be sorted and unique")
    derived = times.diff().ne(pd.Timedelta(hours=1)).cumsum().sub(1).astype("int64")
    if "segment_id" in result:
        values = result.segment_id.tolist()
        if (any(isinstance(v, (bool, np.bool_)) or not isinstance(v, Real)
                or not np.isfinite(v) or v != int(v) for v in values)
                or values != derived.tolist()):
            raise ValueError("segment_id must equal timestamp-derived hourly segments")
    else:
        result["segment_id"] = derived
    for column in _OHLC:
        if result[column].map(lambda v: isinstance(v, (bool, np.bool_))).any():
            raise ValueError("OHLC must be finite positive numeric, not bool")
        result[column] = pd.to_numeric(result[column], errors="raise").astype(float)
    values = result[list(_OHLC)].to_numpy(dtype=float)
    if len(values):
        o, h, low, c = values.T
        if (not np.isfinite(values).all() or (values <= 0).any()
                or (low > np.minimum(o, c)).any() or (h < np.maximum(o, c)).any()
                or (low > h).any()):
            raise ValueError("Invalid OHLC bounds or nonfinite/nonpositive price")
    if "volume" not in result:
        result["volume"] = np.nan
    bad_bool = result.volume.map(lambda v: isinstance(v, (bool, np.bool_)))
    result["volume"] = pd.to_numeric(result.volume, errors="coerce").astype(float)
    valid = ~bad_bool & np.isfinite(result.volume) & result.volume.ge(0)
    # Retain a NaN rather than an invented volume for every unobserved value.
    result["volume"] = result.volume.where(valid)
    return result, valid


def add_reference_features(hourly, reference="SMA"):
    """Compute one fixed SMA40/VWMA40 arm with frozen common-feature parity.

    Returns a normalized RangeIndex frame as frozen add_features does, without
    modifying the input. Existing unrelated columns/attrs are preserved. All
    OHLC features, RMA14 ATR, body/engulf/location, efficiency24, prior20 ranges,
    and current-volume / preceding20-volume ratio are common to both arms.
    ma_side, three-bar slope/current ATR, and prior24 flip count use THIS arm's
    reference. make_entries remains the original, separately called function.

    reference_count is min(40, contiguous hours so far). window_start is the
    REQUIRED open_time-39h even when unavailable; available_at is hour close.
    Volume valid/invalid counts describe the currently observed portion of that
    40h window; volume_sum is known only for a complete all-valid 40h window.
    Reasons: warmup, invalid_volume, zero_volume_sum, nonfinite_reference, known.
    SMA ignores volume failures when determining reference_known/reason.
    """
    if not isinstance(reference, str) or reference not in ("SMA", "VWMA"):
        raise ValueError("reference must be exactly SMA or VWMA (fixed length 40)")
    result, valid_volume = _normalized(hourly)
    parts = []
    for _, part in result.groupby("segment_id", sort=False):
        part = part.copy()
        o, h, low, c, v = [part[name] for name in (*_OHLC, "volume")]
        span = h-low
        safe_span = span.replace(0, np.nan)
        part["hl2"] = (h+low)/2
        count = pd.Series(np.minimum(np.arange(1, len(part)+1), LENGTH), index=part.index)
        valid = valid_volume.loc[part.index]
        valid_count = valid.astype(int).rolling(LENGTH, min_periods=1).sum().astype("int64")
        volume_sum = v.rolling(LENGTH, min_periods=LENGTH).sum()
        if reference == "SMA":
            part["ma"] = part.hl2.rolling(LENGTH, min_periods=LENGTH).mean()
        else:
            weighted_sum = (part.hl2*v).rolling(LENGTH, min_periods=LENGTH).sum()
            part["ma"] = weighted_sum / volume_sum.where(volume_sum.gt(0))
            volume_min = v.rolling(LENGTH, min_periods=LENGTH).min()
            volume_max = v.rolling(LENGTH, min_periods=LENGTH).max()
            equal_weight = (volume_min.eq(volume_max) & volume_min.gt(0)
                            & np.isfinite(volume_sum))
            equal_mean = part.hl2.rolling(LENGTH, min_periods=LENGTH).mean()
            part.loc[equal_weight, "ma"] = equal_mean.loc[equal_weight]
            part["ma"] = part.ma.where(np.isfinite(part.ma) & count.eq(LENGTH) & valid_count.eq(LENGTH))
        tr = pd.concat([span, (h-c.shift()).abs(), (low-c.shift()).abs()], axis=1).max(axis=1)
        part["atr"] = _rma(tr)
        safe_atr = part.atr.replace(0, np.nan)
        part["ma_side"] = np.where(part.ma.isna(), 0, np.where(part.hl2 >= part.ma, 1, -1))
        part["ma_slope_atr"] = part.ma.diff(3)/(3*safe_atr)
        part["body_ratio"] = (c-o).abs()/safe_span
        part["range_atr"] = span/safe_atr
        part["long_close_location"] = (c-low)/safe_span
        part["short_close_location"] = (h-c)/safe_span
        prior_volume = v.shift().rolling(20, min_periods=20).mean()
        part["volume_ratio"] = v/prior_volume.replace(0, np.nan)
        part["bullish_engulf"] = ((c > o) & (c.shift() < o.shift()) & (o <= c.shift())
                                  & (c >= o.shift()) & ((o < c.shift()) | (c > o.shift())))
        part["bearish_engulf"] = ((c < o) & (c.shift() > o.shift()) & (o >= c.shift())
                                  & (c <= o.shift()) & ((o > c.shift()) | (c < o.shift())))
        side = part.ma_side
        flips = ((side*side.shift()) < 0).astype(float)
        part["cross_count24"] = flips.shift().rolling(24, min_periods=24).sum()
        denominator = c.diff().abs().rolling(24, min_periods=24).sum()
        part["efficiency24"] = c.diff(24).abs()/denominator.replace(0, np.nan)
        part.loc[denominator.eq(0), "efficiency24"] = 0.
        part["prior_high20"] = h.shift().rolling(20, min_periods=20).max()
        part["prior_low20"] = low.shift().rolling(20, min_periods=20).min()
        part["prior_range_median20"] = span.shift().rolling(20, min_periods=20).median()
        known = np.isfinite(part.ma)
        reason = pd.Series("known", index=part.index)
        reason.loc[~known] = "nonfinite_reference"
        if reference == "VWMA":
            reason.loc[volume_sum.eq(0)] = "zero_volume_sum"
            reason.loc[valid_count.lt(count)] = "invalid_volume"
        reason.loc[count.lt(LENGTH)] = "warmup"
        part["reference_known"], part["reference_reason"] = known, reason
        part["reference_available_at"] = part.open_time+pd.Timedelta(hours=1)
        part["reference_window_start"] = part.open_time-pd.Timedelta(hours=LENGTH-1)
        part["reference_count"] = count
        part["reference_volume_valid"] = valid
        part["reference_volume_valid_count"] = valid_count
        part["reference_volume_invalid_count"] = count-valid_count
        part["reference_volume_sum"] = volume_sum
        parts.append(part)
    if parts:
        result = pd.concat(parts).sort_index()
    else:
        for column in FEATURE_COLUMNS:
            result[column] = pd.Series(dtype=float)
        for column in REFERENCE_COLUMNS:
            dtype = ("datetime64[ns, UTC]" if column.endswith("_at") or column.endswith("_start")
                     else bool if column in ("reference_known", "reference_volume_valid")
                     else object if column == "reference_reason" else "int64" if column.endswith("count") else float)
            result[column] = pd.Series(dtype=dtype)
    result.attrs.update(hourly.attrs)
    result.attrs.update(ma_kind=reference, ma_length=LENGTH, bar_minutes=60,
                        reference_source="HL2", volume_column_present="volume" in hourly,
                        reference_scope="entry_only", raw_aggregation_verified=False)
    return result

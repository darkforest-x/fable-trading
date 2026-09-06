"""V13 pure native4h colour at K1 OPEN, with no ATR or slope requirement.

Approved raw input is five-minute OHLCV. Reuse complete native UTC4h aggregation
from hourly_impulse_context, but NOT its feature/support gate: only40 complete
contiguous4h HL2 observations are needed for SMA40. No outcomes or files are
read, no entry is dropped, and no request's direction is copied to another.

Pandas2.3.3 documents the complete integer rolling window and backward <= join:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Series.rolling.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge_asof.html
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS, resample_complete
from yoyo.data.hourly_impulse_context import complete_4h_bars


FOUR_HOURS = pd.Timedelta(hours=4)
FIVE_MINUTES = pd.Timedelta(minutes=5)
COLOUR_COLUMNS = [
    "prior_colour_bar_open", "prior_colour_available_at", "prior_colour_ma",
    "prior_colour_hl2", "prior_colour_side", "prior_colour_known",
    "prior_colour_reason", "prior_colour_count", "prior_colour_gate_state",
    "prior_colour_raw_segment_id",
]


def _utc(values: pd.Series) -> pd.Series:
    """Timezone-aware clock only; never guess numeric timestamp units."""
    times = []
    for value in values:
        if isinstance(value, (int, float, np.number, bool)):
            raise ValueError("Explicit timezone-aware timestamps required")
        time = pd.Timestamp(value)
        if pd.isna(time) or time.tzinfo is None:
            raise ValueError("Finite timezone-aware timestamps required")
        times.append(time.tz_convert("UTC"))
    return pd.Series(times, dtype="datetime64[ns, UTC]")


def _empty_context(requests: pd.DataFrame) -> pd.DataFrame:
    result = requests.copy()
    for column in ("prior_colour_bar_open", "prior_colour_available_at"):
        result[column] = pd.array([pd.NaT] * len(result), dtype="datetime64[ns, UTC]")
    for column in ("prior_colour_ma", "prior_colour_hl2"):
        result[column] = np.nan
    for column in ("prior_colour_side", "prior_colour_count", "prior_colour_raw_segment_id"):
        result[column] = pd.array([pd.NA] * len(result), dtype="Int64")
    result["prior_colour_known"] = False
    result["prior_colour_reason"] = "no_complete_4h"
    result["prior_colour_gate_state"] = "unknown"
    return result.loc[:, list(requests.columns) + COLOUR_COLUMNS]


def add_prior_colour_context(requests: pd.DataFrame, raw5: pd.DataFrame) -> pd.DataFrame:
    """Append own prior4h colour without sorting/filtering/mutating requests.

    Request inputs are only signal_time (K1 OPEN, exact UTC hour) and direction
    (+1/-1). Original index, columns, values, row order and attrs are preserved;
    repeated signal times are allowed, but supplied event_id must be nonnull
    and unique. Existing diagnostic columns are rejected, never overwritten.

    Source windows: each4h OHLCV uses all48 UTC-aligned5m bars; HL2=(high+low)/2;
    SMA40 includes that completed4h bar and its39 immediately preceding complete
    contiguous4h bars. Missing/incomplete4h resets the40-bar window. ATR, slope,
    candle open/close direction, volume strength and request K1 OHLC are NOT
    colour gates. Even zero volatility is legal. Equality HL2==MA yields +1.

    Latest completed4h availability=bar_open+4h must be <=signal_time and its
    age must be [0,4h). The exact5m bar ending at signal_time must exist in the
    same raw timestamp-derived contiguous segment as the context; a missing
    intervening5m invalidates carry. No fallback past a missing latest4h. Raw
    price values at/after the latest requested signal_time are not selected or
    validated. Raw timestamps are an explicit globally valid input clock.

    Added columns, all known no later than signal_time:
    * prior_colour_bar_open / prior_colour_available_at: selected complete4h
      candidate's open/close timestamps; retained for invalid/stale diagnosis.
    * prior_colour_ma / prior_colour_hl2: candidate SMA40 and HL2; no imputation.
    * prior_colour_count: consecutive complete4h count in candidate segment,
      not capped at40; warmup counts1..39 remain visible, none is nullable.
    * prior_colour_raw_segment_id: candidate's timestamp-derived raw segment.
    * prior_colour_known: finite SMA40/HL2 with fresh uninterrupted coverage.
    * prior_colour_side: +1/-1 only when known; otherwise nullable Int64 NA.
    * prior_colour_reason: known, warmup, source_gap, stale_context,
      no_complete_4h, or invalid_colour (nonfinite derived value).
    * prior_colour_gate_state: accepted if known side==own direction, abstain
      for known opposite; unknown is never a zero-return abstention.
    """
    if requests.columns.duplicated().any() or not {"signal_time", "direction"}.issubset(requests):
        raise ValueError("Unique request columns, signal_time and direction required")
    if set(COLOUR_COLUMNS).intersection(requests.columns):
        raise ValueError("Prior colour columns already present; refuse overwrite")
    if "event_id" in requests and (requests.event_id.isna().any() or not requests.event_id.is_unique):
        raise ValueError("Request identities must be unique and nonnull")
    signal_times = _utc(requests.signal_time)
    if not signal_times.eq(signal_times.dt.floor("h")).all():
        raise ValueError("signal_time must be the exact UTC K1 hour OPEN")
    direction = requests.direction.reset_index(drop=True)
    if not direction.isin([-1, 1]).all() or direction.map(lambda x: isinstance(x, (bool, np.bool_))).any():
        raise ValueError("Each request direction must be +1/-1")
    result = _empty_context(requests)
    if result.empty:
        return result
    if raw5.columns.duplicated().any() or not set(BAR_COLUMNS).issubset(raw5):
        raise ValueError("Unique complete raw5 OHLCV schema required")
    raw_times = _utc(raw5.open_time)
    if not raw_times.is_monotonic_increasing or not raw_times.is_unique:
        raise ValueError("Raw timestamps must be unique and chronological")
    # Do not even validate unavailable raw price values on or after K1 OPEN.
    positions = np.flatnonzero(raw_times.lt(signal_times.max()).to_numpy())
    prefix = raw5.iloc[positions][BAR_COLUMNS].copy()
    prefix["open_time"] = raw_times.iloc[positions].array
    five = resample_complete(prefix, 5)
    four = complete_4h_bars(five)
    if four.empty:
        return result
    four["hl2"] = four.high / 2 + four.low / 2
    four["ma"] = four.groupby("segment_id")["hl2"].transform(lambda x: x.rolling(40, min_periods=40).mean())
    four["count"] = four.groupby("segment_id").cumcount() + 1
    four["available_at"] = four.open_time + FOUR_HOURS
    query = pd.DataFrame({"signal_time": signal_times, "position": np.arange(len(requests))})
    selected = pd.merge_asof(query.sort_values("signal_time", kind="stable"),
        four[["open_time", "available_at", "ma", "hl2", "count", "raw_segment_id"]],
        left_on="signal_time", right_on="available_at", direction="backward", allow_exact_matches=True,
    ).sort_values("position").reset_index(drop=True)
    raw_segments = pd.Series(five.segment_id.to_numpy(), index=five.open_time + FIVE_MINUTES)
    ending_segment = selected.signal_time.map(raw_segments)
    age = selected.signal_time - selected.available_at
    exists = selected.available_at.notna()
    fresh = exists & age.ge(pd.Timedelta(0)) & age.lt(FOUR_HOURS)
    continuous = ending_segment.notna() & ending_segment.eq(selected.raw_segment_id)
    finite = np.isfinite(selected.ma) & np.isfinite(selected.hl2)
    known = fresh & continuous & finite & selected["count"].ge(40)
    reason = pd.Series("no_complete_4h", index=selected.index)
    reason.loc[exists & ~fresh] = "stale_context"
    reason.loc[fresh & ~continuous] = "source_gap"
    reason.loc[fresh & continuous & selected["count"].lt(40)] = "warmup"
    reason.loc[fresh & continuous & selected["count"].ge(40) & ~finite] = "invalid_colour"
    reason.loc[known] = "known"
    side = pd.Series(np.where(selected.hl2.ge(selected.ma), 1, -1), dtype="Int64").where(known)
    gate = pd.Series("unknown", index=selected.index)
    gate.loc[known & side.eq(direction).fillna(False)] = "accepted"
    gate.loc[known & side.ne(direction).fillna(False)] = "abstain"
    result["prior_colour_bar_open"] = selected.open_time.array
    result["prior_colour_available_at"] = selected.available_at.array
    result["prior_colour_ma"] = selected.ma.to_numpy()
    result["prior_colour_hl2"] = selected.hl2.to_numpy()
    result["prior_colour_count"] = pd.array(selected["count"], dtype="Int64")
    result["prior_colour_raw_segment_id"] = pd.array(selected.raw_segment_id, dtype="Int64")
    result["prior_colour_known"] = known.to_numpy(dtype=bool)
    result["prior_colour_side"] = side.array
    result["prior_colour_reason"] = reason.to_numpy()
    result["prior_colour_gate_state"] = gate.to_numpy()
    return result

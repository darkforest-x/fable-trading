"""Causal, persistent 10/10 confirmed-pivot structure at each own K1 close.

This re-examines the older structure family on direct hourly K1 requests; it is
not a new independent factor. The frozen Python approximation confirms a centre
high/low when it equals the maximum/minimum of its 21-hour window, INCLUDING
ties. This is deliberately NOT a claim of TradingView ta.pivot* tie parity.
Levels become available only at the confirming hour's CLOSE, never at their
plotted centre. A close crossing a level whose PRICE is unchanged since the
preceding hour establishes/reverses direction, which then persists. Same-side
crossings do not create a new directional break. Missing hours reset everything.

Source semantics: ChartPrime Market Break Analytics, source lines
79-80, 93-99, 124-130 and 153-156 (confirmed pivots, unchanged-price guard and
alternating state); local historical approximation is explicitly tie-inclusive.
https://www.tradingview.com/script/0vET13Ra-Market-Break-Analytics-ChartPrime/
https://www.tradingview.com/pine-script-docs/language/execution-model/
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Series.rolling.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.DataFrame.resample.html
"""
from __future__ import annotations

from collections import deque

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS, resample_complete


HOUR = pd.Timedelta(hours=1)
PIVOT_LEFT = PIVOT_RIGHT = 10
PIVOT_WIDTH = 21
_TIME_COLUMNS = [
    "structure_available_at", "structure_high_origin", "structure_low_origin",
    "structure_high_confirmed_at", "structure_low_confirmed_at",
    "structure_last_break_available_at",
]
_INTEGER_COLUMNS = [
    "structure_count", "structure_segment_id", "structure_state_before",
    "structure_state", "structure_break_direction",
]
HOURLY_STRUCTURE_COLUMNS = _TIME_COLUMNS + _INTEGER_COLUMNS + [
    "structure_high", "structure_low", "structure_signal_close",
    "structure_break_on_k1", "structure_known", "structure_reason",
]
STRUCTURE_COLUMNS = HOURLY_STRUCTURE_COLUMNS + [
    "structure_raw_segment_id", "structure_gate_state",
]


def _utc(values: pd.Series) -> pd.Series:
    """Reject implicit epoch units and naive times, retaining positional order."""
    times = []
    for value in values:
        if isinstance(value, (int, float, np.number, bool)):
            raise ValueError("Explicit timezone-aware timestamps required")
        try:
            time = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Finite timezone-aware timestamps required") from error
        if pd.isna(time) or time.tzinfo is None:
            raise ValueError("Finite timezone-aware timestamps required")
        times.append(time.tz_convert("UTC"))
    return pd.Series(times, dtype="datetime64[ns, UTC]")


def _empty_columns(result: pd.DataFrame, available: pd.Series) -> None:
    for column in _TIME_COLUMNS:
        result[column] = pd.array([pd.NaT] * len(result), dtype="datetime64[ns, UTC]")
    result["structure_available_at"] = available.array
    for column in _INTEGER_COLUMNS:
        result[column] = pd.array([pd.NA] * len(result), dtype="Int64")
    result["structure_count"] = pd.array([0] * len(result), dtype="Int64")
    result["structure_break_direction"] = pd.array([0] * len(result), dtype="Int64")
    for column in ("structure_high", "structure_low", "structure_signal_close"):
        result[column] = np.nan
    result["structure_break_on_k1"] = False
    result["structure_known"] = False
    result["structure_reason"] = "no_source"


def add_hourly_structure_state(hourly: pd.DataFrame) -> pd.DataFrame:
    """Append pure closed-hour state using only current/prior complete OHLC.

    Input open_time/open/high/low/close must be chronological, unique, explicit
    timezone-aware, UTC-hour-aligned, finite positive and internally consistent.
    Caller supplies COMPLETE native hours, not partial or forward-filled bars.
    Segment IDs are derived from timestamp gaps, never compared with raw5 or
    another aggregation's IDs; any supplied segment_id is preserved but unused.
    No volume/MA/ATR/outcome column is consulted. Original columns/index/attrs
    are preserved. All output clocks are UTC. count is consecutive hours since
    reset (not capped). state/state_before are nullable +/-1, never known zero.

    For hour t, centre t-10 is tested against [t-20,t], with ties permitted.
    Latest pivot origin is its hour OPEN; confirmed_at is t+1h. Equality at a
    level is NOT a break, and a new different-priced level cannot itself cause
    a break. Same-priced replacement pivots can pass the unchanged-price guard.
    break_direction is +/-1 only on a direction-establishing/reversing break,
    otherwise 0; break_on_k1 is its boolean equivalent. last_break_available_at
    persists with state. state_before excludes the current hour's break and is
    cleared at a gap. Reasons: warmup (<21 contiguous hours), no_confirmed_break,
    known. Availability is each hour CLOSE, not a backwards-painted pivot time.
    """
    required = {"open_time", "open", "high", "low", "close"}
    if hourly.columns.duplicated().any() or not required.issubset(hourly):
        raise ValueError("Unique complete hourly OHLC schema required")
    if any(str(column).startswith("structure_") for column in hourly.columns):
        raise ValueError("Structure columns already present; refuse overwrite")
    times = _utc(hourly.open_time)
    if (not times.is_monotonic_increasing or not times.is_unique
            or not times.eq(times.dt.floor("h")).all()):
        raise ValueError("Hourly timestamps must be unique chronological UTC hour starts")
    numbers = hourly[["open", "high", "low", "close"]]
    if numbers.map(lambda value: isinstance(value, (bool, np.bool_))).any().any():
        raise ValueError("Hourly prices must be finite positive numbers, not bool")
    values = numbers.apply(pd.to_numeric, errors="raise").to_numpy(dtype=float)
    if len(values):
        o, high, low, close = values.T
        if (not np.isfinite(values).all() or (values <= 0).any()
                or (low > np.minimum(o, close)).any()
                or (high < np.maximum(o, close)).any() or (low > high).any()):
            raise ValueError("Invalid hourly OHLC")
    result = hourly.copy()
    _empty_columns(result, times + HOUR)
    output = {column: [] for column in HOURLY_STRUCTURE_COLUMNS}
    window = deque(maxlen=PIVOT_WIDTH)
    previous_time = None
    segment = -1
    for position, time in enumerate(times):
        if previous_time is None or time - previous_time != HOUR:
            window.clear()
            segment += 1
            count = 0
            state = None
            latest_high = latest_low = np.nan
            high_origin = low_origin = high_confirmed = low_confirmed = pd.NaT
            last_break = pd.NaT
            previous_close = np.nan
        count += 1
        state_before = state
        old_high, old_low = latest_high, latest_low
        _, high, low, close = values[position]
        window.append((time, high, low))
        available = time + HOUR
        if len(window) == PIVOT_WIDTH:
            centre_time, centre_high, centre_low = window[PIVOT_LEFT]
            if centre_high == max(item[1] for item in window):
                latest_high, high_origin, high_confirmed = centre_high, centre_time, available
            if centre_low == min(item[2] for item in window):
                latest_low, low_origin, low_confirmed = centre_low, centre_time, available
        up = (latest_high == old_high and close > latest_high
              and previous_close <= latest_high and state != 1)
        down = (latest_low == old_low and close < latest_low
                and previous_close >= latest_low and state != -1)
        change = 1 if up else -1 if down else 0
        if change:
            state, last_break = change, available
        row = {
            "structure_available_at": available, "structure_count": count,
            "structure_segment_id": segment, "structure_state_before": state_before,
            "structure_state": state, "structure_break_direction": change,
            "structure_high": latest_high, "structure_low": latest_low,
            "structure_high_origin": high_origin, "structure_low_origin": low_origin,
            "structure_high_confirmed_at": high_confirmed,
            "structure_low_confirmed_at": low_confirmed,
            "structure_last_break_available_at": last_break,
            "structure_signal_close": close, "structure_break_on_k1": bool(change),
            "structure_known": state is not None,
            "structure_reason": ("known" if state is not None else
                                 "warmup" if count < PIVOT_WIDTH else "no_confirmed_break"),
        }
        for column in output:
            output[column].append(row[column])
        previous_time, previous_close = time, close
    for column, data in output.items():
        if column in _TIME_COLUMNS:
            result[column] = pd.array(data, dtype="datetime64[ns, UTC]")
        elif column in _INTEGER_COLUMNS:
            result[column] = pd.array(data, dtype="Int64")
        elif column in ("structure_break_on_k1", "structure_known"):
            result[column] = np.asarray(data, dtype=bool)
        elif column == "structure_reason":
            result[column] = np.asarray(data, dtype=object)
        else:
            result[column] = np.asarray(data, dtype=float)
    return result


def add_structure_context(requests: pd.DataFrame, raw5: pd.DataFrame) -> pd.DataFrame:
    """Attach persistent structure at each request's EXACT own K1 close.

    Required request columns are signal_time (K1 UTC hour OPEN), direction
    (+/-1), signal_close (finite positive); optional decision_time must equal
    signal_time+1h and optional event_id must be unique/non-null. Preserve all
    requests, their original order/index/attrs/columns, including duplicate index
    labels. Each independently aggregated own K1 close must match signal_close
    at rtol=atol=1e-12, even before warmup. A mismatch raises, never masks itself
    as unknown. Known same/opposite direction gives accepted/abstain; no first
    confirmed directional break or absent K1 gives unknown, NOT zero/abstain.

    Raw columns: open_time and OHLCV. Only complete native UTC hours containing
    exactly 12 ordered unique raw5 bars are used; incomplete hours are dropped
    without filling and reset all following state/levels. Raw segment labels
    are ignored: structure_raw_segment_id is timestamp-derived raw continuity,
    structure_segment_id is independently derived hourly continuity. These two
    IDs must NOT be compared numerically. Volume is validated/aggregated but
    never gates state. Prices at/after the latest requested K1 CLOSE are not
    selected or validated; global source timestamps still require a unique,
    chronological explicit UTC5m clock. No future bar paints an earlier pivot.

    structure_available_at always gives the required own close boundary (even
    if unknown). All other state fields require the exact complete own hour;
    missing hours have no stale fallback. Additional reasons: no_source and
    missing_signal_hour. structure_signal_close is the actual aggregated K1
    close, and structure_gate_state is accepted/abstain/unknown. No file I/O,
    price fetching, trading, outcome/MA/ATR/volume filtering or statistics.
    """
    required = {"signal_time", "direction", "signal_close"}
    if requests.columns.duplicated().any() or not required.issubset(requests):
        raise ValueError("Unique request columns, signal_time/direction/signal_close required")
    if any(str(column).startswith("structure_") for column in requests.columns):
        raise ValueError("Structure columns already present; refuse overwrite")
    if "event_id" in requests and (requests.event_id.isna().any() or not requests.event_id.is_unique):
        raise ValueError("Request identities must be unique and nonnull")
    times = _utc(requests.signal_time)
    if not times.eq(times.dt.floor("h")).all():
        raise ValueError("signal_time must be the exact UTC K1 hour OPEN")
    if "decision_time" in requests and not _utc(requests.decision_time).eq(times + HOUR).all():
        raise ValueError("decision_time must equal own signal_time+1h")
    directions = requests.direction.reset_index(drop=True)
    if not directions.isin([-1, 1]).all() or directions.map(lambda x: isinstance(x, (bool, np.bool_))).any():
        raise ValueError("Each request direction must be +1/-1")
    if requests.signal_close.map(lambda x: isinstance(x, (bool, np.bool_))).any():
        raise ValueError("signal_close must be finite positive, not bool")
    closes = pd.to_numeric(requests.signal_close, errors="raise").to_numpy(dtype=float)
    if not (np.isfinite(closes) & (closes > 0)).all():
        raise ValueError("signal_close must be finite and positive")
    result = requests.copy()
    _empty_columns(result, times + HOUR)
    result["structure_raw_segment_id"] = pd.array([pd.NA] * len(result), dtype="Int64")
    result["structure_gate_state"] = "unknown"
    if result.empty:
        return result
    if raw5.columns.duplicated().any() or not set(BAR_COLUMNS).issubset(raw5):
        raise ValueError("Unique complete raw5 OHLCV schema required")
    raw_times = _utc(raw5.open_time)
    if (not raw_times.is_monotonic_increasing or not raw_times.is_unique
            or not raw_times.eq(raw_times.dt.floor("5min")).all()):
        raise ValueError("Raw timestamps must be unique chronological five-minute starts")
    positions = np.flatnonzero(raw_times.lt((times + HOUR).max()).to_numpy())
    prefix = raw5.iloc[positions][BAR_COLUMNS].copy()
    prefix["open_time"] = raw_times.iloc[positions].array
    if prefix[BAR_COLUMNS[1:]].map(lambda x: isinstance(x, (bool, np.bool_))).any().any():
        raise ValueError("Raw OHLCV must be numeric, not bool")
    five = resample_complete(prefix, 5)
    if five.empty:
        return result
    hourly = add_hourly_structure_state(resample_complete(five, 60))
    source_segments = pd.Series(five.segment_id.to_numpy(), index=five.open_time)
    hourly["structure_raw_segment_id"] = hourly.open_time.map(source_segments)
    hourly = hourly.set_index("open_time", drop=False)
    output = {column: result[column].tolist() for column in STRUCTURE_COLUMNS}
    for position, time in enumerate(times):
        if five.open_time.iloc[0] >= time + HOUR:
            continue
        if time not in hourly.index:
            output["structure_reason"][position] = "missing_signal_hour"
            continue
        own = hourly.loc[time]
        if not np.isclose(float(own.close), closes[position], rtol=1e-12, atol=1e-12):
            identity = requests.iloc[position].get("event_id", position)
            raise ValueError("Own complete K1 signal_close parity failed for %s" % identity)
        for column in HOURLY_STRUCTURE_COLUMNS + ["structure_raw_segment_id"]:
            output[column][position] = own[column]
        if own.structure_known:
            output["structure_gate_state"][position] = (
                "accepted" if own.structure_state == directions.iloc[position] else "abstain")
    for column, data in output.items():
        if column in _TIME_COLUMNS:
            result[column] = pd.array(data, dtype="datetime64[ns, UTC]")
        elif column in _INTEGER_COLUMNS + ["structure_raw_segment_id"]:
            result[column] = pd.array(data, dtype="Int64")
        else:
            result[column] = data
    return result

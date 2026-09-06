"""V14 pure prior20-hour breakout context, evaluated at own K1 close.

Use the existing complete native UTC hour aggregation, not a chart offset or
another request's boundary. This is a fixed20-hour price-range gate only: no MA,
hourly slope, prior4h colour, ATR, outcome, or entry-selection dependency.

Pandas2.3.3 documents integer rolling support and within-group observation order:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Series.rolling.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.core.groupby.SeriesGroupBy.shift.html
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS, resample_complete


HOUR = pd.Timedelta(hours=1)
PRIOR_HOURS = 20
BREAKOUT_COLUMNS = [
    "prior_breakout_window_start", "prior_breakout_window_end",
    "prior_breakout_available_at", "prior_breakout_signal_available_at",
    "prior_breakout_count", "prior_breakout_high", "prior_breakout_low",
    "prior_breakout_signal_close", "prior_breakout_raw_segment_id",
    "prior_breakout_known", "prior_breakout_reason", "prior_breakout_gate_state",
]


def _utc(values: pd.Series) -> pd.Series:
    """Require explicit timezone-aware timestamps; do not infer epoch units."""
    times = []
    for value in values:
        if isinstance(value, (int, float, np.number, bool)):
            raise ValueError("Explicit timezone-aware timestamps required")
        time = pd.Timestamp(value)
        if pd.isna(time) or time.tzinfo is None:
            raise ValueError("Finite timezone-aware timestamps required")
        times.append(time.tz_convert("UTC"))
    return pd.Series(times, dtype="datetime64[ns, UTC]")


def add_prior_breakout_context(requests: pd.DataFrame, raw5: pd.DataFrame) -> pd.DataFrame:
    """Append own causal prior20 breakout gate without filtering requests.

    Request columns used: signal_time (exact UTC hour K1 OPEN), direction (+1 or
    -1), and finite positive signal_close. If decision_time is present it must
    equal signal_time+1h. Optional event_id must be unique and nonnull. Original
    index (including duplicate index labels), order, fields, timezone and attrs
    remain untouched. Existing output columns are rejected, never overwritten.

    Source columns used: open_time/open/high/low/close/volume from raw5, each
    complete hour requiring exactly12 valid unique UTC5m bars. Derived raw and
    hourly segment IDs follow timestamp gaps, not supplied segment labels. The
    price-range window is precisely the20 consecutive completed hours whose
    opens are [signal_time-20h, signal_time-1h]. A missing/incomplete hour resets
    support. K1 is EXCLUDED from those extrema; its own complete hour supplies
    the tested close and is independently checked against request signal_close
    (rtol=atol=1e-12). Mismatch raises even if prior support is insufficient.
    No future K1/other request can replace a missing prior hour or own signal.

    Gate availability is K1 close, signal_time+1h. Raw OHLCV at or after the
    latest requested K1 close is neither selected nor validated. Raw timestamps
    must nevertheless form an explicit globally unique, ordered5m clock. No
    file I/O, price fetching, outcomes or statistics are used by this function.

    Added columns (all known at or before own K1 close):
    * prior_breakout_window_start / window_end: expected first/last prior-hour
      OPEN, signal_time-20h / signal_time-1h, even when support is unknown.
    * prior_breakout_available_at / signal_available_at: required availability
      boundaries signal_time / signal_time+1h; not proof of observed coverage.
    * prior_breakout_count: number of immediately preceding complete contiguous
      hours, capped20, zero if the exact preceding hour is absent. Independent
      of whether K1 itself is complete; no partial extrema are published.
    * prior_breakout_high / low: prior20 extrema only when count==20; may remain
      present if K1 is missing. They never include K1 high/low/open/close.
    * prior_breakout_signal_close: independently aggregated complete K1 close,
      or NaN if absent. prior_breakout_raw_segment_id: complete K1's derived
      raw5 segment, otherwise nullable Int64 NA (not an hourly segment ID).
    * prior_breakout_known: complete20 prior hours and complete own K1 with
      uninterrupted source and close parity. prior_breakout_reason: known,
      no_source, missing_signal_hour, warmup, or source_gap. Missing K1 takes
      precedence over prior-support diagnosis; warmup means source begins after
      the required window start, while source_gap means earlier data exists.
    * prior_breakout_gate_state: accepted if known long close>high or known
      short close<low; equality and other known failures are abstain. Missing
      support is unknown, never a known abstention or an invented zero return.
    """
    required = {"signal_time", "direction", "signal_close"}
    if requests.columns.duplicated().any() or not required.issubset(requests):
        raise ValueError("Unique request columns, signal_time/direction/signal_close required")
    if set(BREAKOUT_COLUMNS).intersection(requests.columns):
        raise ValueError("Prior breakout columns already present; refuse overwrite")
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
        raise ValueError("signal_close must be finite and positive")
    closes = pd.to_numeric(requests.signal_close, errors="raise").to_numpy(dtype=float)
    if not (np.isfinite(closes) & (closes > 0)).all():
        raise ValueError("signal_close must be finite and positive")

    result = requests.copy()
    result["prior_breakout_window_start"] = (times - PRIOR_HOURS * HOUR).array
    result["prior_breakout_window_end"] = (times - HOUR).array
    result["prior_breakout_available_at"] = times.array
    result["prior_breakout_signal_available_at"] = (times + HOUR).array
    result["prior_breakout_count"] = pd.array([0] * len(result), dtype="Int64")
    for column in ("prior_breakout_high", "prior_breakout_low", "prior_breakout_signal_close"):
        result[column] = np.nan
    result["prior_breakout_raw_segment_id"] = pd.array([pd.NA] * len(result), dtype="Int64")
    result["prior_breakout_known"] = False
    result["prior_breakout_reason"] = "no_source"
    result["prior_breakout_gate_state"] = "unknown"
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
    five = resample_complete(prefix, 5)
    if five.empty:
        return result
    hourly = resample_complete(five, 60)
    hourly["prior_count"] = (hourly.groupby("segment_id").cumcount() + 1).clip(upper=PRIOR_HOURS)
    # These rolling extrema include their own row. Queries deliberately select
    # ONLY the row at K1 OPEN-1h, equivalent to a one-hour shift at K1.
    hourly["boundary_high"] = hourly.groupby("segment_id")["high"].transform(
        lambda values: values.rolling(PRIOR_HOURS, min_periods=PRIOR_HOURS).max())
    hourly["boundary_low"] = hourly.groupby("segment_id")["low"].transform(
        lambda values: values.rolling(PRIOR_HOURS, min_periods=PRIOR_HOURS).min())
    source_segments = pd.Series(five.segment_id.to_numpy(), index=five.open_time)
    hourly["raw_segment_id"] = hourly.open_time.map(source_segments)
    hourly = hourly.set_index("open_time", drop=False)
    columns = {name: result[name].tolist() for name in BREAKOUT_COLUMNS[4:]}
    for position, signal_time in enumerate(times):
        if five.open_time.iloc[0] >= signal_time + HOUR:
            # A later request can require a later source prefix. That must not
            # change this earlier request's no_source diagnosis.
            continue
        prior_time = signal_time - HOUR
        own = hourly.loc[signal_time] if signal_time in hourly.index else None
        prior = hourly.loc[prior_time] if prior_time in hourly.index else None
        count = int(prior.prior_count) if prior is not None else 0
        columns["prior_breakout_count"][position] = count
        if count == PRIOR_HOURS:
            columns["prior_breakout_high"][position] = float(prior.boundary_high)
            columns["prior_breakout_low"][position] = float(prior.boundary_low)
        if own is None:
            columns["prior_breakout_reason"][position] = "missing_signal_hour"
            continue
        own_close = float(own.close)
        if not np.isclose(own_close, closes[position], rtol=1e-12, atol=1e-12):
            identity = requests.iloc[position].get("event_id", position)
            raise ValueError("Own complete K1 signal_close parity failed for %s" % identity)
        columns["prior_breakout_signal_close"][position] = own_close
        columns["prior_breakout_raw_segment_id"][position] = int(own.raw_segment_id)
        continuous = prior is not None and own.raw_segment_id == prior.raw_segment_id
        if count < PRIOR_HOURS or not continuous:
            beginning = five.open_time.iloc[0]
            columns["prior_breakout_reason"][position] = (
                "warmup" if beginning > signal_time - PRIOR_HOURS * HOUR else "source_gap")
            continue
        accepted = (own_close > prior.boundary_high if directions.iloc[position] == 1
                    else own_close < prior.boundary_low)
        columns["prior_breakout_known"][position] = True
        columns["prior_breakout_reason"][position] = "known"
        columns["prior_breakout_gate_state"][position] = "accepted" if accepted else "abstain"
    for column, values in columns.items():
        result[column] = (pd.array(values, dtype="Int64") if column in
            ("prior_breakout_count", "prior_breakout_raw_segment_id") else values)
    return result

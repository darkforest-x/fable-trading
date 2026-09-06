"""Frozen 4+4 hourly contraction zones and their first close outside the zone.

V7 replaces the moving-average-cross entry family. A release must cross the
FIXED source-zone boundary with its real body and satisfy original V1 candle
morphology. Neither crossing SMA40 nor matching hourly MA colour is required.
MA features remain diagnostics only; the separately frozen execution/exit
policy is the caller's responsibility. This module does not open price files,
execute trades, tune parameters, or inspect outcome labels.

Pandas 2.3.3 UTC conversion and exact hourly availability arithmetic:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.to_datetime.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Timedelta.html
"""

from __future__ import annotations

from collections import deque
from numbers import Number
from typing import Any, Tuple

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import ENTRY_COLUMNS


HOUR = pd.Timedelta(hours=1)
ZONE_FIELDS = [
    "zone_id", "source_start", "source_end", "zone_arm_time", "zone_deadline",
    "zone_upper", "zone_lower", "fold",
]
SOURCE_ENTRY_COLUMNS = ENTRY_COLUMNS + ["fold"] + [name for name in ZONE_FIELDS if name != "fold"] + ["release_wait_hours"]
ZONE_COLUMNS = ZONE_FIELDS + [
    "status", "terminal_time", "release_time", "direction", "event_id", "reason",
]
REQUIRED_COLUMNS = {
    "open_time", "open", "high", "low", "close", "volume", "segment_id",
    "atr", "ma", "ma_side", "body_ratio", "range_atr", "long_close_location",
    "short_close_location", "volume_ratio", "ma_slope_atr", "cross_count24",
    "efficiency24", "bullish_engulf", "bearish_engulf", "prior_high20", "prior_low20",
}


def _timestamp(value: Any, name: str, *, hourly: bool) -> pd.Timestamp:
    if isinstance(value, Number):
        raise ValueError("normalize numeric %s with an explicit epoch unit first" % name)
    stamp = pd.to_datetime(value, utc=True, errors="raise")
    if not isinstance(stamp, pd.Timestamp) or pd.isna(stamp):
        raise ValueError("%s must be one non-null timestamp" % name)
    if hourly and stamp != stamp.floor("1h"):
        raise ValueError("%s must be UTC hour-aligned" % name)
    return stamp


def _segment_valid(value: Any) -> bool:
    if pd.isna(value):
        return False
    return bool(np.isfinite(value)) if isinstance(value, Number) else True


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return float("nan")


def _valid_prices(row: dict) -> bool:
    o, h, low, c, volume = [_number(row[name]) for name in ("open", "high", "low", "close", "volume")]
    return bool(np.isfinite([o, h, low, c, volume]).all() and min(o, h, low, c) > 0
                and volume >= 0 and low <= min(o, c) <= max(o, c) <= h)


def _release(row: dict, previous: dict, direction: int, boundary: float) -> Tuple[bool, dict, str]:
    """Current completed OHLC/ATR plus preceding real body; no MA admission."""
    o, h, low, c = [_number(row[name]) for name in ("open", "high", "low", "close")]
    atr = _number(row["atr"])
    span = h-low
    body = abs(c-o)/span if span > 0 else float("nan")
    location = ((c-low) if direction == 1 else (h-c))/span if span > 0 else float("nan")
    range_atr = span/atr if np.isfinite(atr) and atr > 0 else float("nan")
    po, pc = _number(previous["open"]), _number(previous["close"])
    if direction == 1:
        engulf = c > o and pc < po and o <= pc and c >= po and (o < pc or c > po)
    else:
        engulf = c < o and pc > po and o >= pc and c <= po and (o > pc or c < po)
    detail = {"body_ratio": body, "range_atr": range_atr,
              "close_location": location, "is_engulf": bool(engulf)}
    if not np.isfinite(atr) or atr <= 0 or span <= 0:
        return False, detail, "unavailable_atr_or_range"
    if not (direction*(o-boundary) < 0 and direction*(c-boundary) > 0):
        return False, detail, "release_body_does_not_strictly_cross_boundary"
    if location < .70:
        return False, detail, "release_close_location_below_070"
    if not ((body >= .65 and range_atr >= 1.0) or (engulf and range_atr >= .65)):
        return False, detail, "release_not_large_or_real_engulf"
    return True, detail, "qualified_first_release"


def _entry(row: dict, direction: int, geometry: dict, zone: dict) -> dict:
    """Record current release values; MA diagnostics retain V1 signed units."""
    signal = row["open_time"]
    atr, ma, c = _number(row["atr"]), _number(row["ma"]), _number(row["close"])
    return {
        "event_id": signal.isoformat()+("_L" if direction == 1 else "_S"),
        "signal_time": signal, "decision_time": signal+HOUR, "direction": direction,
        **{"signal_"+name: row[name] for name in ("open", "high", "low", "close")},
        "initial_stop": row["low" if direction == 1 else "high"], "signal_atr": atr,
        # Stable scalar types prevent an unobserved suffix NaN from upcasting
        # historical output dtypes even when the historical values are equal.
        "ma": _number(row["ma"]), "ma_side": _number(row["ma_side"]), **geometry,
        "volume_ratio": _number(row["volume_ratio"]),
        "ma_slope_atr": direction*_number(row["ma_slope_atr"]),
        "cross_count24": _number(row["cross_count24"]), "efficiency24": _number(row["efficiency24"]),
        "breakout20": bool(c > _number(row["prior_high20"])) if direction == 1 else bool(c < _number(row["prior_low20"])),
        "extension_atr": direction*(c-ma)/atr, **zone,
        "release_wait_hours": (signal+HOUR-zone["zone_arm_time"]).total_seconds()/3600,
    }


def build_source_zone_requests(
    hourly_featured: pd.DataFrame,
    *,
    fold: str,
    start: Any,
    end_exclusive: Any,
    observed_through: Any,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return immutable source-zone release entries and EVERY armed zone.

    Causal input columns/windows: native 1h OHLCV/segment_id from add_features;
    current release ATR (Pine RMA14 with existing earlier warmup allowed); real
    engulf uses release and preceding completed open/close. Current-body ratio,
    range/ATR and direction close location are recalculated from those prices.
    MA/ma_side/volume_ratio/ma_slope_atr/cross_count24/efficiency24/prior_high20/
    prior_low20 are supplied causal diagnostics with add_features windows.
    They do NOT select entries: hourly MA colour, slope, availability and MA
    crossing are not gates. The fixed release boundary replaces the V1 MA.

    Only hourly bars opened at or after start and completely available at
    open_time+1h <= observed_through may form a source. While idle scan the
    latest 8 continuous valid hours: the first 4's min-low/max-high envelope
    must STRICTLY contain the last 4's envelope on BOTH sides. Freeze that
    last-4 lower/upper; arm at the eighth close, only if arm < end_exclusive-80h
    (strict 8h wait +72h execution embargo). No zones straddle fold start.

    After arming, inspect the first through eighth new complete hours. The
    FIRST close strictly outside either fixed boundary consumes the zone even
    when morphology fails. An equal close or wick alone does not release.
    Require a strict real-body crossing of the released boundary, positive
    finite ATR, directional close location >=.70 and either body>=.65 with
    range>=1ATR, or a true opposite-body engulf with range>=.65ATR. Initial
    stop is the RELEASE candle extreme, not a source-zone extreme. At the
    eighth close test release BEFORE expiring. No alternate/second release.

    Following consumption/expiry, collect 8 NEW hours with open >= terminal
    close; no source/pending bar can be reused, nested or concurrently armed.
    Missing hours/segment transitions/invalid completed OHLCV censor a pending
    zone as censored_source_gap and reset idle history. A missing-hour terminal
    is its first expected close; a segment/invalid-bar terminal is that observed
    bar's close. A new valid bar opened at/after terminal may begin new history.
    Running out of supplied complete bars leaves a pending zone explicitly
    censored_source_end at min(observed_through,end_exclusive), never known zero.
    Later gaps cannot rewrite an already known terminal zone. All future OHLCV
    and feature values are ignored at the supplied observation cutoff. An end-
    censored pending snapshot may resolve when replayed at a LATER cutoff;
    that is new observation, not a rewrite of a known release or expiration.

    Output source_start/source_end are first/eighth source BAR OPEN timestamps;
    source_end+1h is zone_arm_time. release_time is release BAR OPEN, whereas
    terminal_time is its close (or explicit censor availability). Direction is
    known for any first release; event_id is set only for an emitted request.
    entries contain SOURCE_ENTRY_COLUMNS (ENTRY_COLUMNS plus fold/zone lineage
    and release_wait_hours); zones contain ZONE_COLUMNS, one row per arm with
    request_emitted/first_release_unqualified/expired_no_release/
    censored_source_gap/censored_source_end. Unknown and known nonentries both
    survive. There is no optimization, file access, fill/risk screening or exit.
    """
    if not isinstance(fold, str) or not fold.strip():
        raise ValueError("fold must be a nonempty name")
    if not hourly_featured.columns.is_unique:
        raise ValueError("hourly_featured has duplicate columns")
    missing = REQUIRED_COLUMNS-set(hourly_featured.columns)
    if missing:
        raise ValueError("hourly_featured missing columns: %s" % sorted(missing))
    for name, expected in (("bar_minutes", 60), ("ma_kind", "SMA"), ("ma_length", 40)):
        if hourly_featured.attrs.get(name, expected) != expected:
            raise ValueError("hourly_featured must supply native 1h SMA40 diagnostics")
    begin = _timestamp(start, "start", hourly=True)
    end = _timestamp(end_exclusive, "end_exclusive", hourly=True)
    cutoff = min(_timestamp(observed_through, "observed_through", hourly=False), end)
    if end <= begin:
        raise ValueError("end_exclusive must follow start")
    source_times = hourly_featured["open_time"]
    if len(source_times) and (pd.api.types.is_numeric_dtype(source_times.dtype)
                             or source_times.map(lambda value: isinstance(value, Number)).any()):
        raise ValueError("normalize numeric open_time with an explicit epoch unit first")
    times = pd.to_datetime(source_times, utc=True, errors="raise")
    if times.isna().any():
        raise ValueError("open_time must not be missing")
    visible = times.ge(begin) & times.lt(end) & (times+HOUR).le(cutoff)
    visible_times = times.loc[visible]
    if (visible_times.duplicated().any() or not visible_times.is_monotonic_increasing
            or not visible_times.eq(visible_times.dt.floor("1h")).all()):
        raise ValueError("observed fold hours must be unique chronological UTC hour starts")
    bars = hourly_featured.loc[visible].copy()
    bars["open_time"] = visible_times
    history = deque(maxlen=8)
    entries, zones = [], []
    pending, previous = None, None
    earliest_new_open = begin

    def terminate(status: str, terminal: pd.Timestamp, reason: str,
                  release_time: Any = pd.NaT, direction: Any = pd.NA,
                  event_id: Any = None) -> None:
        nonlocal pending, earliest_new_open
        zones.append({**pending, "status": status, "terminal_time": terminal,
                      "release_time": release_time, "direction": direction,
                      "event_id": event_id, "reason": reason})
        pending = None
        history.clear()
        earliest_new_open = terminal

    for row in bars.to_dict("records"):
        time, close_time = row["open_time"], row["open_time"]+HOUR
        valid = _valid_prices(row) and _segment_valid(row["segment_id"])
        gap = previous is not None and time != previous["open_time"]+HOUR
        segment_change = (previous is not None and _segment_valid(row["segment_id"])
                          and row["segment_id"] != previous["segment_id"])
        if gap or segment_change or not valid:
            if pending is not None:
                terminal = previous["open_time"]+2*HOUR if gap else close_time
                reason = "missing_completed_hour" if gap else ("segment_changed" if segment_change else "invalid_completed_hour")
                terminate("censored_source_gap", terminal, reason)
            history.clear()
            previous = None
        if not valid:
            earliest_new_open = max(earliest_new_open, close_time)
            continue
        if pending is not None:
            direction = 1 if _number(row["close"]) > pending["zone_upper"] else (-1 if _number(row["close"]) < pending["zone_lower"] else 0)
            if direction:
                boundary = pending["zone_upper" if direction == 1 else "zone_lower"]
                qualifies, geometry, reason = _release(row, previous, direction, boundary)
                if qualifies:
                    entry = _entry(row, direction, geometry, pending)
                    entries.append(entry)
                    terminate("request_emitted", close_time, reason, time, direction, entry["event_id"])
                else:
                    terminate("first_release_unqualified", close_time, reason, time, direction)
            elif close_time == pending["zone_deadline"]:
                terminate("expired_no_release", close_time, "eight_complete_hours_without_release")
        elif time >= earliest_new_open:
            history.append(row)
            if len(history) == 8 and close_time < end-80*HOUR:
                source = list(history)
                first_low = min(_number(bar["low"]) for bar in source[:4])
                first_high = max(_number(bar["high"]) for bar in source[:4])
                second_low = min(_number(bar["low"]) for bar in source[4:])
                second_high = max(_number(bar["high"]) for bar in source[4:])
                if second_low > first_low and second_high < first_high:
                    pending = {"zone_id": close_time.isoformat()+"_ZONE",
                               "source_start": source[0]["open_time"], "source_end": time,
                               "zone_arm_time": close_time, "zone_deadline": close_time+8*HOUR,
                               "zone_upper": second_high, "zone_lower": second_low, "fold": fold}
                    history.clear()
        previous = row
    if pending is not None:
        terminate("censored_source_end", cutoff, "no_further_complete_supplied_hour")
    entry_frame = pd.DataFrame(entries, columns=SOURCE_ENTRY_COLUMNS)
    zone_frame = pd.DataFrame(zones, columns=ZONE_COLUMNS)
    for frame, time_columns in ((entry_frame, ["signal_time", "decision_time", "source_start", "source_end", "zone_arm_time", "zone_deadline"]),
                                (zone_frame, ["source_start", "source_end", "zone_arm_time", "zone_deadline", "terminal_time", "release_time"])):
        for name in time_columns:
            frame[name] = pd.to_datetime(frame[name], utc=True)
    zone_frame["direction"] = pd.array(zone_frame["direction"], dtype="Int64")
    return entry_frame, zone_frame

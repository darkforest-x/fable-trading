"""Pure, flat-first native-5m alignment requests for immutable K2 episodes.

The V6 policy changes entry timing, not stops or signal selection. Its source
observation checks mirror ``hourly_impulse._transition_observation`` in the
execution layer without importing that layer. Only supplied, completed native
SMA40(HL2) management observations are accepted. No files, outcomes, future
extrema, parameter search, or execution fills are used here.

UTC normalization and exact boundary arithmetic follow pandas 2.3.3:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.to_datetime.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Timedelta.html
"""

from __future__ import annotations

from numbers import Number
from typing import Any, Tuple

import numpy as np
import pandas as pd


FIVE_MINUTES = pd.Timedelta(minutes=5)
ADDED_COLUMNS = [
    "base_decision_time", "realign_deadline", "realign_wait_minutes",
    "total_wait_minutes", "realign_initial_state", "realign_initial_side",
    "realign_initial_reason", "realign_confirmation_bar_open",
    "realign_confirmation_available_at", "realign_confirmation_side",
]
STATUS_COLUMNS = [
    "event_id", "mother_decision_time", "mother_deadline", "base_decision_time",
    "realign_deadline", "terminal_time", "status", "reason",
    "realign_wait_minutes", "total_wait_minutes", "wait_hours",
    "realign_initial_state", "realign_initial_side", "realign_initial_reason",
    "realign_confirmation_bar_open", "realign_confirmation_available_at",
    "realign_confirmation_side",
]
TIME_COLUMNS = [
    "base_decision_time", "realign_deadline", "realign_confirmation_bar_open",
    "realign_confirmation_available_at",
]


def _times(values: pd.Series, name: str, *, source: bool = False) -> pd.Series:
    """Require explicit timestamp units and native-grid source identities."""
    values = values.reset_index(drop=True)
    if len(values) and (
        pd.api.types.is_numeric_dtype(values.dtype)
        or values.map(lambda value: isinstance(value, Number)).any()
    ):
        raise ValueError("normalize numeric %s with an explicit epoch unit first" % name)
    times = pd.to_datetime(values, utc=True, errors="raise")
    if times.isna().any() or not times.eq(times.dt.floor("5min")).all():
        raise ValueError("%s must be non-null five-minute UTC timestamps" % name)
    if source and (times.duplicated().any() or not times.is_monotonic_increasing):
        raise ValueError("%s must be unique and chronological" % name)
    return times


def _segment_valid(value: Any) -> bool:
    if pd.isna(value):
        return False
    return bool(np.isfinite(value)) if isinstance(value, Number) else True


def _numbers(row: pd.Series, names: tuple) -> np.ndarray:
    try:
        return np.asarray([float(row[name]) for name in names], dtype=float)
    except (TypeError, ValueError, OverflowError):
        return np.full(len(names), np.nan)


def _observation(raw: pd.DataFrame, management: pd.DataFrame,
                 raw_lookup: dict, management_lookup: dict,
                 boundary: pd.Timestamp) -> Tuple[Any, str, str, Any]:
    """Read previous complete raw OHLC/management HLC, and current OPEN only."""
    previous = boundary - FIVE_MINUTES
    source_index, next_index = raw_lookup.get(previous), raw_lookup.get(boundary)
    if source_index is None or next_index is None:
        return None, "censored_realign_gap", "missing_raw_timestamp", None
    prior, current = raw.iloc[source_index], raw.iloc[next_index]
    if not _segment_valid(prior["segment_id"]) or not _segment_valid(current["segment_id"]):
        return None, "censored_realign_colour", "unknown_raw_segment", None
    if prior["segment_id"] != current["segment_id"]:
        return None, "censored_realign_gap", "raw_segment_changed", None
    source = _numbers(prior, ("open", "high", "low", "close"))
    o, h, low, close = source
    if (not np.isfinite(source).all() or min(source) <= 0
            or not low <= min(o, close) <= max(o, close) <= h):
        return None, "censored_realign_colour", "invalid_completed_raw_ohlc", None
    # Never inspect current high/low/close: even at observed_through only its
    # open is available. Risk relative to immutable initial_stop belongs to L3.
    current_open = _numbers(current, ("open",))[0]
    if not np.isfinite(current_open) or current_open <= 0:
        return None, "censored_realign_colour", "invalid_current_raw_open", None
    management_index = management_lookup.get(previous)
    if management_index is None:
        return None, "censored_realign_colour", "missing_exact_management_bar", None
    row = management.iloc[management_index]
    side = row["ma_side"]
    if (isinstance(side, (bool, np.bool_)) or not isinstance(side, Number)
            or not np.isfinite(side) or side not in (-1, 1)):
        return None, "censored_realign_colour", "invalid_management_side", None
    values = _numbers(row, ("ma", "high", "low", "close"))
    ma, h, low, close = values
    if not np.isfinite(values).all() or min(values) <= 0 or not low <= close <= h:
        return None, "censored_realign_colour", "invalid_management_values", None
    if not _segment_valid(row["segment_id"]):
        return None, "censored_realign_colour", "unknown_management_segment", None
    # Management labels have their OWN namespace, never compared to raw labels.
    return int(side), "", "known", row["segment_id"]


def build_realign_requests(
    raw5: pd.DataFrame,
    management_featured: pd.DataFrame,
    requests: pd.DataFrame,
    *,
    observed_through: Any,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return first alignment requests and one terminal status per input K2.

    Required raw columns: open_time, open/high/low/close, segment_id. Required
    management columns: open_time, ma, high/low/close, ma_side, segment_id.
    Management is supplied native 5m SMA40(HL2); its rolling warmup/colour is
    not recomputed. Required request columns: unique event_id, decision_time
    (base K2 close), mother_decision_time, mother_deadline (=mother+72h),
    direction, initial_stop, signal_atr. Source times are unique chronological
    five-minute UTC starts; numeric epoch units are never guessed.

    Causal columns/windows: at each boundary only raw OPEN at boundary, raw
    OHLC from boundary-5m, and management ma/HLC/ma_side/segment_id from that
    same just-completed bar are consulted. Source timestamps/segments establish
    continuous coverage. No current or later HLC or outcomes influence entry.
    Starting at base decision, test consecutive boundaries through mother+8h
    INCLUSIVE. An already aligned base enters unchanged. Otherwise remain flat
    until the first complete aligned state. That first confirmation consumes
    the only request even if its actual fill has invalid risk: L3 must reject
    it, never request a later retry. Touching K1's extreme while flat does not
    cancel, and no extra hourly invalidation is added after emitted K2.

    Unknown observations before first confirmation terminate fail-closed:
    censored_realign_gap for absent raw timestamps or a raw segment switch;
    censored_realign_colour for missing/invalid management, invalid completed
    raw OHLC/current open, unknown segments, or a management segment switch.
    A future boundary beyond observed_through is censored_realign_end, never
    known zero. Only valid opposite observations all the way THROUGH +8h yield
    expired_no_alignment; confirmation is tested before expiration at equality.

    All immutable original fields, IDs, row order, index, and attrs survive in
    emitted requests. Only decision_time and wait_hours change (wait_hours is
    total elapsed from original mother in possibly fractional hours).
    Added columns are ADDED_COLUMNS: base_decision_time, realign_deadline,
    realign_wait_minutes, total_wait_minutes, initial state/side/reason, and
    exact confirmation bar open/available-at/side. Existing added diagnostics
    or ltf_entry_* context cause ValueError rather than retaining stale values.
    Statuses retain original event_id/mother times, terminal_time, status/reason
    and those diagnostics, including unobserved/censored inputs. No price reads,
    files, execution, stop modification, or outcome filtering occur.
    """
    for frame, columns, name in (
        (raw5, {"open_time", "open", "high", "low", "close", "segment_id"}, "raw5"),
        (management_featured, {"open_time", "ma", "high", "low", "close", "ma_side", "segment_id"}, "management"),
        (requests, {"event_id", "decision_time", "mother_decision_time", "mother_deadline", "direction", "initial_stop", "signal_atr"}, "requests"),
    ):
        if not frame.columns.is_unique:
            raise ValueError("%s has duplicate column names" % name)
        missing = columns - set(frame.columns)
        if missing:
            raise ValueError("%s missing columns: %s" % (name, sorted(missing)))
    collisions = set(ADDED_COLUMNS).intersection(requests.columns)
    collisions.update(name for name in requests.columns if name.startswith("ltf_entry_"))
    if collisions:
        raise ValueError("realign input has existing diagnostics: %s" % sorted(collisions))
    for name, expected in (("bar_minutes", 5), ("ma_kind", "SMA"), ("ma_length", 40)):
        if management_featured.attrs.get(name, expected) != expected:
            raise ValueError("management_featured must be native 5m SMA40(HL2)")
    if isinstance(observed_through, Number):
        raise ValueError("normalize numeric observed_through with an explicit epoch unit first")
    cutoff = pd.to_datetime(observed_through, utc=True, errors="raise")
    if not isinstance(cutoff, pd.Timestamp) or pd.isna(cutoff):
        raise ValueError("observed_through must be one non-null timestamp")
    raw_times = _times(raw5["open_time"], "raw5.open_time", source=True)
    management_times = _times(management_featured["open_time"], "management.open_time", source=True)
    times = {name: _times(requests[name], name) for name in
             ("decision_time", "mother_decision_time", "mother_deadline")}
    if requests.event_id.isna().any() or requests.event_id.duplicated().any():
        raise ValueError("event_id must be non-null and unique")
    if not requests.direction.map(lambda value: isinstance(value, Number)
                                  and not isinstance(value, (bool, np.bool_))
                                  and np.isfinite(value) and value in (-1, 1)).all():
        raise ValueError("direction must be numeric +1/-1")
    deadline = times["mother_decision_time"] + pd.Timedelta(hours=8)
    if not times["mother_deadline"].eq(times["mother_decision_time"] + pd.Timedelta(hours=72)).all():
        raise ValueError("mother_deadline must equal original mother decision +72h")
    if not (times["decision_time"].ge(times["mother_decision_time"])
            & times["decision_time"].le(deadline)).all():
        raise ValueError("base decision must be within original mother +0..8h")
    raw_lookup = {stamp: pos for pos, stamp in enumerate(raw_times)}
    management_lookup = {stamp: pos for pos, stamp in enumerate(management_times)}
    emitted_positions, diagnostics, actual_times, states = [], [], [], []
    for pos in range(len(requests)):
        original = requests.iloc[pos]
        base, mother = times["decision_time"].iloc[pos], times["mother_decision_time"].iloc[pos]
        limit = deadline.iloc[pos]
        initial_state, initial_side, initial_reason = "unknown", pd.NA, "not_observed"
        prior_management_segment = None
        boundary = base
        while True:
            if boundary > cutoff:
                status, terminal, reason = "censored_realign_end", cutoff, "next_boundary_not_observed"
                confirmation_open, confirmation_available, confirmation_side = pd.NaT, pd.NaT, pd.NA
                break
            side, invalid_status, reason, management_segment = _observation(
                raw5, management_featured, raw_lookup, management_lookup, boundary)
            if boundary == base:
                initial_side, initial_reason = (pd.NA if side is None else side), reason
                initial_state = "unknown" if side is None else ("aligned" if side == original.direction else "opposite")
            if not invalid_status and prior_management_segment is not None and management_segment != prior_management_segment:
                invalid_status, reason = "censored_realign_colour", "management_segment_changed"
            if invalid_status:
                status, terminal = invalid_status, boundary
                confirmation_open, confirmation_available, confirmation_side = pd.NaT, pd.NaT, pd.NA
                break
            if side == original.direction:
                status, terminal, reason = "request_emitted", boundary, "first_completed_alignment"
                confirmation_open, confirmation_available, confirmation_side = boundary-FIVE_MINUTES, boundary, side
                break
            if boundary == limit:
                status, terminal, reason = "expired_no_alignment", limit, "all_observed_states_opposite"
                confirmation_open, confirmation_available, confirmation_side = pd.NaT, pd.NaT, pd.NA
                break
            prior_management_segment = management_segment
            boundary += FIVE_MINUTES
        elapsed = max(0.0, (terminal-base).total_seconds()/60)
        total_elapsed = max(0.0, (terminal-mother).total_seconds()/60)
        added = {
            "base_decision_time": base, "realign_deadline": limit,
            "realign_wait_minutes": elapsed, "total_wait_minutes": total_elapsed,
            "realign_initial_state": initial_state, "realign_initial_side": initial_side,
            "realign_initial_reason": initial_reason,
            "realign_confirmation_bar_open": confirmation_open,
            "realign_confirmation_available_at": confirmation_available,
            "realign_confirmation_side": confirmation_side,
        }
        states.append({
            "event_id": original.event_id, "mother_decision_time": original.mother_decision_time,
            "mother_deadline": original.mother_deadline, **added,
            "terminal_time": terminal, "status": status, "reason": reason,
            "wait_hours": total_elapsed/60,
        })
        if status == "request_emitted":
            emitted_positions.append(pos)
            actual_times.append(terminal)
            diagnostics.append(added)
    result = requests.iloc[emitted_positions].copy()
    result["decision_time"] = pd.to_datetime(actual_times, utc=True)
    for name in ADDED_COLUMNS:
        values = [row[name] for row in diagnostics]
        result[name] = pd.to_datetime(values, utc=True) if name in TIME_COLUMNS else values
    result["wait_hours"] = [row["total_wait_minutes"]/60 for row in diagnostics]
    statuses = pd.DataFrame(states, columns=STATUS_COLUMNS, index=requests.index)
    for frame in (result, statuses):
        for name in ("realign_initial_side", "realign_confirmation_side"):
            frame[name] = pd.array(frame[name], dtype="Int64")
    for name in TIME_COLUMNS + ["terminal_time"]:
        statuses[name] = pd.to_datetime(statuses[name], utc=True)
    return result, statuses

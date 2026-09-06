"""Independent, entry-known native-5m/15m colour diagnostics for fixed requests.

No entry/exit selection, file access, outcome inspection or layer imports. Old
``ltf_entry_*`` fields keep their frozen 5m meaning; this helper only appends
``mg_entry_*``. A native management colour is the supplied HL2-versus-MA state,
never real candle body direction or a future transition.

Clock source (repository pandas 2.3.3):
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Timestamp.floor.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.to_datetime.html
The exact most recently completed management close is floor(entry, interval).
This implementation is deliberately independent of L3 for initialization parity.
"""
from __future__ import annotations

from numbers import Number

import numpy as np
import pandas as pd


CONTEXT_COLUMNS = [
    "mg_entry_side", "mg_entry_aligned", "mg_entry_state", "mg_entry_bar_open",
    "mg_entry_available_at", "mg_entry_reason",
]
FIVE_MINUTES = pd.Timedelta(minutes=5)


def _times(values: pd.Series, name: str, *, source: bool) -> pd.Series:
    """Do not guess numeric epoch units or silently reorder source rows."""
    values = values.reset_index(drop=True)
    if len(values) and (pd.api.types.is_numeric_dtype(values.dtype)
                        or values.map(lambda value: isinstance(value, Number)).any()):
        raise ValueError(f"{name}: normalize numeric timestamps with explicit units first")
    result = pd.to_datetime(values, utc=True, errors="raise" if source else "coerce", format="mixed")
    if source and (result.isna().any() or result.duplicated().any()
                   or not result.is_monotonic_increasing):
        raise ValueError(f"{name}: source times must be finite, unique, chronological")
    return result


def _segment_known(value) -> bool:
    """Numeric or opaque source segment IDs are valid, but never missing."""
    if pd.isna(value):
        return False
    return bool(np.isfinite(value)) if isinstance(value, Number) else True


def _observation(raw5, raw_lookup, management_bar, available_at, entry_time,
                 interval) -> tuple:
    """Validate completed native/source OHLC and only the actual entry open."""
    if management_bar is None:
        return None, "missing_management"
    if management_bar["open_time"] + interval != available_at:
        return None, "stale_management"
    try:
        side, ma, low, high, close = map(float, (management_bar[name] for name in
                                               ("ma_side", "ma", "low", "high", "close")))
    except (ValueError, TypeError):
        return None, "invalid_management"
    if not np.isfinite([side, ma, low, high, close]).all():
        return None, "nonfinite_management"
    if side not in (-1, 1) or min(ma, low, high, close) <= 0 or not low <= close <= high:
        return None, "invalid_management"
    if not _segment_known(management_bar["segment_id"]):
        return None, "unknown_management_segment"

    # At +5/+10 phases, all bars between the native close and entry have also
    # completed. They validate continuity, but do not supply a new MA colour.
    expected = pd.date_range(management_bar["open_time"], entry_time, freq="5min")
    if any(time not in raw_lookup for time in expected):
        return None, "missing_source"
    positions = [raw_lookup[time] for time in expected]
    segments = [raw5.iloc[position]["segment_id"] for position in positions]
    if not all(_segment_known(value) for value in segments) or any(value != segments[0] for value in segments):
        return None, "source_segment_change"
    for position in positions[:-1]:
        try:
            open_, high, low, close = map(float, (raw5.iloc[position][name]
                                                for name in ("open", "high", "low", "close")))
        except (ValueError, TypeError):
            return None, "invalid_completed_source"
        if (not np.isfinite([open_, high, low, close]).all() or min(open_, high, low, close) <= 0
                or not low <= min(open_, close) <= max(open_, close) <= high):
            return None, "invalid_completed_source"
    try:
        entry_open = float(raw5.iloc[positions[-1]]["open"])
    except (ValueError, TypeError):
        return None, "invalid_source_open"
    if not np.isfinite(entry_open) or entry_open <= 0:
        return None, "invalid_source_open"
    return int(side), "valid"


def attach_management_context(raw5: pd.DataFrame, management: pd.DataFrame,
                              entries: pd.DataFrame,
                              management_minutes: int) -> pd.DataFrame:
    """Append independently checked completed-management state to every entry.

    Required raw5 columns: open_time/open/high/low/close/segment_id. Management
    needs open_time/ma/ma_side/high/low/close/segment_id. Entries need decision_time
    and direction. All original fields, order, duplicate entry IDs/index and attrs
    remain untouched, including old ltf_entry_* 5m diagnostics. Duplicate source
    timestamps and unsupported intervals fail explicitly; output collisions fail.

    Causal columns/windows: for entry E and native interval M=5 or15 minutes,
    A=floor(E,M); select EXACT management open A-M, available at A<=E. Missing or
    invalid newest colour is unknown; no fallback to an older management bar.
    For a 15m phase +5/+10 entry, that same last completed native bar initializes
    state; the current incomplete management candle is never inspected. MA and
    side are supplied completed-bar features; MA window construction stays in
    add_features. Missing slope is irrelevant. Side is +1/-1, not body direction.

    Every underlying five-minute timestamp in [A-M,E] must exist in the same
    known RAW source segment. OHLC of rows before E must be complete, finite,
    positive and geometrically valid; at E only open is inspected. Management
    MA/HLC, side and its own segment must be valid; its independently numbered
    segment is NEVER compared directly to raw segment IDs. No post-E OHLC is read.

    Added columns: mg_entry_side nullable Int64; mg_entry_aligned nullable boolean;
    mg_entry_state aligned/opposite/unknown; mg_entry_bar_open and
    mg_entry_available_at UTC times of the selected exact management row (NaT
    when absent); mg_entry_reason explicit validation result. Unknown is never
    a false/opposite classification. Initial risk is intentionally NOT an entry
    filter here: compare L3 state parity only after valid execution entry setup.
    """
    if (isinstance(management_minutes, (bool, np.bool_))
            or not isinstance(management_minutes, (int, np.integer))
            or management_minutes not in (5, 15)):
        raise ValueError("management_minutes must be native 5 or 15")
    if management.attrs.get("bar_minutes", management_minutes) != management_minutes:
        raise ValueError("management frame interval conflicts with management_minutes")
    required = ((raw5, {"open_time", "open", "high", "low", "close", "segment_id"}, "raw5"),
                (management, {"open_time", "ma", "ma_side", "high", "low", "close", "segment_id"}, "management"),
                (entries, {"decision_time", "direction"}, "entries"))
    for frame, fields, name in required:
        if not fields.issubset(frame) or frame.columns.duplicated().any():
            raise ValueError(f"{name}: missing required or duplicate columns")
    if set(CONTEXT_COLUMNS).intersection(entries.columns):
        raise ValueError("management context columns already exist")
    raw_times = _times(raw5["open_time"], "raw5.open_time", source=True)
    if not raw_times.eq(raw_times.dt.floor("5min")).all():
        raise ValueError("raw source times must use the native five-minute grid")
    mg = management.copy().reset_index(drop=True)
    mg["open_time"] = _times(mg["open_time"], "management.open_time", source=True)
    entry_times = _times(entries["decision_time"], "entries.decision_time", source=False)
    raw_lookup = {time: position for position, time in enumerate(raw_times)}
    mg_lookup = {time: position for position, time in enumerate(mg["open_time"])}
    interval = pd.Timedelta(minutes=int(management_minutes))
    frequency = f"{management_minutes}min"
    sides, alignments, states, opens, available, reasons = [], [], [], [], [], []
    for position, direction in enumerate(entries["direction"]):
        side, alignment, state = pd.NA, pd.NA, "unknown"
        bar_open, available_at, reason = pd.NaT, pd.NaT, "invalid_entry_time"
        entry_time = entry_times.iloc[position]
        try:
            numeric_direction = float(direction)
        except (ValueError, TypeError):
            numeric_direction = np.nan
        if pd.isna(entry_time):
            pass
        elif entry_time != entry_time.floor("5min"):
            reason = "unaligned_entry_time"
        elif not np.isfinite(numeric_direction) or numeric_direction not in (-1, 1):
            reason = "invalid_entry_direction"
        else:
            completed_at = entry_time.floor(frequency)
            mg_position = mg_lookup.get(completed_at - interval)
            bar = mg.iloc[mg_position] if mg_position is not None else None
            if bar is not None:
                bar_open, available_at = bar["open_time"], completed_at
            observed_side, reason = _observation(raw5, raw_lookup, bar, completed_at,
                                                entry_time, interval)
            if observed_side is not None:
                side = observed_side
                alignment = side == numeric_direction
                state = "aligned" if alignment else "opposite"
        sides.append(side)
        alignments.append(alignment)
        states.append(state)
        opens.append(bar_open)
        available.append(available_at)
        reasons.append(reason)
    result = entries.copy()
    result["mg_entry_side"] = pd.array(sides, dtype="Int64")
    result["mg_entry_aligned"] = pd.array(alignments, dtype="boolean")
    result["mg_entry_state"] = states
    result["mg_entry_bar_open"] = pd.to_datetime(opens, utc=True)
    result["mg_entry_available_at"] = pd.to_datetime(available, utc=True)
    result["mg_entry_reason"] = reasons
    return result

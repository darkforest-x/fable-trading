"""Exact, already-completed native-5m colour at an entry request boundary.

This pure diagnostic separates a state (already aligned/opposite at entry)
from a later transition. It does not generate signals, select thresholds, read
files, or inspect post-entry OHLC. Colours retain the HL2-versus-MA semantics
of ``yoyo.data.hourly_impulse.add_features``; real candle body direction is
never substituted. Exact close-time equality is causally available.

Pandas 2.3.3 backward-asof semantics and exact-match option:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge_asof.html
"""

from __future__ import annotations

from numbers import Number
from typing import Any

import numpy as np
import pandas as pd


CONTEXT_COLUMNS = [
    "ltf_entry_side", "ltf_entry_aligned", "ltf_entry_state",
    "ltf_entry_bar_open", "ltf_entry_available_at", "ltf_entry_context_reason",
]
FIVE_MINUTES = pd.Timedelta(minutes=5)


def _timestamps(values: pd.Series, name: str, *, source: bool) -> pd.Series:
    """Normalize timestamps explicitly; numeric epoch units are not guessed."""
    values = values.reset_index(drop=True)
    if len(values) and (
        pd.api.types.is_numeric_dtype(values.dtype)
        or values.map(lambda value: isinstance(value, Number)).any()
    ):
        raise ValueError("normalize numeric %s with an explicit epoch unit first" % name)
    times = pd.to_datetime(values, utc=True, errors="raise" if source else "coerce")
    if source and (
        times.isna().any() or times.duplicated().any()
        or not times.is_monotonic_increasing
        or not times.eq(times.dt.floor("5min")).all()
    ):
        raise ValueError("%s must be unique, chronological UTC five-minute starts" % name)
    return times


def _valid_segment(value: Any) -> bool:
    """Source segment labels are opaque; only missing/nonfinite labels fail."""
    if pd.isna(value):
        return False
    return bool(np.isfinite(value)) if isinstance(value, Number) else True


def attach_entry_colour_context(
    raw5: pd.DataFrame,
    management_featured: pd.DataFrame,
    entries: pd.DataFrame,
    management_minutes: int = 5,
) -> pd.DataFrame:
    """Preserve every entry and append its known native-5m colour state.

    Required inputs: raw5 ``open_time/open/segment_id``; management_featured
    ``open_time/ma_side``; entries ``decision_time/direction``. The entry time
    is the actual request decision_time, not its mother K1 time. All original
    columns, values, IDs, index, order and attrs survive unchanged, including
    duplicate entry timestamps/IDs. Duplicate source times fail explicitly.

    Causal columns/windows: management ma_side is the already-computed
    HL2>=MA +1 / HL2<MA -1 state from the bar OPEN exactly entry_time-5m.
    Its available_at is open+5m and MUST equal entry_time; an older available
    bar is stale, not carried forward. ma_side=0, missing, nonfinite or any
    value other than numeric +/-1 is unknown. Body OHLC is never read here.
    The raw source bar at that management OPEN and raw entry OPEN must both
    exist in the same valid SOURCE segment. Management segment counters are
    deliberately ignored: source identities are mapped by timestamp, never
    compared to independently numbered aggregate segment counters. Only the
    observable raw entry open is read; its subsequent high/low/close are not.

    Added columns:
    * ltf_entry_side: nullable Int64, +1/-1 when all context checks pass.
    * ltf_entry_aligned: nullable boolean; unknown is NA, never False.
    * ltf_entry_state: aligned / opposite / unknown relative to direction.
    * ltf_entry_bar_open / ltf_entry_available_at: UTC timestamps of the
      selected completed management row, retained for stale/gap diagnostics;
      NaT when none exists or the entry key itself is invalid.
    * ltf_entry_context_reason: known or the explicit unknown reason.

    Native 5m only: other management_minutes or a conflicting frame interval
    raise. No outcomes, lookahead, colour-transition inference, imputation,
    tuning, or data loading occur. Prefix parity requires the raw ENTRY OPEN
    row (observable then), but does not require any completed post-entry bar.
    """
    if isinstance(management_minutes, bool) or management_minutes != 5:
        raise ValueError("only native management_minutes=5 is supported")
    if management_featured.attrs.get("bar_minutes", 5) != 5:
        raise ValueError("management_featured must contain native five-minute bars")
    required = (
        (raw5, {"open_time", "open", "segment_id"}, "raw5"),
        (management_featured, {"open_time", "ma_side"}, "management_featured"),
        (entries, {"decision_time", "direction"}, "entries"),
    )
    for frame, columns, name in required:
        missing = columns - set(frame.columns)
        if missing:
            raise ValueError("%s missing columns: %s" % (name, sorted(missing)))
    collisions = set(CONTEXT_COLUMNS).intersection(entries.columns)
    if collisions:
        raise ValueError("entry colour context already present: %s" % sorted(collisions))

    raw_times = _timestamps(raw5["open_time"], "raw5.open_time", source=True)
    management_times = _timestamps(management_featured["open_time"], "management.open_time", source=True)
    entry_times = _timestamps(entries["decision_time"], "entries.decision_time", source=False)
    raw_lookup = {time: pos for pos, time in enumerate(raw_times)}
    valid_entry_time = entry_times.notna() & entry_times.eq(entry_times.dt.floor("5min"))
    query = pd.DataFrame({"entry_boundary": entry_times, "entry_row": np.arange(len(entries))})
    query = query.loc[valid_entry_time].sort_values("entry_boundary", kind="mergesort")
    context = pd.DataFrame({
        "available_at": management_times + FIVE_MINUTES,
        "management_row": np.arange(len(management_times)),
    })
    chosen = {}
    if len(query) and len(context):
        joined = pd.merge_asof(
            query, context, left_on="entry_boundary", right_on="available_at",
            direction="backward", allow_exact_matches=True,
        )
        chosen = {int(row.entry_row): row for row in joined.itertuples(index=False)}

    sides, aligned, states, opens, available, reasons = [], [], [], [], [], []
    for position, direction in enumerate(entries["direction"]):
        side, alignment, state = pd.NA, pd.NA, "unknown"
        bar_open, available_at, reason = pd.NaT, pd.NaT, "known"
        time = entry_times.iloc[position]
        selected = chosen.get(position)
        if pd.isna(time):
            reason = "invalid_entry_time"
        elif not valid_entry_time.iloc[position]:
            reason = "unaligned_entry_time"
        elif (
            isinstance(direction, (bool, np.bool_)) or not isinstance(direction, Number)
            or not np.isfinite(direction) or direction not in (-1, 1)
        ):
            reason = "invalid_entry_direction"
        elif selected is None or pd.isna(selected.management_row):
            reason = "no_completed_management_bar"
        else:
            management_position = int(selected.management_row)
            bar_open = management_times.iloc[management_position]
            available_at = bar_open + FIVE_MINUTES
            if available_at != time or bar_open != time - FIVE_MINUTES:
                reason = "stale_management_bar"
            elif time not in raw_lookup:
                reason = "missing_raw_entry_open"
            elif bar_open not in raw_lookup:
                reason = "missing_raw_context_bar"
            else:
                previous_source = raw5.iloc[raw_lookup[bar_open]]["segment_id"]
                entry_source = raw5.iloc[raw_lookup[time]]["segment_id"]
                try:
                    entry_open = float(raw5.iloc[raw_lookup[time]]["open"])
                except (ValueError, TypeError):
                    entry_open = np.nan
                source_side = management_featured.iloc[management_position]["ma_side"]
                if not _valid_segment(previous_source) or not _valid_segment(entry_source):
                    reason = "invalid_raw_source_segment"
                elif previous_source != entry_source:
                    reason = "raw_source_gap"
                elif not np.isfinite(entry_open) or entry_open <= 0:
                    reason = "invalid_raw_entry_open"
                elif isinstance(source_side, (bool, np.bool_)) or not isinstance(source_side, Number) or not np.isfinite(source_side) or source_side not in (-1, 1):
                    reason = "invalid_management_side"
                else:
                    side = int(source_side)
                    alignment = side == direction
                    state = "aligned" if alignment else "opposite"
        sides.append(side)
        aligned.append(alignment)
        states.append(state)
        opens.append(bar_open)
        available.append(available_at)
        reasons.append(reason)

    result = entries.copy()
    result["ltf_entry_side"] = pd.array(sides, dtype="Int64")
    result["ltf_entry_aligned"] = pd.array(aligned, dtype="boolean")
    result["ltf_entry_state"] = states
    result["ltf_entry_bar_open"] = pd.to_datetime(opens, utc=True)
    result["ltf_entry_available_at"] = pd.to_datetime(available, utc=True)
    result["ltf_entry_context_reason"] = reasons
    return result

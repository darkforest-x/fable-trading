"""Pure diagnostic audit of V36 native5 SMA40 colour-state exits.

Source contracts: yoyo/data/hourly_impulse.py::add_features (SMA40 of HL2,
side +1 iff HL2 >= MA), and l3_backtest/hourly_impulse.py::simulate_events
(`colour` mode tests the first completed HELD bar, without requiring a new
aligned-to-opposite transition). Neither source function is imported here.

Input trades retain all original columns/index/attrs, notably event_id,
decision_time, flow_pass, outcome, hold_minutes and net_return. Direction and
exit_time are required; optional entry_time must equal decision_time. No
return, flow gate, stop distance or future profit selects diagnostic labels.

Native features are caller-completed five-minute bars with open_time, hl2, ma,
ma_side and segment_id. SMA is supplied, not independently reconstructed: the
caller must create native5 SMA40 from the authorized prefix. Each observation
checks its completed raw OHLC/HL2 and the next boundary's raw OPEN only. An
opaque management segment is never numerically compared with raw segments.

Three exact clocks: entry uses [E-5min,E); first_postentry uses [E,E+5min);
exit uses [X-5min,X). The latter TWO groups and all post-entry flags are
diagnostic future LABELS, never entry features. For non-colour exits the exit
group describes the bar available at X, not the cause of that exit. No asof,
forward fill, clock search, I/O, resampling, policy change or execution replay.
Duplicates/malformed clocks raise; absent, stale or invalid source is unknown.
The script never treats unknown as a known false entry/transition state.
"""
from __future__ import annotations

import math
from numbers import Number

import numpy as np
import pandas as pd


FIVE = pd.Timedelta(minutes=5)
PREFIXES = ("entry", "first_postentry", "exit")
OBSERVATION_FIELDS = ("bar_open", "available_at", "hl2", "sma40", "side", "aligned",
                      "context_known", "context_reason")
FLAG_COLUMNS = ("entry_already_opposite", "first_postentry_opposite", "exited_first5m",
                "color_exit_without_new_flip", "first_postentry_new_flip", "exact_known_clocks")
AUDIT_COLUMNS = tuple(p + "_" + f for p in PREFIXES for f in OBSERVATION_FIELDS) + FLAG_COLUMNS


def _clock(value, nullable=False):
    if nullable and pd.isna(value):
        return pd.NaT
    if isinstance(value, (Number, np.bool_)):
        raise ValueError("Explicit timezone-aware five-minute clocks required")
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is None or result != result.floor("5min"):
        raise ValueError("Explicit timezone-aware five-minute clocks required")
    return result.tz_convert("UTC")


def _source(frame, required, name):
    if not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique or not set(required).issubset(frame):
        raise ValueError(name + " has missing/duplicate columns")
    result = frame.copy(deep=True)
    times = pd.DatetimeIndex([_clock(value) for value in result.open_time])
    if not times.is_unique or not times.is_monotonic_increasing:
        raise ValueError(name + " has duplicate or unordered timestamps")
    result["open_time"] = pd.array(times, dtype="datetime64[ns, UTC]")
    result["_audit_raw_segment"] = result.open_time.diff().ne(FIVE).cumsum() - 1
    return result.set_index("open_time", drop=False)


def _positive(value):
    if isinstance(value, (bool, np.bool_)):
        return False
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def _segment(value):
    if (pd.isna(value) or isinstance(value, (bool, np.bool_)) or str(value).strip() == ""
            or (isinstance(value, Number) and not math.isfinite(float(value)))):
        return None
    return str(value)


def _unknown(available, reason):
    return dict(bar_open=available-FIVE if pd.notna(available) else pd.NaT,
                available_at=available, hl2=np.nan, sma40=np.nan, side=pd.NA,
                aligned=pd.NA, context_known=False, context_reason=reason, _segment=None)


def _observe(available, direction, native, raw):
    result = _unknown(available, "missing_management_bar")
    prior = available - FIVE
    if prior not in native.index:
        return result
    if prior not in raw.index or available not in raw.index:
        result["context_reason"] = "missing_raw_boundary_or_completed_bar"
        return result
    source, boundary, feature = raw.loc[prior], raw.loc[available], native.loc[prior]
    if source._audit_raw_segment != boundary._audit_raw_segment:
        result["context_reason"] = "raw_gap"
        return result
    if not _positive(boundary.open):
        result["context_reason"] = "invalid_boundary_open"
        return result
    values = [source[c] for c in ("open", "high", "low", "close")]
    if not all(_positive(x) for x in values):
        result["context_reason"] = "invalid_completed_raw"
        return result
    o, high, low, close = map(float, values)
    if not low <= min(o, close) <= max(o, close) <= high:
        result["context_reason"] = "invalid_completed_raw"
        return result
    if not _positive(feature.hl2) or not _positive(feature.ma):
        result["context_reason"] = "unknown_sma_or_hl2"
        return result
    if not math.isclose(float(feature.hl2), (high+low)/2, rel_tol=1e-12, abs_tol=1e-12):
        result["context_reason"] = "hl2_source_mismatch"
        return result
    for column in ("open", "high", "low", "close"):
        if column in native and (not _positive(feature[column]) or not math.isclose(float(feature[column]), float(source[column]), rel_tol=1e-12, abs_tol=1e-12)):
            result["context_reason"] = "management_source_mismatch"
            return result
    side = 1 if float(feature.hl2) >= float(feature.ma) else -1
    supplied = feature.ma_side
    if isinstance(supplied, (bool, np.bool_)) or pd.isna(supplied) or supplied not in (-1, 1) or supplied != side:
        result["context_reason"] = "invalid_management_side"
        return result
    segment = _segment(feature.segment_id)
    if segment is None:
        result["context_reason"] = "unknown_management_segment"
        return result
    result.update(hl2=float(feature.hl2), sma40=float(feature.ma), side=side,
                  aligned=bool(side == direction), context_known=True,
                  context_reason="known", _segment=segment)
    return result


def audit_clock(trades, native5_featured, raw5):
    """Return all input trade rows plus exact-clock state/transition diagnostics.

    Flags are nullable booleans. entry_* outcomes never imply actual holdings.
    exact_known_clocks additionally requires the entire raw timestamp span from
    E-5min through X (inclusive) to be continuous and the same management segment
    across the three observations. It is not an independent SMA40/raw replay.
    Missing exit clocks prevent post-entry labels rather than selecting another
    observation. Original PnL and flow columns are copied, never consulted.
    """
    required = {"event_id", "decision_time", "direction", "flow_pass", "outcome", "hold_minutes", "net_return", "exit_time"}
    if not isinstance(trades, pd.DataFrame) or not trades.columns.is_unique or not required.issubset(trades):
        raise ValueError("Unique trade columns and full diagnostic identity required")
    if trades.event_id.isna().any() or not trades.event_id.is_unique or any(not isinstance(x, str) or not x.strip() for x in trades.event_id):
        raise ValueError("Unique nonempty event IDs required")
    if set(AUDIT_COLUMNS).intersection(trades.columns):
        raise ValueError("Refuse overwriting existing exit clock audit columns")
    for key, wanted in (("bar_minutes", 5), ("ma_kind", "SMA"), ("ma_length", 40)):
        if key in native5_featured.attrs and native5_featured.attrs[key] != wanted:
            raise ValueError("Expected native5 SMA40 feature contract")
    native = _source(native5_featured, ("open_time", "hl2", "ma", "ma_side", "segment_id"), "native5")
    raw = _source(raw5, ("open_time", "open", "high", "low", "close"), "raw5")
    records = []
    for trade in trades.to_dict("records"):
        e = _clock(trade["decision_time"])
        if "entry_time" in trade and pd.notna(trade["entry_time"]) and _clock(trade["entry_time"]) != e:
            raise ValueError("V36 entry_time must equal decision_time")
        direction = trade["direction"]
        if isinstance(direction, (bool, np.bool_)) or pd.isna(direction) or direction not in (-1, 1):
            raise ValueError("Direction must be numeric +1/-1")
        x = _clock(trade["exit_time"], nullable=True)
        entered = not str(trade["outcome"]).startswith("entry_")
        if pd.notna(x) and (x < e or (pd.notna(trade["hold_minutes"]) and float(trade["hold_minutes"]) != (x-e).total_seconds()/60)):
            raise ValueError("Saved holding clock mismatch")
        observations = {p: _unknown(at, "not_executed" if not entered else "no_observed_hold") for p, at in
                        (("entry", e), ("first_postentry", e+FIVE), ("exit", x))}
        if entered:
            observations["entry"] = _observe(e, direction, native, raw)
            if pd.notna(x) and x >= e + FIVE:
                observations["first_postentry"] = _observe(e+FIVE, direction, native, raw)
                observations["exit"] = _observe(x, direction, native, raw)
        before, first, exit_observation = [observations[p] for p in PREFIXES]
        first_exit = bool(x == e+FIVE) if entered and pd.notna(x) else pd.NA
        before_opposite = before["side"] == -direction if before["context_known"] else pd.NA
        first_opposite = first["side"] == -direction if first["context_known"] else pd.NA
        pair_known = before["context_known"] and first["context_known"] and before["_segment"] == first["_segment"]
        no_flip = pd.NA
        if entered and pd.notna(x) and (str(trade["outcome"]) != "colour_exit" or not first_exit):
            no_flip = False
        elif entered and pair_known and pd.notna(first_exit):
            no_flip = bool(first_exit and before_opposite and first_opposite)
        complete = all(r["context_known"] for r in observations.values())
        if complete:
            span = raw.loc[(raw.open_time >= e-FIVE) & (raw.open_time <= x), "open_time"]
            complete = (pd.DatetimeIndex(span).equals(pd.date_range(e-FIVE, x, freq="5min"))
                        and len({r["_segment"] for r in observations.values()}) == 1)
        record = {p+"_"+field: observations[p][field] for p in PREFIXES for field in OBSERVATION_FIELDS}
        record.update(entry_already_opposite=before_opposite, first_postentry_opposite=first_opposite,
                      exited_first5m=first_exit, color_exit_without_new_flip=no_flip,
                      first_postentry_new_flip=bool(before["aligned"] and first_opposite) if pair_known else pd.NA,
                      exact_known_clocks=bool(complete))
        records.append(record)
    result = trades.copy(deep=True)
    for column in AUDIT_COLUMNS:
        values = [r[column] for r in records]
        dtype = ("datetime64[ns, UTC]" if column.endswith(("_bar_open", "_available_at")) else
                 "Int64" if column.endswith("_side") else "boolean" if column in FLAG_COLUMNS or column.endswith(("_aligned", "_context_known")) else
                 "float64" if column.endswith(("_hl2", "_sma40")) else object)
        result[column] = pd.array(values, dtype=dtype)
    return result

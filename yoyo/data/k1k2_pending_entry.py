"""Pure V38 pending-entry clock audit; no price I/O or return calculation.

Requests supply event_id, decision_time E, direction (+/-1), initial_stop,
signal_atr and audit_cutoff. Source clocks are explicit timezone-aware five-minute
starts. At E only the new raw OPEN can invalidate the original stop; the complete
seed [E-5m,E) supplies colour, never a wait-stop test. Later boundaries first
inspect the complete waiting OHLC [t-5m,t), then the current OPEN, then colour.
Touching the fixed stop, including equality, precedes colour confirmation.

native5 is the caller's add_features(resample_complete(raw,5),'SMA',40) result:
OHLC, hl2, ma, ma_side, segment_id; attrs bar_minutes=5/ma_kind=SMA/ma_length=40.
HL2=(high+low)/2, side=+1 iff HL2>=MA, otherwise -1. The supplied SMA40 uses 40
complete contiguous bars; this audit checks its metadata and row/source/colour
consistency, but does NOT independently reconstruct its rolling value. ATR14,
slope and volume are not additional gates. Raw OHLC is checked only when complete;
the boundary's future high/low/close is never inspected. Segment IDs are compared
within each source domain, never numerically across raw/native domains.

The result is sequential evidence AFTER the original K2 request, not an E-time
entry feature or a backtest. pending_at_cutoff is administrative right-censoring,
not a known zero return. No expiry, cooldown, re-arming or stop replacement exists.
Only eligible rows receive reference_open/risk_pct/risk_atr; other rows retain NaN.
waiting_bars is elapsed (t-E)/5m, not a count of successfully validated source bars.
Trace state is aligned/opposite/unknown, independent of administrative cutoff, so
the same observed prefix has identical trace rows. Unvisited observations remain
unknown/NaN; no observation occurs after the first terminal status.

Pandas 2.3 metadata copying and explicit Timestamp timezone conversion:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.attrs.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Timestamp.html
"""

from __future__ import annotations

from numbers import Number, Real

import numpy as np
import pandas as pd


FIVE = pd.Timedelta(minutes=5)
REQUEST_COLUMNS = ["event_id", "decision_time", "direction", "initial_stop",
                   "signal_atr", "audit_cutoff"]
EVENT_COLUMNS = ["status", "status_reason", "status_known_at", "eligible_at",
                 "reference_open", "risk_pct", "risk_atr", "seed_state",
                 "waiting_bars", "invalidated_interval_start"]
TRACE_COLUMNS = ["event_id", "observed_at", "completed_bar_open", "state",
                 "colour_known", "side", "wait_bar_stop_touched", "boundary_open",
                 "raw_known", "raw_reason", "management_reason",
                 "management_segment_id", "raw_segment_id"]
OHLC = ["open", "high", "low", "close"]


def _clock(value, name):
    if isinstance(value, (Number, np.number)) or value is None or value is pd.NaT:
        raise ValueError(name + " must be an explicit timezone-aware timestamp")
    try:
        t = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(name + " must be a timestamp") from exc
    if pd.isna(t) or t.tzinfo is None:
        raise ValueError(name + " must have an explicit timezone")
    t = t.tz_convert("UTC")
    if t != t.floor("5min"):
        raise ValueError(name + " must be on the five-minute grid")
    return t


def _number(value):
    if isinstance(value, (bool, np.bool_)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if np.isfinite(number) else None


def _segment(value):
    if value is None or value is pd.NA or isinstance(value, (bool, np.bool_)):
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, Real):
        return value if np.isfinite(value) else None
    return None


def _schema(frame, required, name):
    if not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique:
        raise ValueError(name + " must have unique DataFrame columns")
    if set(required) - set(frame):
        raise ValueError(name + " missing columns: " + str(sorted(set(required)-set(frame))))


class _Source:
    """Index clocks only; defer source-value validation until an observation."""

    def __init__(self, frame, name):
        self.frame, self.name = frame, name
        times = pd.DatetimeIndex([_clock(t, name + ".open_time") for t in frame.open_time])
        if not times.is_monotonic_increasing:
            raise ValueError(name + " clocks must be sorted")
        self.positions = {}
        for i, t in enumerate(times):
            self.positions.setdefault(t, []).append(i)
        self.derived_segments = pd.Series(times).diff().ne(FIVE).cumsum().sub(1).to_numpy()

    def row(self, t):
        positions = self.positions.get(t, [])
        if not positions:
            return None, "missing_bar"
        if len(positions) != 1:
            return None, "duplicate_bar"
        return self.frame.iloc[positions[0]], "valid"

    def segment(self, row, t):
        value = row["segment_id"] if "segment_id" in self.frame else self.derived_segments[self.positions[t][0]]
        return _segment(value)


def _ohlc(row):
    values = [_number(row[name]) for name in OHLC]
    if any(value is None or value <= 0 for value in values):
        return None
    o, h, low, c = values
    return values if low <= min(o, c) and h >= max(o, c) and low <= h else None


def _management(native, t, raw_values, previous_segment):
    row, reason = native.row(t)
    if row is None:
        return None, None, reason
    segment = native.segment(row, t)
    if segment is None:
        return None, None, "unknown_segment"
    if previous_segment is not None and segment != previous_segment:
        return None, segment, "segment_change"
    values = _ohlc(row)
    if values is None:
        return None, segment, "invalid_ohlc"
    if not np.allclose(values, raw_values, rtol=1e-12, atol=1e-12):
        return None, segment, "source_ohlc_mismatch"
    hl2, ma, side = (_number(row[name]) for name in ["hl2", "ma", "ma_side"])
    if hl2 is None or ma is None or ma <= 0 or side not in (-1, 1):
        return None, segment, "unknown_colour"
    source_hl2 = (raw_values[1] + raw_values[2]) / 2
    if not np.isclose(hl2, source_hl2, rtol=1e-12, atol=1e-12):
        return None, segment, "source_hl2_mismatch"
    # The side check is strict at equality; tolerance is only for source parity.
    if side != (1 if hl2 >= ma else -1) or side != (1 if source_hl2 >= ma else -1):
        return None, segment, "source_side_mismatch"
    return int(side), segment, "valid"


def audit_pending_entries(requests, native5, raw5):
    """Return all requests plus terminal evidence, and actual boundary visits.

    statuses: eligible, invalidated_open, invalidated_wait_bar, unknown_raw,
    unknown_management, pending_at_cutoff. Events preserve original fields,
    order, index and attrs; added names are EVENT_COLUMNS. Trace uses TRACE_COLUMNS.
    Structural schema/clock/metadata errors raise. Missing/duplicate observed bars,
    invalid prices/colours, or changed observed segments terminate the individual
    event as unknown. Source values in unvisited future rows cannot alter an event.
    """
    _schema(requests, REQUEST_COLUMNS, "requests")
    _schema(raw5, ["open_time"]+OHLC, "raw5")
    _schema(native5, ["open_time"]+OHLC+["hl2", "ma", "ma_side", "segment_id"], "native5")
    if set(EVENT_COLUMNS) & set(requests):
        raise ValueError("requests already contain pending-audit output columns")
    if (any(not isinstance(value, str) or not value.strip() for value in requests.event_id)
            or requests.event_id.duplicated().any()):
        raise ValueError("event_id must be nonempty strings and unique")
    expected_attrs = {"bar_minutes": 5, "ma_kind": "SMA", "ma_length": 40}
    if any(isinstance(native5.attrs.get(k), (bool, np.bool_)) or native5.attrs.get(k) != v
           for k, v in expected_attrs.items()):
        raise ValueError("native5 attrs must declare five-minute SMA40")
    if "bar_minutes" in raw5.attrs and (isinstance(raw5.attrs["bar_minutes"], (bool, np.bool_))
                                       or raw5.attrs["bar_minutes"] != 5):
        raise ValueError("raw5 bar_minutes must be five")
    prepared = []
    for row in requests.to_dict("records"):
        entry, cutoff = (_clock(row[k], k) for k in ["decision_time", "audit_cutoff"])
        direction = row["direction"]
        if isinstance(direction, (bool, np.bool_)) or not isinstance(direction, Real) or direction not in (-1, 1):
            raise ValueError("direction must be numeric +1 or -1, not boolean")
        stop, atr = _number(row["initial_stop"]), _number(row["signal_atr"])
        if stop is None or stop <= 0 or atr is None or atr <= 0 or cutoff < entry:
            raise ValueError("positive finite stop/ATR and cutoff >= decision_time required")
        prepared.append((row["event_id"], entry, cutoff, int(direction), stop, atr))
    raw, native = _Source(raw5, "raw5"), _Source(native5, "native5")
    outcomes, visits = [], []
    for event_id, entry, cutoff, direction, stop, atr in prepared:
        result = dict(status=None, status_reason=None, status_known_at=pd.NaT,
                      eligible_at=pd.NaT, reference_open=np.nan, risk_pct=np.nan,
                      risk_atr=np.nan, seed_state="unknown", waiting_bars=0,
                      invalidated_interval_start=pd.NaT)
        previous_raw_segment = previous_management_segment = None
        now = entry
        while now <= cutoff:
            completed = now-FIVE
            trace = dict(event_id=event_id, observed_at=now, completed_bar_open=completed,
                         state="unknown", colour_known=False, side=pd.NA,
                         wait_bar_stop_touched=pd.NA, boundary_open=np.nan,
                         raw_known=False, raw_reason="not_observed",
                         management_reason="not_observed", management_segment_id=pd.NA,
                         raw_segment_id=pd.NA)
            result["waiting_bars"] = int((now-entry)/FIVE)
            status, reason = None, None
            values = source_segment = None
            # The original request's seed is deliberately not a waiting interval.
            if now > entry:
                bar, reason = raw.row(completed)
                source_segment = raw.segment(bar, completed) if bar is not None else None
                values = _ohlc(bar) if bar is not None else None
                if bar is not None and source_segment is None:
                    reason = "unknown_segment"
                elif bar is not None and source_segment != previous_raw_segment:
                    reason = "segment_change"
                elif bar is not None and values is None:
                    reason = "invalid_completed_ohlc"
                if reason != "valid":
                    status, trace["raw_reason"] = "unknown_raw", reason
                else:
                    trace.update(raw_known=True, raw_reason="valid", raw_segment_id=source_segment)
                    touched = values[2] <= stop if direction == 1 else values[1] >= stop
                    trace["wait_bar_stop_touched"] = bool(touched)
                    if touched:
                        status, reason = "invalidated_wait_bar", "fixed_stop_touched"
                        result["invalidated_interval_start"] = completed
            if status is None:
                boundary, reason = raw.row(now)
                boundary_segment = raw.segment(boundary, now) if boundary is not None else None
                price = _number(boundary["open"]) if boundary is not None else None
                if boundary is not None and (price is None or price <= 0):
                    reason = "invalid_boundary_open"
                elif boundary is not None and boundary_segment is None:
                    reason = "unknown_segment"
                elif now > entry and boundary is not None and boundary_segment != source_segment:
                    reason = "segment_change"
                if price is not None:
                    trace["boundary_open"] = price
                if reason != "valid":
                    status, trace["raw_reason"] = "unknown_raw", reason
                elif direction*(price-stop) <= 0:
                    status, reason = "invalidated_open", "fixed_stop_at_open"
                    trace.update(raw_known=True, raw_reason="valid", raw_segment_id=boundary_segment)
                else:
                    # Seed source must exist, but its high/low never invalidate.
                    if now == entry:
                        seed, reason = raw.row(completed)
                        source_segment = raw.segment(seed, completed) if seed is not None else None
                        values = _ohlc(seed) if seed is not None else None
                        if seed is not None and values is None:
                            reason = "invalid_completed_ohlc"
                        elif seed is not None and source_segment is None:
                            reason = "unknown_segment"
                        elif seed is not None and source_segment != boundary_segment:
                            reason = "segment_change"
                    if reason != "valid":
                        status, trace["raw_reason"] = "unknown_raw", reason
                    else:
                        trace.update(raw_known=True, raw_reason="valid", raw_segment_id=boundary_segment)
                        side, mg_segment, reason = _management(native, completed, values, previous_management_segment)
                        trace.update(management_reason=reason, management_segment_id=mg_segment)
                        if side is None:
                            status = "unknown_management"
                        else:
                            state = "aligned" if side == direction else "opposite"
                            trace.update(state=state, colour_known=True, side=side)
                            if now == entry:
                                result["seed_state"] = state
                            if side == direction:
                                risk = direction*(price-stop)
                                ratios = risk/price, risk/atr
                                if not all(np.isfinite(v) and v > 0 for v in ratios):
                                    status, reason = "unknown_raw", "nonfinite_risk"
                                else:
                                    status, reason = "eligible", "aligned_positive_risk"
                                    result.update(eligible_at=now, reference_open=price,
                                                  risk_pct=ratios[0], risk_atr=ratios[1])
                            elif now == cutoff:
                                status, reason = "pending_at_cutoff", "known_opposite_at_cutoff"
                            previous_raw_segment = boundary_segment
                            previous_management_segment = mg_segment
            if status == "unknown_raw":
                trace.update(raw_known=False, raw_reason=reason)
            visits.append(trace)
            if status is not None:
                result.update(status=status, status_reason=reason, status_known_at=now)
                break
            now += FIVE
        outcomes.append(result)
    events = requests.copy(deep=True)
    for name in EVENT_COLUMNS:
        values = [row[name] for row in outcomes]
        if name in ["status_known_at", "eligible_at", "invalidated_interval_start"]:
            events[name] = pd.array(values, dtype="datetime64[ns, UTC]")
        else:
            events[name] = values
    trace = pd.DataFrame(visits, columns=TRACE_COLUMNS)
    for name in ["observed_at", "completed_bar_open"]:
        trace[name] = pd.array(trace[name], dtype="datetime64[ns, UTC]")
    for name in ["colour_known", "raw_known", "wait_bar_stop_touched"]:
        trace[name] = pd.array(trace[name], dtype="boolean")
    trace["side"] = pd.array(trace["side"], dtype="Int64")
    return events, trace

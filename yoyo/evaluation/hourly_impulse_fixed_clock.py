"""Fixed-clock observation labels, not a stop-constrained trading simulator.

Inputs used: raw ``open_time/open`` and request
``event_id/decision_time/direction/fold`` only. For E = decision_time, each
H in (1, 4, 12, 24) hours requires every native 5-minute timestamp and finite
positive open in the inclusive window [E, E+H]. The label is
direction * (open[E+H] - open[E]) / open[E]. Its 20bp-subtracted companion is
only a cost-threshold markout: no K1 stop, old exit, colour, MFE, sizing,
funding, slippage or execution feasibility enters this calculation. Future
quotes are labels, never causal entry features. Four labels per mother are
repeated observations, not four independent samples; only 4h is primary.

Version contracts: Python 3.9 Decimal constructs from quote strings before
arithmetic, avoiding binary-float pollution at exactly 20bp:
https://docs.python.org/3.9/library/decimal.html#decimal.Decimal
Pandas 2.3.3 explicit fixed-frequency inclusive grids and timestamp semantics:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.date_range.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Timestamp.html
No I/O, aggregation, nearest joins, forward fills or strategy imports occur.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Context, Decimal, DecimalException, localcontext
import math
from numbers import Number, Real

import numpy as np
import pandas as pd


HORIZONS_HOURS = (1, 4, 12, 24)
PRIMARY_HORIZON_HOURS = 4
COST_THRESHOLD = Decimal("0.002")
REQUEST_COLUMNS = ("event_id", "decision_time", "direction", "fold")
LABEL_COLUMNS = (
    "event_id", "fold", "direction", "decision_time", "endpoint_time",
    "horizon_hours", "role", "status", "reason", "gross_markout",
    "cost_threshold_markout", "entry_open", "endpoint_open", "n_expected", "n_observed",
)
_FIVE_MINUTES_NS = 5 * 60 * 10**9


def _utc_grid_time(value, name):
    """Require explicit zero-offset clocks; never infer epoch units or timezone."""
    if isinstance(value, (Number, np.bool_)) or not isinstance(value, (str, datetime, pd.Timestamp)):
        raise ValueError("%s requires an explicit UTC timestamp, not an epoch" % name)
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
            raise ValueError("not an explicit UTC timestamp")
        stamp = stamp.tz_convert("UTC").as_unit("ns")
        if stamp.value % _FIVE_MINUTES_NS:
            raise ValueError("not aligned to the five-minute grid")
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("%s must be a valid UTC five-minute timestamp" % name) from error
    return stamp


def _fold_bounds(fold_ends):
    if not isinstance(fold_ends, Mapping) or not fold_ends:
        raise ValueError("fold_ends must map fold names to explicit (start, end) pairs")
    result = {}
    for name, bounds in fold_ends.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
            raise ValueError("each fold requires a nonempty name and (start, end)")
        start, end = (_utc_grid_time(value, "fold boundary") for value in bounds)
        if start >= end:
            raise ValueError("fold start must precede its end")
        result[name] = (start, end)
    ordered = sorted(result.values())
    if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("fold intervals must not overlap")
    return result


def _quote(value):
    """Invalid quotes become unknown labels only in windows containing them."""
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (Real, Decimal, str)):
        return None
    try:
        price = Decimal(str(value))
        if not price.is_finite() or price <= 0:
            return None
        numerical = float(price)
        return price if math.isfinite(numerical) and numerical > 0 else None
    except (DecimalException, TypeError, ValueError, OverflowError):
        return None


def build_fixed_clock_labels(raw5, requests, fold_ends):
    """Return all mother-clock labels in request order then HORIZONS_HOURS order.

    ``fold_ends`` is explicitly ``{name: (start_inclusive, end_exclusive)}``;
    both boundaries must be aware UTC, five-minute aligned, and nonoverlapping.
    E must belong to its named fold. E+H must be strictly below its fold end;
    a boundary-crossing label is unknown even if the supplied data has quotes.

    Raw timestamps must be sorted, unique, UTC and five-minute aligned. Missing
    bars or invalid opens are not globally rejected: affected labels are kept
    with ``status='unknown'`` and a reason. Unknown reasons have precedence
    endpoint_outside_fold, missing_bar, invalid_open, nonfinite_markout.
    ``n_expected`` counts both endpoints (12*H+1). ``n_observed`` counts actual
    timestamps, including present invalid opens; it is NA when the fold guard
    prevents observing the window. For that guard both quotes remain unknown.
    Otherwise finite endpoint quotes may be retained for diagnostics even if
    an interior gap/invalid quote makes the whole label unknown.

    Only the four request identity columns are projected, so arbitrary old
    outcome/exit/stop/MFE/colour columns cannot affect even the output schema.
    Inputs are not mutated. Output uses a new RangeIndex and a fixed schema,
    including for empty requests. No rows are dropped for missing labels.
    """
    if not isinstance(raw5, pd.DataFrame) or not isinstance(requests, pd.DataFrame):
        raise ValueError("raw5 and requests must be dataframes")
    if raw5.columns.duplicated().any() or requests.columns.duplicated().any():
        raise ValueError("duplicate input column names")
    if not {"open_time", "open"} <= set(raw5) or not set(REQUEST_COLUMNS) <= set(requests):
        raise ValueError("missing required raw or request columns")
    folds = _fold_bounds(fold_ends)
    times = pd.DatetimeIndex([_utc_grid_time(value, "raw open_time") for value in raw5["open_time"]], tz="UTC")
    if not times.is_unique or not times.is_monotonic_increasing:
        raise ValueError("raw timestamps must be sorted and unique")
    positions = {time: index for index, time in enumerate(times)}
    opens = raw5["open"].tolist()
    source = requests.loc[:, list(REQUEST_COLUMNS)].to_dict("records")
    seen = set()
    for request in source:
        event = request["event_id"]
        if not isinstance(event, str) or not event.strip() or event in seen:
            raise ValueError("event_id must be a unique nonempty string")
        seen.add(event)
        direction, fold = request["direction"], request["fold"]
        if isinstance(direction, (bool, np.bool_)) or not isinstance(direction, Real) or direction not in (-1, 1):
            raise ValueError("direction must be numeric +1/-1, not boolean")
        if not isinstance(fold, str) or fold not in folds:
            raise ValueError("request has an unknown fold")
        entry = _utc_grid_time(request["decision_time"], "decision_time")
        if not folds[fold][0] <= entry < folds[fold][1]:
            raise ValueError("decision_time is outside its named fold")
        request.update(decision_time=entry, direction=int(direction))

    rows = []
    for request in source:
        entry = request["decision_time"]
        for hours in HORIZONS_HOURS:
            endpoint = entry + pd.Timedelta(hours=hours)
            row = dict(request, endpoint_time=endpoint, horizon_hours=hours,
                       role="primary" if hours == PRIMARY_HORIZON_HOURS else "descriptive",
                       status="unknown", reason="endpoint_outside_fold", gross_markout=np.nan,
                       cost_threshold_markout=np.nan, entry_open=np.nan, endpoint_open=np.nan,
                       n_expected=12*hours+1, n_observed=pd.NA)
            rows.append(row)
            if endpoint >= folds[request["fold"]][1]:
                continue
            window = pd.date_range(entry, endpoint, freq="5min", inclusive="both", unit="ns")
            present = [positions.get(time) for time in window]
            row["n_observed"] = sum(index is not None for index in present)
            quotes = [_quote(opens[index]) if index is not None else None for index in present]
            for field, price in (("entry_open", quotes[0]), ("endpoint_open", quotes[-1])):
                if price is not None:
                    row[field] = float(price)
            if row["n_observed"] != row["n_expected"]:
                row["reason"] = "missing_bar"
                continue
            if any(price is None for price in quotes):
                row["reason"] = "invalid_open"
                continue
            # Local fixed precision makes this independent of caller Decimal
            # settings. Subtract the threshold before converting to float.
            try:
                with localcontext(Context(prec=60)):
                    gross = Decimal(request["direction"]) * (quotes[-1]-quotes[0]) / quotes[0]
                    gross_float, cost_float = float(gross), float(gross-COST_THRESHOLD)
                if not math.isfinite(gross_float) or not math.isfinite(cost_float):
                    raise ValueError("markout outside finite float range")
            except (DecimalException, ValueError, OverflowError):
                row["reason"] = "nonfinite_markout"
                continue
            row.update(status="known", reason="known", gross_markout=gross_float,
                       cost_threshold_markout=cost_float)
    result = pd.DataFrame(rows, columns=LABEL_COLUMNS)
    for field in ("decision_time", "endpoint_time"):
        result[field] = pd.to_datetime(result[field], utc=True)
    for field in ("direction", "horizon_hours", "n_expected", "n_observed"):
        result[field] = result[field].astype("Int64")
    for field in ("gross_markout", "cost_threshold_markout", "entry_open", "endpoint_open"):
        result[field] = result[field].astype(float)
    result.attrs.update(label_only=True, executable_pnl=False, primary_horizon_hours=4,
                        horizons_hours=HORIZONS_HOURS, cost_threshold_fraction=.002,
                        observations_per_mother=4, independent_samples=False)
    return result

"""Pure conservative point-in-time joins for one instrument's derivatives data.

Source schema: ``yoyo.data.okx_altcoin_snapshot.COLUMNS``. The IO adapter MUST
convert event_time/available_at_nominal/funding_time from explicit Unix-ms to
timezone-aware UTC datetime columns before calling this module. inst_id must
match the requested instrument on every row. No mixed-symbol joins, implicit
timestamp-unit inference, HTTP, model, outcomes or live state occur here.

OI and taker rows describe four-hour buckets [event_time, event_time + 4h).
The collector's nominal availability is bucket end; this research adds another
full four-hour publication lag. At each decision CLOSE, only availability <=
close may be joined. An age of exactly 8h after that availability is allowed;
older rows retain their provenance but their numeric features are NaN. This
is an assumed conservative availability policy, NOT evidence of when a past
HTTP response was first observable. Historical publication times are unknown.

OI changes compare the selected row with exact event_time-4h or -24h endpoints,
never the preceding row by position. log(OI_base[t])-log(OI_base[t-horizon])
and the separate USD-notional diagnostic are undefined at nonpositive or
missing endpoints. Rising OI alone establishes neither buying nor direction;
USD OI can rise solely because price changes. Taker buy share is buy/(buy+sell),
and imbalance (buy-sell)/(buy+sell), for the selected fully lagged bucket.

Funding features use realized settlement rates only, at funding_time + 1h.
The latest available settlement may itself have a missing realized_rate: an
older actual or a predicted_rate does NOT replace it. Trailing 24h sums use
settlements in (decision_close-24h, decision_close] which have passed the lag.
The sum stays NaN if there are no actual observations, an observed settlement
lacks its actual, history starts inside the window, or the latest source row
is stale. Counts and source-span coverage are explicit. Source-span coverage
does not certify endpoint completeness or an unchanging settlement schedule.
Actual zero and negative funding rates are valid. These lagged INPUT features
must not be used as settlement timestamps in a separate realized-cost ledger.

Inputs may have temporal gaps, which remain gaps; no backfill or interpolation.
Missing values stay missing, negative OI/volume and infinities are rejected.
All feature windows and availability lags are fixed; there are no fitted gates.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


BUCKET = pd.Timedelta(hours=4)
PUBLICATION_LAG = pd.Timedelta(hours=4)
FUNDING_LAG = pd.Timedelta(hours=1)
MAX_STALENESS = pd.Timedelta(hours=8)
FUNDING_WINDOW = pd.Timedelta(hours=24)
_UTC_NAMES = ("UTC", "Etc/UTC", "GMT", "Etc/GMT", "UTC+00:00")


def _utc_index(values: pd.DatetimeIndex, name: str) -> pd.DatetimeIndex:
    """Require explicit finite unique chronological UTC timestamps."""
    if not isinstance(values, pd.DatetimeIndex) or str(values.tz) not in _UTC_NAMES:
        raise ValueError(f"{name} must be a UTC DatetimeIndex")
    if values.hasnans or not values.is_unique or not values.is_monotonic_increasing:
        raise ValueError(f"{name} must be finite, unique and increasing")
    return values.as_unit("ns")


def _time_column(frame: pd.DataFrame, column: str) -> pd.DatetimeIndex:
    """Reject Unix integers and strings: the caller owns their explicit adapter."""
    series = frame[column]
    if not isinstance(series.dtype, pd.DatetimeTZDtype):
        raise ValueError(f"{column} must be a standardized UTC datetime column")
    return _utc_index(pd.DatetimeIndex(series), column)


def _number_column(frame: pd.DataFrame, name: str, nonnegative: bool) -> np.ndarray:
    try:
        values = pd.to_numeric(frame[name], errors="raise").to_numpy(dtype=float, na_value=np.nan)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain numeric or missing values") from error
    if np.isinf(values).any() or (nonnegative and np.any(values < 0)):
        raise ValueError(f"{name} contains invalid negative or infinite values")
    return values


def _source(frame: pd.DataFrame | None, kind: str, inst_id: str) -> pd.DataFrame:
    """Validate exact collector clock identities and return an isolated copy."""
    numeric = {"oi": ("oi_base", "oi_usd"), "taker": ("buy_base", "sell_base"),
               "funding": ("realized_rate",)}[kind]
    required = {"inst_id", "event_time", "available_at_nominal", *numeric}
    if kind == "funding":
        required |= {"funding_time", "rate_status"}
    if frame is None:
        return pd.DataFrame()
    if not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique or not required.issubset(frame.columns):
        raise ValueError(f"{kind} requires unique columns {sorted(required)}")
    if frame.empty:
        return pd.DataFrame()
    if frame.inst_id.isna().any() or not frame.inst_id.eq(inst_id).all():
        raise ValueError(f"{kind} rows must all belong to {inst_id}")
    times = _time_column(frame, "event_time")
    nominal = _time_column(frame, "available_at_nominal")
    expected = times if kind == "funding" else times + BUCKET
    if not nominal.equals(expected):
        raise ValueError(f"{kind} nominal availability does not match its event clock")
    if kind != "funding" and np.any(times.asi8 % BUCKET.value):
        raise ValueError(f"{kind} event times must align to four-hour bucket starts")
    out = pd.DataFrame({"event_time": times, "available_at": nominal + (FUNDING_LAG if kind == "funding" else PUBLICATION_LAG)})
    for name in numeric:
        out[name] = _number_column(frame, name, nonnegative=kind != "funding")
    if kind == "funding":
        settlement = _time_column(frame, "funding_time")
        if not settlement.equals(times):
            raise ValueError("funding_time must equal event_time")
        known = np.isfinite(out.realized_rate.to_numpy())
        expected_status = np.where(known, "actual_available", "predicted_only")
        if not np.array_equal(frame.rate_status.to_numpy(), expected_status):
            raise ValueError("funding rate_status disagrees with realized_rate availability")
    return out


def _latest(source: pd.DataFrame, decisions: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray, dict]:
    """Backward-only join; retain old provenance but mask features when stale."""
    n = len(decisions)
    row = np.full(n, -1, dtype=int)
    event_ns = np.full(n, pd.NaT.value, dtype=np.int64)
    available_ns = event_ns.copy()
    age = np.full(n, np.nan)
    if not source.empty:
        source_available = pd.DatetimeIndex(source.available_at).asi8
        row = np.searchsorted(source_available, decisions.asi8, side="right") - 1
        found = row >= 0
        event_ns[found] = pd.DatetimeIndex(source.event_time).asi8[row[found]]
        available_ns[found] = source_available[row[found]]
        age[found] = (decisions.asi8[found] - available_ns[found]) / pd.Timedelta(hours=1).value
    fresh = (row >= 0) & (age <= MAX_STALENESS / pd.Timedelta(hours=1))
    metadata = dict(event_time=pd.to_datetime(event_ns, utc=True),
                    available_at=pd.to_datetime(available_ns, utc=True),
                    age_hours=age, fresh=fresh)
    return row, fresh, metadata


def _take(values: np.ndarray, row: np.ndarray, fresh: np.ndarray) -> np.ndarray:
    result = np.full(len(row), np.nan)
    if len(values):
        result[fresh] = values[row[fresh]]
    return result


def _endpoint_log_change(source: pd.DataFrame, column: str, hours: int) -> np.ndarray:
    """Use exact calendar endpoints; missing buckets cannot shorten a horizon."""
    values = source[column].to_numpy()
    times = pd.DatetimeIndex(source.event_time).asi8
    target = times - pd.Timedelta(hours=hours).value
    previous = np.searchsorted(times, target)
    safe = np.minimum(previous, len(times) - 1)
    known = (times[safe] == target) & np.isfinite(values) & (values > 0)
    known &= np.isfinite(values[safe]) & (values[safe] > 0)
    result = np.full(len(source), np.nan)
    result[known] = np.log(values[known]) - np.log(values[safe[known]])
    return result


def build_derivative_features(
    decision_closes: pd.DatetimeIndex,
    *,
    inst_id: str,
    oi: pd.DataFrame | None = None,
    taker: pd.DataFrame | None = None,
    funding: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Join known one-instrument inputs to 1H/4H decision CLOSE timestamps.

    Close timestamps may be a sparse candidate subset but must be UTC-hour
    aligned, unique and chronological. Missing sources yield missing features
    and fresh=False. Input frames are never modified. See the module docstring
    for the fixed lag/window contracts and the source/publication limitation.
    """
    if not isinstance(inst_id, str) or not inst_id.strip():
        raise ValueError("inst_id must be a nonempty instrument identifier")
    decisions = _utc_index(decision_closes, "decision_closes")
    if np.any(decisions.asi8 % pd.Timedelta(hours=1).value):
        raise ValueError("decision closes must align to UTC hourly boundaries")
    sources = {kind: _source(frame, kind, inst_id) for kind, frame in (("oi", oi), ("taker", taker), ("funding", funding))}
    out = pd.DataFrame(index=decisions.copy())
    n = len(out)
    for kind, source in sources.items():
        row, fresh, metadata = _latest(source, decisions)
        for name, values in metadata.items():
            out[f"{kind}_{name}"] = values
        if kind == "oi":
            for column in ("oi_base", "oi_usd"):
                out[column] = np.nan if source.empty else _take(source[column].to_numpy(), row, fresh)
                for hours in (4, 24):
                    change = np.full(n, np.nan) if source.empty else _take(_endpoint_log_change(source, column, hours), row, fresh)
                    out[f"{column}_log_change_{hours}h"] = change
        elif kind == "taker":
            buy = np.full(n, np.nan) if source.empty else _take(source.buy_base.to_numpy(), row, fresh)
            sell = np.full(n, np.nan) if source.empty else _take(source.sell_base.to_numpy(), row, fresh)
            total = buy + sell
            positive = np.isfinite(total) & (total > 0)
            out["taker_buy_share"] = np.divide(buy, total, out=np.full(n, np.nan), where=positive)
            out["taker_imbalance"] = np.divide(buy - sell, total, out=np.full(n, np.nan), where=positive)
        else:
            out["funding_last_rate"] = np.nan if source.empty else _take(source.realized_rate.to_numpy(), row, fresh)
            trailing_sum = np.full(n, np.nan)
            actual_count = np.full(n, np.nan)
            missing_count = np.full(n, np.nan)
            span = np.zeros(n, dtype=bool)
            if not source.empty:
                rates = source.realized_rate.to_numpy()
                known = np.isfinite(rates)
                sum_prefix = np.r_[0., np.cumsum(np.where(known, rates, 0.))]
                count_prefix = np.r_[0, np.cumsum(known)]
                times = pd.DatetimeIndex(source.event_time).asi8
                left = np.searchsorted(times, decisions.asi8 - FUNDING_WINDOW.value, side="right")
                right = row + 1
                usable = fresh & (right >= left)
                actual_count[usable] = count_prefix[right[usable]] - count_prefix[left[usable]]
                missing_count[usable] = right[usable] - left[usable] - actual_count[usable]
                span = (row >= 0) & (times[0] <= decisions.asi8 - FUNDING_WINDOW.value)
                complete = usable & span & (actual_count > 0) & (missing_count == 0)
                trailing_sum[complete] = sum_prefix[right[complete]] - sum_prefix[left[complete]]
            out["funding_24h_sum"] = trailing_sum
            out["funding_24h_actual_count"] = actual_count
            out["funding_24h_missing_count"] = missing_count
            out["funding_history_covers_24h"] = span
    out.attrs["historical_publication_time_known"] = False
    out.attrs["availability_policy"] = "4H buckets: event + 4H nominal + 4H lag; funding: settlement + 1H; maximum source age 8H"
    out.attrs["funding_span_is_not_completeness_proof"] = True
    return out

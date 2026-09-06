"""Pure first-K2 requests for a fixed hourly K1 mother cohort.

Geometry and strict intermediate-close/HL2-colour rules are transcribed from
``experiments/active/exp-btcusdtp-1h-owner-causal-v2-preholdout-20260904-v1/``
``pine/fable_k1_k2_owner_causal_v2.pine`` (f_findBestK1, lines 98--171).
Unlike that retrospective best-K1 search, each supplied mother is immutable and
can emit only its first qualifying K2. The registered new default gap is 1--8
hours, explicitly different from the source's 2--8. No fills, exit labels,
fee/risk gates, cooldowns, or wait-time K1-stop cancellation are implemented.

UTC conversion follows the repository's pandas 2.3.3 contract:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.to_datetime.html
Numeric epochs must be normalized with an explicit unit by the caller.
"""

from __future__ import annotations

from numbers import Number
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd


HOUR = pd.Timedelta(hours=1)
FIVE_MINUTES = pd.Timedelta(minutes=5)
STATUS_COLUMNS = [
    "event_id", "mother_signal_time", "mother_decision_time", "mother_deadline",
    "waiting_deadline", "terminal_time", "status", "wait_hours", "k2_time", "reason",
]
REQUEST_COLUMNS = [
    "mother_signal_time", "mother_decision_time", "mother_deadline",
    "waiting_deadline", "k2_time", "k2_initial_stop", "k2_atr", "k2_ma",
    "k2_open", "k2_high", "k2_low", "k2_close", "k2_wick_share",
    "k2_body_share", "k2_rejection_location", "k2_touch_depth_atr", "wait_hours",
]


def _times(values: pd.Series, name: str, *, aligned: str, unique: bool) -> pd.Series:
    """Normalize explicit timestamps without guessing numeric epoch units."""
    if len(values) and (
        pd.api.types.is_numeric_dtype(values.dtype)
        or values.map(lambda value: isinstance(value, Number)).any()
    ):
        raise ValueError("normalize numeric %s epochs with an explicit unit first" % name)
    result = pd.to_datetime(values, utc=True, errors="raise")
    if result.isna().any() or not result.eq(result.dt.floor(aligned)).all():
        raise ValueError("%s must contain non-null UTC-aligned timestamps" % name)
    if unique and (not result.is_monotonic_increasing or result.duplicated().any()):
        raise ValueError("%s must be monotonic and unique" % name)
    return result


def _k2_geometry(row: Dict[str, Any], direction: int) -> Tuple[bool, Dict[str, float]]:
    """Current completed OHLC/MA/ATR only; exact owner-Pine rejection geometry."""
    o, h, low, c, ma, atr = [float(row[name]) for name in ("open", "high", "low", "close", "ma", "atr")]
    values = [o, h, low, c, ma, atr]
    if not np.isfinite(values).all() or atr <= 0 or h <= low:
        return False, {}
    span = h - low
    body_share = abs(c - o) / span
    if direction == 1:
        wick = min(o, c) - low
        rejection = (c - low) / span
        touch = (ma - low) / atr
        close_side = (c - ma) / atr
        body_side = min(o, c) >= ma
    else:
        wick = h - max(o, c)
        rejection = (h - c) / span
        touch = (h - ma) / atr
        close_side = (ma - c) / atr
        body_side = max(o, c) <= ma
    geometry = {
        "k2_wick_share": wick / span, "k2_body_share": body_share,
        "k2_rejection_location": rejection, "k2_touch_depth_atr": touch,
    }
    qualified = (
        geometry["k2_wick_share"] >= 0.25 and body_share <= 0.50
        and rejection >= 0.25 and 0.0 <= touch <= 1.50
        and close_side >= 0.0 and body_side
    )
    return bool(qualified), geometry


def build_entry_requests(
    hourly_featured: pd.DataFrame,
    raw5: pd.DataFrame,
    mothers: pd.DataFrame,
    *,
    observed_through: Any,
    gap_min: int = 1,
    gap_max: int = 8,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return first-K2 requests plus one terminal record for EVERY mother.

    Mother inputs: unique event_id, signal_time (K1 open), decision_time (K1
    close), direction +/-1, initial_stop, signal_atr, and optional K1 features.
    Mothers are not reselected and may share timestamps. Their initial risk is
    not validated here: only the execution layer can validate the future fill.

    Causal columns/windows: the supplied hourly SMA40(HL2), RMA14 ATR, OHLC and
    segment_id use each completed bar and its own past. Each fixed mother scans
    exact hours K1+1 through K1+gap_max, never compressed dataframe offsets.
    Every raw five-minute timestamp from mother decision through the candidate
    close (exclusive bar-open bound) must exist; hourly/raw gaps censor waiting.
    During a partial hour, only five-minute bars already closed by
    observed_through can establish a gap. No hourly price or feature after an
    episode's terminal event is consulted.

    A candidate satisfying owner geometry is tested BEFORE its own wrong-side
    colour; only a failed candidate becomes an intermediate bar. An opposite
    K2 HL2 colour can therefore be valid. Failed candidates with wrong-side
    close or colour terminate the mother; no later K1/K2 can rescue it. Waiting
    never becomes an open position, and touching the K1 extreme while flat does
    not cancel the episode. Only completed K2 bars can emit requests.

    Requests keep the original event_id, initial_stop, signal_atr, and other K1
    feature columns. signal_time/decision_time become K2 open/close; explicit
    mother_* timestamps preserve their original meanings. All K2 OHLC/geometry
    are separately prefixed k2_. mother_deadline is always ORIGINAL decision
    +72h, not delayed entry+72h. The driver must enforce this common horizon.
    k2_initial_stop/k2_atr support the separately registered stop-only contrast.

    Status is request_emitted, invalidated_wrong_close,
    invalidated_ma_colour, expired_no_k2, data_gap, or waiting_censored.
    request_emitted means only an order request, never an executed trade.
    A truncated prefix is waiting_censored, not a zero-return expiration.
    No input dataframe is modified and no file or outcome data is opened.
    """
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (gap_min, gap_max)):
        raise ValueError("gap_min and gap_max must be integers")
    if not 1 <= gap_min <= gap_max <= 8:
        raise ValueError("require 1 <= gap_min <= gap_max <= 8")
    if isinstance(observed_through, Number):
        raise ValueError("normalize numeric observed_through with an explicit unit first")
    cutoff = pd.to_datetime(observed_through, utc=True, errors="raise")
    if not isinstance(cutoff, pd.Timestamp) or pd.isna(cutoff):
        raise ValueError("observed_through must be one non-null timestamp")

    required_h = {"open_time", "open", "high", "low", "close", "ma", "atr", "segment_id"}
    required_m = {"event_id", "signal_time", "decision_time", "direction", "initial_stop", "signal_atr"}
    if not required_h.issubset(hourly_featured) or not required_m.issubset(mothers) or "open_time" not in raw5:
        raise ValueError("missing hourly, raw5, or mother input columns")
    for attr, expected in (("bar_minutes", 60), ("ma_kind", "SMA"), ("ma_length", 40)):
        if hourly_featured.attrs.get(attr, expected) != expected:
            raise ValueError("hourly_featured must use native one-hour SMA40(HL2)")
    hourly = hourly_featured.copy().reset_index(drop=True)
    hourly["open_time"] = _times(hourly["open_time"], "hourly open_time", aligned="1h", unique=True)
    raw_times = _times(raw5["open_time"], "raw5 open_time", aligned="5min", unique=True)
    cohort = mothers.copy().reset_index(drop=True)
    for name in ("signal_time", "decision_time"):
        cohort[name] = _times(cohort[name], name, aligned="1h", unique=False)
    if cohort["event_id"].isna().any() or cohort["event_id"].duplicated().any():
        raise ValueError("mother event_id must be non-null and unique")
    if not cohort["direction"].isin((-1, 1)).all() or cohort["direction"].map(lambda x: isinstance(x, (bool, np.bool_))).any():
        raise ValueError("mother direction must be +1 or -1")
    if not cohort["decision_time"].eq(cohort["signal_time"] + HOUR).all():
        raise ValueError("mother decision_time must equal K1 open + one hour")

    hourly_index = {stamp: index for index, stamp in enumerate(hourly["open_time"])}
    raw_available = set(raw_times.loc[raw_times + FIVE_MINUTES <= cutoff])
    requests, statuses = [], []
    for mother in cohort.to_dict("records"):
        start = mother["decision_time"]
        waiting_deadline = start + gap_max * HOUR
        base = {
            "event_id": mother["event_id"],
            "mother_signal_time": mother["signal_time"],
            "mother_decision_time": start,
            "mother_deadline": start + 72 * HOUR,
            "waiting_deadline": waiting_deadline,
        }

        def terminate(status: str, time: pd.Timestamp, reason: str, k2_time: Any = pd.NaT) -> None:
            statuses.append({
                **base, "terminal_time": time, "status": status,
                "wait_hours": max(0.0, (time - start).total_seconds() / 3600.0),
                "k2_time": k2_time, "reason": reason,
            })

        if cutoff < start:
            terminate("waiting_censored", cutoff, "mother_not_yet_available")
            continue
        mother_index = hourly_index.get(mother["signal_time"])
        if mother_index is None or pd.isna(hourly.iloc[mother_index]["segment_id"]):
            terminate("data_gap", start, "mother_hour_missing")
            continue
        mother_segment = hourly.iloc[mother_index]["segment_id"]
        direction = int(mother["direction"])
        for gap in range(1, gap_max + 1):
            candidate_open = mother["signal_time"] + gap * HOUR
            candidate_close = candidate_open + HOUR
            # Incremental raw coverage checks stop immediately at the earliest
            # known missing five-minute close; never inspect later price bars.
            raw_open = candidate_open
            missing_close = None
            while raw_open + FIVE_MINUTES <= min(candidate_close, cutoff):
                if raw_open not in raw_available:
                    missing_close = raw_open + FIVE_MINUTES
                    break
                raw_open += FIVE_MINUTES
            if missing_close is not None:
                terminate("data_gap", missing_close, "missing_completed_raw5_bar")
                break
            if candidate_close > cutoff:
                terminate("waiting_censored", cutoff, "next_hour_not_complete")
                break
            index = hourly_index.get(candidate_open)
            if index is None:
                terminate("data_gap", candidate_close, "completed_hour_missing")
                break
            row = hourly.iloc[index].to_dict()
            if pd.isna(row["segment_id"]) or row["segment_id"] != mother_segment:
                terminate("data_gap", candidate_close, "hourly_segment_changed")
                break
            o, h, low, c, ma = [float(row[key]) for key in ("open", "high", "low", "close", "ma")]
            if not np.isfinite([o, h, low, c, ma]).all() or min(o, h, low, c, ma) <= 0 or low > min(o, c) or h < max(o, c):
                terminate("data_gap", candidate_close, "hourly_prices_or_ma_unavailable")
                break
            qualifies, geometry = _k2_geometry(row, direction)
            if gap >= gap_min and qualifies:
                request = {
                    **mother, **base, **geometry,
                    "signal_time": candidate_open, "decision_time": candidate_close,
                    "k2_time": candidate_open,
                    "k2_initial_stop": low if direction == 1 else h,
                    "k2_atr": float(row["atr"]), "k2_ma": ma,
                    "k2_open": o, "k2_high": h, "k2_low": low, "k2_close": c,
                    "wait_hours": float(gap),
                }
                requests.append(request)
                terminate("request_emitted", candidate_close, "first_qualifying_k2", candidate_open)
                break
            if direction * (c - ma) < 0:
                terminate("invalidated_wrong_close", candidate_close, "failed_k2_closed_wrong_ma_side")
                break
            colour = 1 if (h + low) / 2 >= ma else -1
            if colour != direction:
                terminate("invalidated_ma_colour", candidate_close, "failed_k2_hl2_colour_wrong_side")
                break
            if gap == gap_max:
                terminate("expired_no_k2", candidate_close, "full_wait_observed_without_k2")

    request_columns = list(dict.fromkeys(list(cohort.columns) + REQUEST_COLUMNS))
    request_frame = pd.DataFrame(requests, columns=request_columns)
    status_frame = pd.DataFrame(statuses, columns=STATUS_COLUMNS)
    return request_frame, status_frame

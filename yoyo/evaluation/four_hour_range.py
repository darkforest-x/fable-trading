"""Pure offline replay of a New York 00:00--04:00 range reversion rule.

The input is a contiguous, already-closed five-minute UTC OHLCV frame.  Each
New York calendar day has a reference range made from the bars whose *open*
times lie in ``00:00 <= local < 04:00``.  This deliberately uses wall-clock
time: the spring DST day has 36 bars and the fall DST day has 60 bars.  A
range is unavailable unless every expected reference bar is present.

After 04:00 a close below the range arms a long excursion and a close above it
arms a short excursion.  The full high/low wick of every bar from the first
outside close through the first strictly-inside close is part of that
excursion's frozen stop extreme.  Equality is neither outside nor inside and
therefore leaves an armed excursion in place.  This is a deterministic
research evaluator: it does not read files, place orders, or size positions.
"""
from __future__ import annotations

from numbers import Integral
from typing import Any

import numpy as np
import pandas as pd

from yoyo.contracts.costs import LEGACY_P0_ROUND_TRIP


BAR = pd.Timedelta(minutes=5)
NY = "America/New_York"
TRADE_COLUMNS = [
    "trade_id", "signal_i", "signal_time", "excursion_start_i", "excursion_bars",
    "side", "entry_i", "entry_time", "entry_price", "entry_inside_range",
    "exit_i", "exit_bar_open_time", "exit_time", "exit_price", "stop_price",
    "target_price", "risk_pct", "gross_return", "net_return", "gross_r", "net_r",
    "reason", "dual_touch", "holding_bars", "censored", "range_high", "range_low",
    "target_inside_range", "volatility", "vol_bucket",
]
EVENT_COLUMNS = [
    "signal_i", "signal_time", "side", "status", "reason", "excursion_start_i",
    "excursion_bars", "stop_price", "range_high", "range_low", "entry_i",
    "entry_time", "entry_price", "entry_inside_range", "target_price", "risk_pct",
    "target_inside_range", "volatility", "vol_bucket", "trade_id",
]


def _validate_frame(frame5: pd.DataFrame) -> pd.DataFrame:
    """Return numeric OHLCV after enforcing the evaluator's source contract."""
    required = ["open", "high", "low", "close", "volume"]
    if not isinstance(frame5, pd.DataFrame) or not set(required).issubset(frame5.columns):
        raise ValueError("frame5 requires open/high/low/close/volume columns")
    index = frame5.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None or str(index.tz) != "UTC":
        raise ValueError("frame5 requires a timezone-aware UTC DatetimeIndex")
    if len(frame5) == 0 or not index.is_monotonic_increasing or not index.is_unique:
        raise ValueError("frame5 index must be nonempty, unique, and chronological")
    if index[0].minute % 5 or index[0].second or index[0].microsecond or index[0].nanosecond:
        raise ValueError("frame5 must begin on a five-minute UTC grid boundary")
    expected = pd.date_range(index[0], periods=len(index), freq="5min", tz="UTC")
    if not index.equals(expected):
        raise ValueError("frame5 must be a continuous five-minute UTC grid")
    frame = frame5.loc[:, required].astype(float).copy()
    values = frame.to_numpy()
    if not np.isfinite(values).all() or (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame.volume < 0).any():
        raise ValueError("frame5 has non-finite values, non-positive OHLC, or negative volume")
    lower = frame[["open", "close"]].min(axis=1)
    upper = frame[["open", "close"]].max(axis=1)
    if (frame.low > lower).any() or (frame.high < upper).any() or (frame.low > frame.high).any():
        raise ValueError("frame5 has invalid OHLC geometry")
    return frame


def _volatility(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Causal 14-bar TR mean / 14-bar close MA and prior-30-day quintiles.

    True range uses the prior close.  A bucket is -1 until its 8,640 strictly
    prior five-minute volatility values exist; its cut points never inspect the
    current or later bar.
    """
    previous_close = frame.close.shift(1)
    tr = pd.concat(
        [frame.high - frame.low, (frame.high - previous_close).abs(), (frame.low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    level = tr.rolling(14, min_periods=14).mean() / frame.close.rolling(14, min_periods=14).mean()
    prior = level.shift(1)
    cuts = np.column_stack([prior.rolling(8640, min_periods=8640).quantile(q).to_numpy() for q in (.2, .4, .6, .8)])
    value = level.to_numpy(float)
    bucket = np.full(len(frame), -1, dtype=int)
    ready = np.isfinite(value) & np.isfinite(cuts).all(axis=1)
    bucket[ready] = (value[ready, None] > cuts[ready]).sum(axis=1)
    return value, bucket


def _bound(index: pd.DatetimeIndex, value: Any, name: str, *, allow_end: bool) -> int:
    stamp = pd.Timestamp(value)
    if stamp.tz is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    stamp = stamp.tz_convert("UTC")
    if stamp < index[0] or stamp > index[-1] + BAR:
        raise ValueError(f"{name} is outside frame5")
    position = int(index.searchsorted(stamp, side="left"))
    if position < len(index) and index[position] != stamp:
        raise ValueError(f"{name} must fall on the five-minute grid")
    if position == len(index) and not allow_end:
        raise ValueError(f"{name} cannot equal the exclusive frame end")
    return position


def _daily_ranges(index: pd.DatetimeIndex) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build day-local reference metadata without looking outside each 00--04 range."""
    local = index.tz_convert(NY)
    days = local.strftime("%Y-%m-%d").to_numpy(dtype=str)
    unique_days = pd.Index(days).unique()
    n = len(index)
    high = np.full(n, np.nan)
    low = np.full(n, np.nan)
    range_complete = np.zeros(n, dtype=bool)
    day_complete = np.zeros(n, dtype=bool)
    day_end_i = np.empty(n, dtype=int)
    starts = np.empty(n, dtype=object)
    ends = np.empty(n, dtype=object)
    rows: list[dict[str, Any]] = []
    for day in unique_days:
        positions = np.flatnonzero(days == day)
        midnight = pd.Timestamp(day, tz=NY)
        # Construct the wall-clock endpoints directly.  Adding four elapsed
        # hours would incorrectly make the DST transition days look regular.
        range_end = pd.Timestamp(f"{day} 04:00", tz=NY)
        next_midnight = midnight + pd.DateOffset(days=1)
        start_utc, range_end_utc, next_utc = midnight.tz_convert("UTC"), range_end.tz_convert("UTC"), next_midnight.tz_convert("UTC")
        expected = pd.date_range(start_utc, range_end_utc, freq="5min", inclusive="left", tz="UTC")
        in_reference = index.get_indexer(expected)
        is_complete = bool((in_reference >= 0).all())
        end_i = min(int(index.searchsorted(next_utc, side="left")), n)
        has_day_end = bool(index[-1] + BAR >= next_utc)
        day_end_i[positions] = end_i
        day_complete[positions] = has_day_end
        starts[positions] = start_utc
        ends[positions] = range_end_utc
        if is_complete:
            # This function cannot price the range itself; values are filled in prepare.
            range_complete[positions] = True
        rows.append(
            dict(day=day, range_start=start_utc, range_end=range_end_utc, next_midnight=next_utc,
                 reference_bars_expected=len(expected), reference_bars_present=int((in_reference >= 0).sum()),
                 range_complete=is_complete, day_complete=has_day_end, day_end_i=end_i)
        )
    return pd.DataFrame(rows), range_complete, day_complete, day_end_i, starts, ends, days


def prepare(frame5: pd.DataFrame) -> dict[str, Any]:
    """Prepare causal range, volatility, and day-boundary arrays for replay.

    ``range_high`` and ``range_low`` stay NaN before local 04:00 and whenever
    the relevant 00:00--04:00 reference range is incomplete.  The source's
    final partial day is therefore never accidentally treated as a complete
    setup.  Returned arrays are prefix invariant: later bars cannot revise an
    earlier range or volatility value.
    """
    frame = _validate_frame(frame5)
    index = frame.index
    daily, range_complete, day_complete, day_end_i, starts, ends, days = _daily_ranges(index)
    range_high = np.full(len(frame), np.nan)
    range_low = np.full(len(frame), np.nan)
    local = index.tz_convert(NY)
    for row in daily.itertuples(index=False):
        if not row.range_complete:
            continue
        refs = (index >= row.range_start) & (index < row.range_end)
        visible = (days == row.day) & (local >= row.range_end.tz_convert(NY))
        high = float(frame.high.loc[refs].max())
        low = float(frame.low.loc[refs].min())
        range_high[visible] = high
        range_low[visible] = low
        daily.loc[daily.day == row.day, "range_high"] = high
        daily.loc[daily.day == row.day, "range_low"] = low
    daily["range_high"] = daily.get("range_high", np.nan)
    daily["range_low"] = daily.get("range_low", np.nan)
    entry_eligible = range_complete & (local.time >= pd.Timestamp("04:05").time()) & (local.time < pd.Timestamp("23:59").time())
    # Five-minute grid means < midnight is equivalently <= 23:55; the explicit
    # timestamp expression keeps the contract apparent for consumers.
    volatility, vol_bucket = _volatility(frame)
    return {
        "frame": frame,
        "open": frame.open.to_numpy(float), "high": frame.high.to_numpy(float),
        "low": frame.low.to_numpy(float), "close": frame.close.to_numpy(float),
        "day": days, "day_end_i": day_end_i, "day_complete": day_complete, "range_complete": range_complete,
        "range_high": range_high, "range_low": range_low,
        "range_start": starts, "range_end": ends, "entry_eligible": entry_eligible,
        "volatility": volatility, "vol_bucket": vol_bucket, "daily_ranges": daily,
    }


def trace_trade(ctx: dict[str, Any], entry_i: int, side: int, stop_price: float | None = None,
                end_i: int | None = None, *, risk_pct: float | None = None) -> dict[str, Any]:
    """Trace a same-NY-day 2R trade using observed open gaps then conservative wicks.

    Exactly one of ``stop_price`` and ``risk_pct`` is required.  Gap stops fill
    at the observed bar open; target gaps fill at the target.  When both fixed
    levels touch within a bar, the stop wins.  Intrabar and EOD exit timestamps
    are bar closes, while ``exit_bar_open_time`` identifies the source candle;
    gap timestamps are the known bar open.  A study/dataset endpoint before
    midnight is a final-close ``data_end`` mark and is explicitly censored.
    """
    frame = ctx["frame"]
    n = len(frame)
    if isinstance(entry_i, (bool, np.bool_)) or not isinstance(entry_i, Integral) or not 0 <= int(entry_i) < n:
        raise ValueError("entry_i must be a valid frame position")
    if side not in (-1, 1):
        raise ValueError("side must be +1 or -1")
    if (stop_price is None) == (risk_pct is None):
        raise ValueError("supply exactly one of stop_price or risk_pct")
    entry_i = int(entry_i)
    day_end = int(ctx["day_end_i"][entry_i])
    boundary = day_end if end_i is None else min(day_end, int(end_i))
    if not entry_i < boundary <= n:
        raise ValueError("end_i must be after entry_i and within frame5")
    opening = float(ctx["open"][entry_i])
    if risk_pct is not None:
        if not np.isfinite(risk_pct) or risk_pct <= 0:
            raise ValueError("risk_pct must be positive and finite")
        stop_price = opening * (1 - side * float(risk_pct))
    stop = float(stop_price)
    risk = side * (opening - stop)
    if not np.isfinite(stop) or risk <= 0:
        return dict(entry_i=entry_i, entry_time=frame.index[entry_i], entry_price=opening, side=side,
                    stop_price=stop, reason="invalid_entry_gap", status="invalid", censored=False)
    target = opening + side * 2.0 * risk
    if not np.isfinite(target) or target <= 0:
        return dict(entry_i=entry_i, entry_time=frame.index[entry_i], entry_price=opening, side=side,
                    stop_price=stop, target_price=target, reason="invalid_entry_gap", status="invalid", censored=False)
    risk_pct_value = risk / opening
    base = dict(entry_i=entry_i, entry_time=frame.index[entry_i], entry_price=opening, side=side,
                stop_price=stop, target_price=target, risk_pct=risk_pct_value)
    for i in range(entry_i, boundary):
        op, hi, lo = float(ctx["open"][i]), float(ctx["high"][i]), float(ctx["low"][i])
        stop_gap = op <= stop if side == 1 else op >= stop
        target_gap = op >= target if side == 1 else op <= target
        stop_touch = lo <= stop if side == 1 else hi >= stop
        target_touch = hi >= target if side == 1 else lo <= target
        if stop_gap:
            price, reason, dual, exit_time = op, "stop_gap", False, frame.index[i]
        elif target_gap:
            price, reason, dual, exit_time = target, "target_gap", False, frame.index[i]
        elif stop_touch:
            price, reason, dual, exit_time = stop, "stop", bool(target_touch), frame.index[i] + BAR
        elif target_touch:
            price, reason, dual, exit_time = target, "target", False, frame.index[i] + BAR
        else:
            continue
        gross_return = side * (price - opening) / opening
        net_return = gross_return - LEGACY_P0_ROUND_TRIP
        gross_r = side * (price - opening) / risk
        return base | dict(exit_i=i, exit_bar_open_time=frame.index[i], exit_time=exit_time, exit_price=price,
                           reason=reason, dual_touch=dual, holding_bars=i - entry_i + 1, censored=False,
                           gross_return=gross_return, net_return=net_return, gross_r=gross_r,
                           net_r=net_return / risk_pct_value, status="closed")
    last_i = boundary - 1
    price = float(ctx["close"][last_i])
    censored = boundary < day_end or not bool(ctx["day_complete"][entry_i])
    reason = "data_end" if censored else "daily_end"
    gross_return = side * (price - opening) / opening
    net_return = gross_return - LEGACY_P0_ROUND_TRIP
    return base | dict(exit_i=last_i, exit_bar_open_time=frame.index[last_i], exit_time=frame.index[last_i] + BAR,
                       exit_price=price, reason=reason, dual_touch=False, holding_bars=boundary - entry_i,
                       censored=censored, gross_return=gross_return, net_return=net_return,
                       gross_r=side * (price - opening) / risk, net_r=net_return / risk_pct_value,
                       status="closed")


def _event_base(ctx: dict[str, Any], i: int, side: int, start_i: int, bars: int, stop: float) -> dict[str, Any]:
    frame = ctx["frame"]
    return dict(signal_i=i, signal_time=frame.index[i] + BAR, side=side, status="candidate", reason="",
                excursion_start_i=start_i, excursion_bars=bars, stop_price=stop,
                range_high=float(ctx["range_high"][i]), range_low=float(ctx["range_low"][i]),
                entry_i=pd.NA, entry_time=pd.NaT, entry_price=np.nan, entry_inside_range=pd.NA,
                target_price=np.nan, risk_pct=np.nan, target_inside_range=pd.NA,
                volatility=float(ctx["volatility"][i]), vol_bucket=int(ctx["vol_bucket"][i]), trade_id=pd.NA)


def _typed_table(rows: list[dict[str, Any]], columns: list[str], time_columns: tuple[str, ...]) -> pd.DataFrame:
    """Construct result tables with UTC time dtypes even when no row was emitted."""
    table = pd.DataFrame(rows, columns=columns)
    for name in time_columns:
        table[name] = pd.to_datetime(table[name], utc=True)
    return table


def simulate(ctx: dict[str, Any], start: Any, end: Any) -> dict[str, pd.DataFrame]:
    """Simulate close-confirmed, serial range excursions in ``[start, end)``.

    Every first strictly-inside close becomes an event, including candidates
    discarded by occupancy or execution safeguards.  A candidate formed on a
    bar that closes an old trade may enter on the next bar; one whose next open
    overlaps an existing trade is recorded as ``skipped_occupied``.
    """
    frame = ctx["frame"]
    start_i = _bound(frame.index, start, "start", allow_end=False)
    end_i = _bound(frame.index, end, "end", allow_end=True)
    if start_i >= end_i:
        raise ValueError("start must precede end")
    events: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    state = 0
    excursion_start = -1
    extreme_low = np.nan
    extreme_high = np.nan
    excursion_bars = 0
    occupied_until = start_i - 1
    current_day: str | None = None
    for i in range(start_i, end_i):
        day = str(ctx["day"][i])
        if day != current_day:
            current_day, state, excursion_start, excursion_bars = day, 0, -1, 0
            extreme_low, extreme_high = np.nan, np.nan
        high, low, close = float(ctx["high"][i]), float(ctx["low"][i]), float(ctx["close"][i])
        range_high, range_low = float(ctx["range_high"][i]), float(ctx["range_low"][i])
        if not (np.isfinite(range_high) and np.isfinite(range_low)):
            continue
        outside_side = 1 if close < range_low else -1 if close > range_high else 0
        inside = range_low < close < range_high
        if state == 0:
            if outside_side:
                state, excursion_start, excursion_bars = outside_side, i, 1
                extreme_low, extreme_high = low, high
            continue
        if inside:
            # The return bar wick belongs to the excursion before it emits.
            extreme_low, extreme_high = min(extreme_low, low), max(extreme_high, high)
            excursion_bars += 1
            stop = extreme_low if state == 1 else extreme_high
            event = _event_base(ctx, i, state, excursion_start, excursion_bars, stop)
            entry = i + 1
            if entry >= end_i:
                event.update(status="no_next_bar", reason="no_next_bar")
            elif str(ctx["day"][entry]) != day:
                event.update(status="skipped_day_boundary", reason="next_bar_new_ny_day")
            elif entry <= occupied_until:
                event.update(status="skipped_occupied", reason="position_open")
            elif not bool(ctx["entry_eligible"][entry]):
                event.update(status="skipped_day_boundary", reason="entry_not_eligible")
            else:
                entry_price = float(ctx["open"][entry])
                risk = state * (entry_price - stop)
                event.update(entry_i=entry, entry_time=frame.index[entry], entry_price=entry_price,
                             entry_inside_range=bool(range_low <= entry_price <= range_high),
                             risk_pct=(risk / entry_price if risk > 0 else np.nan),
                             target_price=(entry_price + state * 2 * risk if risk > 0 else np.nan),
                             target_inside_range=(bool(range_low < entry_price + state * 2 * risk < range_high) if risk > 0 else pd.NA))
                if risk <= 0 or not np.isfinite(risk) or not np.isfinite(event["target_price"]) or event["target_price"] <= 0:
                    event.update(status="skipped_entry_gap", reason="invalid_entry_gap")
                else:
                    trace = trace_trade(ctx, entry, state, stop, end_i)
                    if trace.get("status") == "invalid":
                        event.update(status="skipped_entry_gap", reason="invalid_entry_gap")
                    else:
                        trade_id = len(trades)
                        event.update(status="filled", reason="", trade_id=trade_id)
                        row = {name: trace.get(name, np.nan) for name in TRADE_COLUMNS}
                        row.update(trade_id=trade_id, signal_i=i, signal_time=frame.index[i] + BAR,
                                   excursion_start_i=excursion_start, excursion_bars=excursion_bars,
                                   range_high=range_high, range_low=range_low,
                                   entry_inside_range=event["entry_inside_range"],
                                   target_inside_range=event["target_inside_range"],
                                   volatility=float(ctx["volatility"][i]), vol_bucket=int(ctx["vol_bucket"][i]))
                        trades.append(row)
                        occupied_until = int(trace["exit_i"])
            events.append(event)
            state, excursion_start, excursion_bars = 0, -1, 0
            extreme_low, extreme_high = np.nan, np.nan
        elif outside_side and outside_side != state:
            # No inside close occurred: start the opposite excursion at this bar.
            state, excursion_start, excursion_bars = outside_side, i, 1
            extreme_low, extreme_high = low, high
        else:
            # Same-direction closes and equality both retain the state and wick.
            extreme_low, extreme_high = min(extreme_low, low), max(extreme_high, high)
            excursion_bars += 1
    study_days = pd.unique(ctx["day"][start_i:end_i])
    return {
        "trades": _typed_table(trades, TRADE_COLUMNS, ("signal_time", "entry_time", "exit_bar_open_time", "exit_time")),
        "events": _typed_table(events, EVENT_COLUMNS, ("signal_time", "entry_time")),
        "daily_ranges": ctx["daily_ranges"].loc[ctx["daily_ranges"].day.isin(study_days)].copy(),
    }

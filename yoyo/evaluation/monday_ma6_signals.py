"""Causal Monday 1.618 six-MA signal and pending-order preparation.

This module only prepares opportunities, resting-order geometry, and causal
amendment events.  Nautilus owns every fill and outcome.  Monday anchors and
the lagged five-minute volatility bucket reuse ``monday_parameter_signals``;
the six lines reuse the renderer-contract ``add_six_mas`` implementation.

Decision inputs are completed five-minute OHLC through a first post-Monday
1.618 touch and completed fifteen-minute OHLC/MA values.  A long requires four
consecutive fifteen-minute closes strictly above all six lines; a short
requires four strictly below them.  Each MA at a decision uses only complete
fifteen-minute bars through that close, with 120 complete bars of warmup.
Crypto MA history resets at data gaps.  Gold carries MA history across normal
market closures within the supplied quality segment, while its four-close
confirmation still requires continuous fifteen-minute wall time.

No entry, stop, target, or excursion is evaluated against later OHLC here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS, add_six_mas
from yoyo.evaluation.monday_15m_volume_rejection import week_keys
from yoyo.evaluation.monday_parameter_signals import base_context

STEP5 = pd.Timedelta(minutes=5)
STEP15 = pd.Timedelta(minutes=15)
TZ = "America/New_York"
OHLC = ("open", "high", "low", "close")
WARMUP_15M = 120
CONTROLS_PER_SIGNAL = 3


def _week_start_utc(week: str) -> pd.Timestamp:
    """Return a NY-local Monday midnight as an aware UTC timestamp."""
    return pd.Timestamp(week).tz_localize(TZ).tz_convert("UTC")


def _aggregate_15m(f: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Build UTC-open 15m bars and renderer-contract MAs on complete bars."""
    aggregation = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in f.columns:
        aggregation["volume"] = "sum"
    grouped = f.resample("15min", closed="left", label="left")
    raw = grouped.agg(aggregation)
    raw["source_bars"] = grouped.close.count()
    raw["source_count"] = grouped["count"].sum(min_count=1)

    if not len(f):
        return raw
    grid = pd.date_range(f.index[0].floor("15min"), f.index[-1].floor("15min"),
                         freq=STEP15, tz="UTC", name=f.index.name)
    h = raw.reindex(grid)
    h["source_bars"] = h.source_bars.fillna(0).astype("int64")
    h["source_count"] = h.source_count.fillna(0).astype("int64")
    source_pos = [f.index.get_indexer(grid + offset) for offset in (pd.Timedelta(0), STEP5, 2 * STEP5)]
    exact = np.logical_and.reduce([p >= 0 for p in source_pos])
    safe = [np.maximum(p, 0) for p in source_pos]
    complete_sources = np.logical_and.reduce([
        f["count"].to_numpy()[p].astype(float) == 5 for p in safe
    ])
    if "quality_segment" in f.columns:
        segment_values = f["quality_segment"].to_numpy()
        same_segment = np.logical_and.reduce([
            segment_values[safe[0]] == segment_values[p] for p in safe[1:]
        ])
    else:
        same_segment = np.ones(len(grid), dtype=bool)
    complete = exact & complete_sources & same_segment & h.source_bars.eq(3).to_numpy() & h.source_count.eq(15).to_numpy()
    h["complete"] = complete
    h["global_i"] = np.where(complete, source_pos[2], -1).astype("int64")
    if "quality_segment" in f.columns:
        h["quality_segment"] = np.where(complete, f.quality_segment.to_numpy()[safe[2]], np.nan)

    # add_six_mas is run only on complete observations.  Gold's normal session
    # closures do not reset the 120-observation warmup; quality segments do.
    ma = pd.DataFrame(np.nan, index=grid, columns=SIX_MA_COLUMNS, dtype="float64")
    positions = np.flatnonzero(complete)
    if len(positions):
        split_at = []
        for k in range(1, len(positions)):
            prev, cur = positions[k - 1], positions[k]
            reset = False
            if "quality_segment" in f.columns and h.quality_segment.iloc[prev] != h.quality_segment.iloc[cur]:
                reset = True
            if symbol != "XAUUSD" and grid[cur] - grid[prev] != STEP15:
                reset = True
            if reset:
                split_at.append(k)
        for run in np.split(positions, split_at):
            enriched = add_six_mas(h.iloc[run][list(OHLC)])
            ma.iloc[run] = enriched.loc[:, list(SIX_MA_COLUMNS)].to_numpy(float)
    h.loc[:, list(SIX_MA_COLUMNS)] = ma
    h["ma_warm"] = complete & ma.notna().all(axis=1).to_numpy()
    h.index = h.index.as_unit("ns")
    return h


def _prefix_known(base: dict) -> np.ndarray:
    """Whether each crypto 5m bar follows a complete NY-Monday prefix."""
    f = base["f"]
    known = np.zeros(len(f), dtype=bool)
    weeks = base["week5"]
    for week in pd.unique(weeks):
        ix = np.flatnonzero(weeks == week)
        if not len(ix):
            continue
        start = _week_start_utc(str(week))
        expected = (f.index[ix].asi8 - start.value) // STEP5.value
        exact = expected == np.arange(len(ix), dtype=np.int64)
        complete = f["count"].iloc[ix].to_numpy() == 5
        known[ix] = np.logical_and.accumulate(exact & complete)
    return known


def _range_return(f: pd.DataFrame, touch_i: int, side: int, level: float,
                  boundary: float, week_end: pd.Timestamp,
                  max_i: int | None = None) -> tuple[int, int]:
    """First post-touch boundary return under O-H-L-C, as (global_i, phase).

    Phases are open=0, high=1, low=2, close=3.  In the initial touch candle,
    only nodes after the first node that reaches the 1.618 level are eligible.
    """
    last = int(f.index.searchsorted(week_end, side="left") - 1)
    if max_i is not None:
        last = min(last, int(max_i))
    if last < touch_i:
        return -1, -1
    # Restrict the copy to this opportunity's remaining NY week.  This helper
    # runs once per first-touch side, so copying the complete multi-year frame
    # here would multiply memory traffic by the number of opportunities.
    window = f.iloc[touch_i:last + 1]
    prices = window.loc[:, list(OHLC)].to_numpy(dtype=float)
    # First touch can occur at the open (then the later H/L/C nodes count) or
    # at the directional wick (then only the close remains in O-H-L-C order).
    first = prices[0]
    for k, price in enumerate(first):
        touched = price <= level if side == 1 else price >= level
        if touched:
            for phase in range(k + 1, 4):
                returned = first[phase] >= boundary if side == 1 else first[phase] <= boundary
                if returned:
                    return touch_i, phase
            break
    if touch_i >= last:
        return -1, -1
    rows = prices[1:]
    complete = window["count"].iloc[1:].to_numpy() == 5
    if side == 1:
        hit = (rows[:, 0] >= boundary) | (rows[:, 1] >= boundary) | (rows[:, 3] >= boundary)
    else:
        hit = (rows[:, 0] <= boundary) | (rows[:, 2] <= boundary) | (rows[:, 3] <= boundary)
    candidates = np.flatnonzero(hit & complete)
    if not len(candidates):
        return -1, -1
    local_i = 1 + int(candidates[0])
    i = touch_i + local_i
    o, h, _l, c = prices[local_i]
    if side == 1:
        phase = 0 if o >= boundary else 1 if h >= boundary else 3
    else:
        phase = 0 if o <= boundary else 2 if prices[local_i, 2] <= boundary else 3
    return i, phase


def _segment_ends(f: pd.DataFrame, symbol: str) -> np.ndarray:
    """Last global bar in each execution-continuous/source-quality segment."""
    n = len(f)
    ends = np.arange(n, dtype=np.int64)
    if symbol == "XAUUSD" and "quality_segment" in f.columns:
        values = f.quality_segment
        positions = pd.Series(np.arange(n, dtype=np.int64), index=f.index)
        grouped = positions.groupby(values, dropna=False).transform("max")
        ends = grouped.to_numpy(dtype=np.int64)
        return ends
    if symbol == "XAUUSD":
        return np.full(n, n - 1, dtype=np.int64)
    times = f.index.asi8
    complete = f["count"].to_numpy() == 5
    breaks = ~(complete[:-1] & complete[1:] & (np.diff(times) == STEP5.value))
    starts = np.r_[0, np.flatnonzero(breaks) + 1]
    ends_by_segment = np.r_[starts[1:], n] - 1
    return np.repeat(ends_by_segment, np.diff(np.r_[starts, n])).astype(np.int64)


def _week_expiry_i(f: pd.DataFrame, week_end: pd.Timestamp) -> int:
    """Last observed 5m open whose candle close is no later than week_end."""
    i = int(f.index.searchsorted(week_end - STEP5, side="right") - 1)
    if i < 0 or f.index[i] + STEP5 > week_end:
        return -1
    return i


def _geometry(hrow: pd.Series, side: int, monday_high: float, monday_low: float) -> dict:
    """Causal six-MA limit/stop and fixed Monday/far 1.618 targets."""
    mas = hrow.loc[list(SIX_MA_COLUMNS)].to_numpy(dtype=float)
    if len(mas) != 6 or not np.isfinite(mas).all():
        return {"valid": False, "reason": "no_warmup"}
    if side == 1:
        entry = float(np.max(mas))
        stop = float(np.min(mas) * 0.995)
        boundary, target_monday = float(monday_low), float(monday_high)
        target_far = monday_high + 0.618 * (monday_high - monday_low)
    else:
        entry = float(np.min(mas))
        stop = float(np.max(mas) * 1.005)
        boundary, target_monday = float(monday_high), float(monday_low)
        target_far = monday_low - 0.618 * (monday_high - monday_low)
    risk = side * (entry - stop)
    if not np.isfinite([entry, stop, risk, target_monday, target_far, boundary]).all():
        return {"valid": False, "reason": "nonfinite_geometry"}
    if risk <= 0:
        return {"valid": False, "reason": "nonpositive_risk"}
    if side * (boundary - entry) <= 0:
        return {"valid": False, "reason": "entry_not_outside_monday_boundary"}
    if side * (target_monday - entry) <= 0 or side * (target_far - target_monday) <= 0:
        return {"valid": False, "reason": "target_geometry"}
    return dict(valid=True, entry=entry, stop=stop, risk=float(risk),
                cancel_boundary=boundary, target_monday=target_monday,
                target_far=float(target_far), target_price=target_monday)


def _order_row(*, f: pd.DataFrame, base: dict, h: pd.DataFrame, symbol: str,
               week: str, side: int, opportunity_id: str, population: str,
               entry_i: int, h_i: int, monday_high: float, monday_low: float,
               level: float, segment_ends: np.ndarray, week_end: pd.Timestamp,
               expiry_i: int, geometry: dict, touch_i: int, touch_15m_i: int,
               new_extremes: int, running_extreme: float, split_time: str) -> dict | None:
    """Build one native-order row if its causal execution horizon is observed."""
    if not geometry.get("valid", False) or entry_i < 0 or entry_i + 1 >= len(f):
        return None
    next_i = entry_i + 1
    if (symbol != "XAUUSD" and f.index[next_i] - f.index[entry_i] != STEP5) or f["count"].iloc[next_i] != 5:
        return None
    # Use the submission bar's bucket.  Its open and the preceding 14 TR bars
    # are known by the completed submission close; the following bar's open
    # is future information even though it is a valid execution-time bucket
    # for trade-management purposes.
    bucket = int(base["bucket"][entry_i])
    if bucket == -999 or expiry_i < 0:
        return None
    end_i = int(segment_ends[entry_i])
    effective_expiry_i = min(int(expiry_i), end_i)
    if not entry_i < effective_expiry_i <= end_i:
        return None
    if f.index[effective_expiry_i] + STEP5 > week_end:
        return None
    trade_id = opportunity_id if population == "signal" else opportunity_id
    return dict(trade_id=trade_id, pair_id=opportunity_id, population=population,
        symbol=symbol, week=week, side=int(side), entry_i=int(entry_i),
        entry=float(geometry["entry"]), stop=float(geometry["stop"]),
        risk=float(geometry["risk"]), target_price=float(geometry["target_price"]),
        target_monday=float(geometry["target_monday"]), target_far=float(geometry["target_far"]),
        cancel_boundary=float(geometry["cancel_boundary"]), expire_i=effective_expiry_i,
        expiry_reason="week_end" if effective_expiry_i == expiry_i else "source_boundary",
        end_i=end_i, bucket=bucket, causalvolbucket=bucket, level=float(level),
        monday_high=float(monday_high), monday_low=float(monday_low),
        submit_time=str(f.index[entry_i] + STEP5), entry_time=str(f.index[entry_i]),
        entry_15m_i=int(h_i), touch_i=int(touch_i), touch_15m_i=int(touch_15m_i),
        new_extremes=int(new_extremes), running_extreme=float(running_extreme),
        segment=int(f.index[entry_i] >= pd.Timestamp(split_time)))


def prepare(f: pd.DataFrame, symbol: str, cfg: dict, blocked=()) -> dict:
    """Prepare signal opportunities, native orders, and pending-order updates.

    Returns ``{"h", "opportunities", "orders", "updates", "diagnostics"}``.
    ``h`` is a UTC-open 15m frame with OHLC, optional summed volume, ``count``,
    completion/global-index fields and the six renderer-contract MA columns.
    Every execution index in the other frames is a positional index into the
    original 5m ``f``.  ``orders`` uses the Monday-edge target in
    ``target_price`` and also carries ``target_far`` for the caller's
    predeclared target projection.  This function never fills or scores a
    trade.
    """
    if symbol == "":
        raise ValueError("symbol is required")
    if not isinstance(f.index, pd.DatetimeIndex) or f.index.tz is None:
        raise ValueError("f must have an aware UTC DatetimeIndex of 5m OPEN times")
    if str(f.index.tz) not in ("UTC", "UTC+00:00"):
        f = f.copy()
        f.index = f.index.tz_convert("UTC")
    f = f.copy(deep=False)
    f.index = f.index.as_unit("ns")
    if not f.index.is_monotonic_increasing or f.index.has_duplicates:
        raise ValueError("f index must be sorted and unique")
    missing = [c for c in (*OHLC, "count") if c not in f.columns]
    if missing:
        raise ValueError(f"f is missing required 5m columns: {missing}")
    if len(f) and ((f.index.asi8 % STEP5.value) != 0).any():
        raise ValueError("f timestamps must align to UTC 5m OPEN boundaries")

    base = base_context(f, symbol, blocked)
    f = base["f"]
    h = _aggregate_15m(f, symbol)
    if h.empty:
        empty = pd.DataFrame()
        return dict(h=h, opportunities=empty, orders=empty.copy(), updates=empty.copy(),
                    diagnostics=dict(first_touches=0, signals=0, random=0, unmatched=0))

    local5, week5 = base["local5"], base["week5"]
    anchors = base["anchors"]
    known_prefix = _prefix_known(base) if symbol != "XAUUSD" else np.ones(len(f), dtype=bool)
    segment_ends = _segment_ends(f, symbol)
    # Week labels are chronological and contiguous in this sorted 5m frame.
    # Build the eligible post-Monday position lists once rather than rescanning
    # every row for every anchor week.
    week_rows_by_key: dict[str, np.ndarray] = {}
    week_boundaries = np.r_[0, np.flatnonzero(week5[1:] != week5[:-1]) + 1, len(f)]
    complete5 = f["count"].to_numpy() == 5
    for start, end in zip(week_boundaries[:-1], week_boundaries[1:]):
        positions = np.arange(start, end, dtype=np.int64)
        eligible = (local5.dayofweek[start:end] >= 1) & complete5[start:end]
        week_rows_by_key[str(week5[start])] = positions[eligible]
    rng = np.random.default_rng(int(cfg.get("seed", 20261024)))
    n_controls = int(cfg.get("controls_per_signal", CONTROLS_PER_SIGNAL))
    if n_controls != CONTROLS_PER_SIGNAL:
        raise ValueError(f"controls_per_signal is fixed at {CONTROLS_PER_SIGNAL}")

    opportunities: list[dict] = []
    orders: list[dict] = []
    updates: list[dict] = []
    unmatched = 0
    anchors = anchors[anchors.high > anchors.low]

    for week, anchor in anchors.iterrows():
        week = str(week)
        monday_high, monday_low = float(anchor.high), float(anchor.low)
        week_start = _week_start_utc(week)
        week_end = _week_start_utc((pd.Timestamp(week) + pd.Timedelta(days=7)).strftime("%Y-%m-%d"))
        expiry_i = _week_expiry_i(f, week_end)
        week_rows = week_rows_by_key.get(week, np.empty(0, dtype=np.int64))
        if not len(week_rows):
            continue

        for side in (1, -1):
            level = (monday_low - 0.618 * (monday_high - monday_low) if side == 1
                     else monday_high + 0.618 * (monday_high - monday_low))
            hit_mask = (f.low.to_numpy()[week_rows] <= level if side == 1
                        else f.high.to_numpy()[week_rows] >= level)
            hits = week_rows[np.flatnonzero(hit_mask)]
            if not len(hits):
                continue
            touch_i = int(hits[0])
            pair_id = f"{symbol}-{week}-{side:+d}"
            touch_time = f.index[touch_i]
            touch_h_i = int(h.index.get_indexer([touch_time.floor("15min")])[0])
            opp = dict(opportunity_id=pair_id, pair_id=pair_id, symbol=symbol,
                week=week, side=side, level=float(level), monday_high=monday_high,
                monday_low=monday_low, touch_i=touch_i, touch_open_time=str(touch_time),
                trigger_time=str(touch_time + STEP5), touch_15m_i=touch_h_i,
                touch_15m_time=str(touch_time.floor("15min")), confirm_i=-1,
                confirm_15m_i=-1, invalid_i=-1, invalid_15m_i=-1,
                status="pending", reason="", signal_trade_id=pair_id)

            touch_end_i = int(segment_ends[touch_i])
            source_gap_i = (touch_end_i + 1 if symbol != "XAUUSD" and touch_end_i + 1 < len(f)
                            else -1)
            fully_observable_week = (expiry_i >= 0 and expiry_i <= touch_end_i
                and f.index[touch_end_i] + STEP5 >= week_end)

            if not known_prefix[touch_i]:
                opp.update(status="invalid", reason="unknown_crypto_prefix", invalid_i=touch_i)
                opportunities.append(opp)
                continue
            if touch_h_i < 0 or not bool(h.complete.iloc[touch_h_i]):
                opp.update(status="invalid", reason="touch_15m_incomplete", invalid_i=touch_i)
                opportunities.append(opp)
                continue

            # Locate the first range return from the trigger point under the
            # stipulated O-H-L-C path; the trigger touch itself is not a return.
            boundary = monday_low if side == 1 else monday_high
            return_i, return_phase = _range_return(
                f, touch_i, side, level, boundary, week_end,
                max_i=touch_end_i if symbol != "XAUUSD" else None)
            if return_i >= 0:
                opp["range_return_i"] = return_i
                opp["range_return_phase"] = return_phase
                opp["range_return_time"] = str(f.index[return_i] + STEP5)

            running_extreme = float(h.low.iloc[touch_h_i] if side == 1 else h.high.iloc[touch_h_i])
            new_extremes = 0
            streak = 0
            previous_complete_i = -1
            saw_warm = False
            signal_h_i = -1
            signal_candidate = None
            random_candidates: list[dict] = []
            third_extreme_h_i = -1
            range_before_signal = False
            monitor_stopped_by_gap = False
            stopped_before_source_gap = False

            last_h_i = min(len(h) - 1, int(h.index.searchsorted(week_end, side="left") - 1))
            if symbol != "XAUUSD":
                eligible_h = np.flatnonzero(h.complete.to_numpy() & (h.global_i.to_numpy() <= touch_end_i))
                last_h_i = min(last_h_i, int(eligible_h[-1]) if len(eligible_h) else touch_h_i - 1)
            for j in range(touch_h_i, last_h_i + 1):
                if not bool(h.complete.iloc[j]):
                    streak = 0
                    previous_complete_i = -1
                    continue
                if previous_complete_i >= 0 and h.index[j] - h.index[previous_complete_i] != STEP15:
                    streak = 0
                previous_complete_i = j

                if j > touch_h_i:
                    extreme = float(h.low.iloc[j] if side == 1 else h.high.iloc[j])
                    refreshed = extreme < running_extreme if side == 1 else extreme > running_extreme
                    if refreshed:
                        running_extreme = extreme
                        new_extremes += 1
                        if new_extremes >= 3:
                            third_extreme_h_i = j
                            if signal_candidate is None:
                                opp.update(status="invalid", reason="third_new_extreme",
                                           invalid_i=int(h.global_i.iloc[j]), invalid_15m_i=j,
                                           new_extremes=new_extremes)
                            else:
                                opp.update(post_confirm_invalid_reason="third_new_extreme",
                                           post_confirm_invalid_i=int(h.global_i.iloc[j]),
                                           post_confirm_invalid_15m_i=j,
                                           new_extremes=new_extremes)
                            stopped_before_source_gap = True
                            break

                last5_i = int(h.global_i.iloc[j])
                close_i = last5_i
                close_time = h.index[j] + STEP15
                # Submission happens only after this close.  Any OHLC node
                # reaching the Monday boundary in its final 5m candle has
                # already invalidated a not-yet-submitted candidate.
                return_is_earlier = return_i >= 0 and return_i <= close_i
                if return_is_earlier:
                    if signal_candidate is None:
                        opp.update(status="invalid", reason="returned_to_monday_range",
                                   invalid_i=return_i, invalid_15m_i=j)
                        range_before_signal = True
                    else:
                        opp.update(post_confirm_invalid_reason="returned_to_monday_range",
                                   post_confirm_invalid_i=return_i,
                                   post_confirm_invalid_15m_i=j)
                    stopped_before_source_gap = True
                    break

                warm = bool(h.ma_warm.iloc[j])
                if warm:
                    saw_warm = True
                    ma_values = h.loc[h.index[j], list(SIX_MA_COLUMNS)].to_numpy(float)
                    favorable = bool(np.all(side * (float(h.close.iloc[j]) - ma_values) > 0))
                else:
                    favorable = False

                next_i = close_i + 1
                next_observed = (next_i < len(f) and f["count"].iloc[next_i] == 5
                                 and (symbol == "XAUUSD" or f.index[next_i] - f.index[close_i] == STEP5))
                # Matching uses the bucket at the completed submission bar.
                # Do not use next_i: that 5m open is not known at this close.
                bucket = int(base["bucket"][close_i])
                geom = _geometry(h.iloc[j], side, monday_high, monday_low) if warm else {"valid": False, "reason": "no_warmup"}
                candidate_order = None
                if favorable and geom.get("valid") and next_observed and bucket != -999:
                    candidate_order = _order_row(f=f, base=base, h=h, symbol=symbol,
                        week=week, side=side, opportunity_id=pair_id, population="random",
                        entry_i=close_i, h_i=j, monday_high=monday_high, monday_low=monday_low,
                        level=level, segment_ends=segment_ends, week_end=week_end,
                        expiry_i=expiry_i, geometry=geom, touch_i=touch_i,
                        touch_15m_i=touch_h_i, new_extremes=new_extremes,
                        running_extreme=running_extreme,
                        split_time=str(cfg.get("split", "2023-01-01T00:00:00Z")))
                    if candidate_order is not None:
                        candidate_order["bucket"] = bucket
                        candidate_order["causalvolbucket"] = bucket
                        candidate_order["entry"] = float(geom["entry"])
                        candidate_order["trade_id"] = f"{pair_id}-candidate-{close_i}"
                        random_candidates.append(candidate_order)

                if warm and favorable:
                    streak += 1
                else:
                    streak = 0
                if streak >= 4 and signal_h_i < 0:
                    signal_h_i = j
                    opp.update(confirm_i=close_i, confirm_15m_i=j,
                               confirm_time=str(close_time), confirm_15m_time=str(h.index[j]),
                               status="confirmed", reason="")
                    if not next_observed:
                        opp.update(status="invalid", reason="invalid_unobservable", invalid_i=close_i)
                    elif bucket == -999:
                        opp.update(status="invalid", reason="no_causal_volatility_bucket",
                                   invalid_i=close_i)
                    elif not geom.get("valid"):
                        opp.update(status="invalid", reason=str(geom.get("reason", "invalid_geometry")),
                                   invalid_i=close_i)
                    else:
                        signal_candidate = _order_row(f=f, base=base, h=h, symbol=symbol,
                            week=week, side=side, opportunity_id=pair_id, population="signal",
                            entry_i=close_i, h_i=j, monday_high=monday_high, monday_low=monday_low,
                            level=level, segment_ends=segment_ends, week_end=week_end,
                            expiry_i=expiry_i, geometry=geom, touch_i=touch_i,
                            touch_15m_i=touch_h_i, new_extremes=new_extremes,
                            running_extreme=running_extreme,
                            split_time=str(cfg.get("split", "2023-01-01T00:00:00Z")))
                        if signal_candidate is None:
                            opp.update(status="invalid", reason="invalid_unobservable", invalid_i=close_i)
                        else:
                            signal_candidate["bucket"] = bucket
                            signal_candidate["causalvolbucket"] = bucket
                            orders.append(signal_candidate)
                    # The first four-close confirmation is final.  If it
                    # yielded an order, keep scanning until the first
                    # invalidation so random timing candidates after the
                    # confirmation remain eligible; never emit a second one.
                    if signal_candidate is None:
                        stopped_before_source_gap = True
                        break

            source_gap_before_week = (symbol != "XAUUSD" and source_gap_i >= 0
                                      and touch_end_i < expiry_i)
            if source_gap_before_week and not stopped_before_source_gap and third_extreme_h_i < 0 and not range_before_signal:
                monitor_stopped_by_gap = True
                opp["source_gap_i"] = source_gap_i
                if signal_candidate is not None:
                    opp.setdefault("post_confirm_invalid_reason", "source_gap")
                    opp.setdefault("post_confirm_invalid_i", source_gap_i)

            if third_extreme_h_i < 0 and not range_before_signal and signal_h_i < 0:
                if return_i >= 0:
                    opp.update(status="invalid", reason="returned_to_monday_range", invalid_i=return_i)
                elif monitor_stopped_by_gap:
                    opp.update(status="invalid_unobservable", reason="source_gap",
                               invalid_i=source_gap_i)
                elif not fully_observable_week:
                    opp.update(status="invalid_unobservable", reason="invalid_unobservable",
                               invalid_i=int(segment_ends[touch_i]))
                elif not saw_warm:
                    opp.update(status="no_warmup", reason="no_warmup")
                else:
                    opp.update(status="no_confirmation", reason="no_confirmation")

            # A confirmed signal is the only parent of matched random timing
            # candidates.  Sampling is per signal, without replacement.
            if signal_candidate is not None and signal_candidate in orders:
                eligible = [r for r in random_candidates
                    if r["entry_i"] != signal_candidate["entry_i"]
                    and r["bucket"] == signal_candidate["bucket"]]
                opp["eligible_random_candidates"] = len(eligible)
                opp["eligible_random_after_confirm"] = sum(
                    r["entry_i"] > signal_candidate["entry_i"] for r in eligible)
                if eligible:
                    picked = rng.choice(len(eligible), size=min(n_controls, len(eligible)), replace=False)
                    for control_no, pick in enumerate(picked):
                        row = dict(eligible[int(pick)])
                        row.update(trade_id=f"{pair_id}-c{control_no}", pair_id=pair_id,
                                   population="random")
                        orders.append(row)
                if len(eligible) < n_controls:
                    unmatched += 1
                    opp["unmatched_controls"] = n_controls - len(eligible)
                else:
                    opp["unmatched_controls"] = 0
                updates.extend(_pending_updates(f, h, signal_candidate, side, week_end,
                                                monday_high, monday_low, level,
                                                segment_ends, symbol))
                for control in [r for r in orders if r["pair_id"] == pair_id and r["population"] == "random"]:
                    if control["trade_id"] == pair_id:
                        continue
                    updates.extend(_pending_updates(f, h, control, side, week_end,
                                                    monday_high, monday_low, level,
                                                    segment_ends, symbol))

            opportunities.append(opp)

    opp_df = pd.DataFrame(opportunities)
    order_df = pd.DataFrame(orders)
    update_df = pd.DataFrame(updates)
    if len(order_df):
        order_df = order_df.sort_values(["entry_i", "population", "trade_id"], kind="stable").reset_index(drop=True)
    if len(update_df):
        update_df = update_df.sort_values(["bar_i", "trade_id"], kind="stable").reset_index(drop=True)
    diagnostics = dict(first_touches=len(opp_df), signals=int(order_df.population.eq("signal").sum()) if len(order_df) else 0,
                       random=int(order_df.population.eq("random").sum()) if len(order_df) else 0,
                       unmatched=unmatched, complete_15m_bars=int(h.complete.sum()),
                       warm_15m_bars=int(h.ma_warm.sum()))
    return dict(h=h, opportunities=opp_df, orders=order_df, updates=update_df,
                diagnostics=diagnostics)


def _pending_updates(f: pd.DataFrame, h: pd.DataFrame, order: dict, side: int,
                     week_end: pd.Timestamp, monday_high: float, monday_low: float,
                     level: float, segment_ends: np.ndarray, symbol: str) -> list[dict]:
    """Emit only post-submission close amendments/cancels for one pending row."""
    result: list[dict] = []
    entry_i = int(order["entry_i"])
    end_i = min(int(order["end_i"]), int(order["expire_i"]))
    start_h = int(order["entry_15m_i"]) + 1
    previous_h = int(order["entry_15m_i"])
    # Continue the cumulative extreme state that was observed before submission.
    touch_i = int(order.get("touch_i", -1))
    if touch_i < 0:
        # The caller fills this in before invoking us; defensive no-op keeps
        # malformed external rows from receiving uncausal amendments.
        return result
    touch_h = int(h.index.get_indexer([f.index[touch_i].floor("15min")])[0])
    if touch_h < 0 or not bool(h.complete.iloc[touch_h]):
        return result
    running = float(order["running_extreme"])
    new_extremes = int(order["new_extremes"])
    gap_pending = False
    allow_session_gaps = symbol == "XAUUSD"
    for j in range(start_h, len(h)):
        if h.index[j] + STEP15 > week_end:
            break
        if not bool(h.complete.iloc[j]):
            gap_pending = True
            continue
        bar_i = int(h.global_i.iloc[j])
        if bar_i > end_i:
            if not allow_session_gaps and gap_pending and previous_h >= 0:
                result.append(dict(trade_id=order["trade_id"], bar_i=end_i,
                                   action="cancel", status="gap_cancel"))
            break
        discontinuity = gap_pending or (previous_h >= 0 and h.index[j] - h.index[previous_h] != STEP15)
        if discontinuity and not allow_session_gaps:
            if bar_i <= end_i:
                result.append(dict(trade_id=order["trade_id"], bar_i=bar_i,
                                   action="cancel", status="gap_cancel"))
            break
        gap_pending = False
        previous_h = j

        extreme = float(h.low.iloc[j] if side == 1 else h.high.iloc[j])
        refreshed = extreme < running if side == 1 else extreme > running
        if refreshed:
            running = extreme
            new_extremes += 1
            if new_extremes >= 3:
                result.append(dict(trade_id=order["trade_id"], bar_i=bar_i,
                                   action="cancel", status="third_new_extreme"))
                break

        geom = _geometry(h.iloc[j], side, monday_high, monday_low)
        if not geom.get("valid"):
            result.append(dict(trade_id=order["trade_id"], bar_i=bar_i,
                               action="cancel", status="invalid_amendment_geometry"))
            break
        result.append(dict(trade_id=order["trade_id"], bar_i=bar_i,
                           action="reprice", entry=float(geom["entry"]),
                           stop=float(geom["stop"]), risk=float(geom["risk"])))
    return result


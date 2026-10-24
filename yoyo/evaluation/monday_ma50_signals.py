"""Prepare causal Monday 1.618 / 5m SMA50 rejection candidates.

The Owner-confirmed rule observes the first post-Monday 1.618 touch per side,
then waits for a completed-close SMA50 cross and a later wick retest whose close
remains on the trade side. This module prepares signal and timing-control rows
only; it does not execute, stop, target, or score trades. SMA50 is calculated
from the 50 most recent complete 5m closes through each decision bar. Crypto
history resets at gaps; XAU history carries across ordinary session closures
inside one source quality segment.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_ma6_signals import (
    STEP5,
    _prefix_known,
    _segment_ends,
    _week_expiry_i,
    _week_start_utc,
)
from yoyo.evaluation.monday_parameter_signals import base_context

OHLC = ("open", "high", "low", "close")
CONTROLS_PER_SIGNAL = 3
SPLIT_TIME = pd.Timestamp("2023-01-01T00:00:00Z")

ENTRY_COLUMNS = (
    "trade_id", "pair_id", "population", "control_id", "symbol", "week",
    "side", "entry_i", "entry", "entry_time", "stop", "risk",
    "target_price", "end_i", "touch_i", "touch_open_time", "cross_i",
    "confirm_i", "bucket", "segment",
)

OPPORTUNITY_COLUMNS = (
    "opportunity_id", "pair_id", "symbol", "week", "side", "level",
    "monday_high", "monday_low", "touch_i", "touch_open_time", "end_i",
    "scan_end_i", "cross_i", "confirm_i", "entry_i", "entry", "stop",
    "risk", "target_price", "bucket", "cross_count", "warm_bars",
    "status", "reason", "monitor_end_reason",
)


def _same_value(left: Any, right: Any) -> bool:
    """Compare adjacent quality-segment labels, including paired missing labels."""
    if pd.isna(left) and pd.isna(right):
        return True
    return bool(left == right)


def _contiguous_groups(f: pd.DataFrame, complete: np.ndarray,
                       symbol: str) -> list[np.ndarray]:
    """Return SMA history groups under the existing source-boundary contract."""
    n = len(f)
    if not n:
        return []
    if symbol == "XAUUSD":
        if "quality_segment" not in f:
            return [np.arange(n, dtype=np.int64)]
        labels = f.quality_segment.to_numpy()
        starts = [0]
        for i in range(1, n):
            if not _same_value(labels[i - 1], labels[i]):
                starts.append(i)
        ends = [*starts[1:], n]
        return [np.arange(a, b, dtype=np.int64) for a, b in zip(starts, ends)]

    times = f.index.asi8
    breaks = ~(complete[:-1] & complete[1:] & (np.diff(times) == STEP5.value))
    starts = np.r_[0, np.flatnonzero(breaks) + 1]
    ends = np.r_[starts[1:], n]
    return [np.arange(a, b, dtype=np.int64) for a, b in zip(starts, ends)]


def _sma50(f: pd.DataFrame, complete: np.ndarray,
           symbol: str) -> np.ndarray:
    """Compute close SMA50 on complete observations, causally and segment-safe."""
    result = np.full(len(f), np.nan, dtype="float64")
    for group in _contiguous_groups(f, complete, symbol):
        valid = group[complete[group]]
        if len(valid) < 50:
            continue
        closes = pd.Series(f.close.iloc[valid].to_numpy(dtype="float64"))
        result[valid] = closes.rolling(50, min_periods=50).mean().to_numpy()
    return result


def _previous_complete(f: pd.DataFrame, complete: np.ndarray,
                       symbol: str) -> np.ndarray:
    """Map each bar to its immediately preceding complete close in this source."""
    previous = np.full(len(f), -1, dtype=np.int64)
    if len(f) < 2:
        return previous
    same_segment = np.ones(len(f), dtype=bool)
    if symbol == "XAUUSD" and "quality_segment" in f:
        labels = f.quality_segment.to_numpy()
        same_segment[1:] = [
            _same_value(labels[i - 1], labels[i]) for i in range(1, len(f))
        ]
    else:
        same_segment[1:] = np.diff(f.index.asi8) == STEP5.value
    usable = complete[1:] & complete[:-1] & same_segment[1:]
    positions = np.flatnonzero(usable) + 1
    previous[positions] = positions - 1
    return previous


def _geometry(entry: float, stop: float, target: float,
              side: int) -> dict[str, Any]:
    """Validate fixed touch-stop / far Monday-edge geometry at a known close."""
    risk = side * (entry - stop)
    if not np.isfinite([entry, stop, target, risk]).all() or min(entry, stop, target) <= 0:
        return {"valid": False, "reason": "nonfinite_or_nonpositive_price", "risk": risk}
    if risk <= 0:
        return {"valid": False, "reason": "nonpositive_risk", "risk": float(risk)}
    if side * (target - entry) <= 0:
        return {"valid": False, "reason": "target_not_favorable", "risk": float(risk)}
    return {"valid": True, "reason": "", "risk": float(risk)}


def _monitor_end_reason(f: pd.DataFrame, symbol: str, end_i: int,
                        deadline_i: int, week_end: pd.Timestamp) -> str:
    """Explain the causal cutoff for an unconfirmed opportunity."""
    if end_i < 0:
        return "no_observed_bar"
    observed_close = f.index[end_i] + STEP5
    last_allowed_close = week_end - STEP5
    if symbol != "XAUUSD":
        if (deadline_i >= 0 and end_i == deadline_i
                and observed_close == last_allowed_close
                and float(f["count"].iloc[end_i]) == 5):
            return "week_expiry"
        if end_i >= len(f) - 1:
            return "source_end"
        return "source_gap"

    # XAU can be closed over the normal weekend. In that case, a same-quality
    # observation in the next NY week proves that the deadline passed; a source
    # quality-segment break still censors the opportunity.
    if deadline_i >= 0 and end_i == deadline_i:
        if observed_close == last_allowed_close and float(f["count"].iloc[end_i]) == 5:
            return "week_expiry"
        next_week_i = int(f.index.searchsorted(week_end, side="left"))
        if next_week_i < len(f):
            if "quality_segment" not in f:
                return "week_expiry"
            labels = f["quality_segment"].to_numpy()
            if _same_value(labels[end_i], labels[next_week_i]):
                return "week_expiry"
            return "quality_segment_end"
    if end_i >= len(f) - 1:
        return "source_end"
    if "quality_segment" in f:
        labels = f["quality_segment"].to_numpy()
        if not _same_value(labels[end_i], labels[end_i + 1]):
            return "quality_segment_end"
    return "quality_segment_end"


def _timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _entry_row(*, pair_id: str, trade_id: str, population: str,
               control_id: int, symbol: str, week: str, side: int,
               entry_i: int, entry: float, stop: float, risk: float,
               target: float, end_i: int, touch_i: int, cross_i: int,
               bucket: int, f: pd.DataFrame, split_time: pd.Timestamp) -> dict:
    close_time = f.index[entry_i] + STEP5
    return {
        "trade_id": trade_id,
        "pair_id": pair_id,
        "population": population,
        "control_id": int(control_id),
        "symbol": symbol,
        "week": week,
        "side": int(side),
        "entry_i": int(entry_i),
        "entry": float(entry),
        "entry_time": close_time,
        "stop": float(stop),
        "risk": float(risk),
        "target_price": float(target),
        "end_i": int(end_i),
        "touch_i": int(touch_i),
        "touch_open_time": f.index[touch_i],
        "cross_i": int(cross_i),
        "confirm_i": int(entry_i),
        "bucket": int(bucket),
        "segment": int(close_time >= split_time),
    }


def prepare(f: pd.DataFrame, symbol: str, cfg: dict | None = None,
            blocked=()) -> dict[str, Any]:
    """Prepare all first-touch opportunities and their causal candidate rows.

    Input bars are UTC-open-indexed 5m OHLC plus ``count``. The result has
    ``opportunities``, ``entries`` and ``diagnostics``. Signal ``entry_i`` and
    ``confirm_i`` point to the complete retest-close bar; ``entry_time`` is its
    UTC close. Timing controls are sampled without replacement from complete,
    warm closes strictly after the touch through the NY-week deadline and source
    boundary, matching signal side, week and lagged causal volatility bucket.
    No future exit path or outcome enters candidate selection.
    """
    cfg = {} if cfg is None else dict(cfg)
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("symbol is required")
    if int(cfg.get("controls_per_signal", CONTROLS_PER_SIGNAL)) != CONTROLS_PER_SIGNAL:
        raise ValueError(f"controls_per_signal is fixed at {CONTROLS_PER_SIGNAL}")
    split_time = _timestamp(cfg.get("split", SPLIT_TIME))
    seed = int(cfg.get("seed", 20261024))

    if not isinstance(f.index, pd.DatetimeIndex) or f.index.tz is None:
        raise ValueError("f must have a timezone-aware 5m OPEN DatetimeIndex")
    f = f.copy(deep=False)
    if str(f.index.tz) not in ("UTC", "UTC+00:00"):
        f = f.copy()
        f.index = f.index.tz_convert("UTC")
    f.index = f.index.as_unit("ns")
    if not f.index.is_monotonic_increasing or f.index.has_duplicates:
        raise ValueError("f index must be chronological and unique")
    if len(f) and (f.index.asi8 % STEP5.value).any():
        raise ValueError("f timestamps must align to UTC 5m OPEN boundaries")
    missing = [name for name in (*OHLC, "count") if name not in f]
    if missing:
        raise ValueError(f"f is missing required columns: {missing}")
    if len(f):
        prices = f.loc[:, list(OHLC)].to_numpy(dtype="float64")
        if not np.isfinite(prices).all() or (prices <= 0).any():
            raise ValueError("OHLC must contain finite positive prices")
        if ((f.low > f[["open", "close"]].min(axis=1)).any()
                or (f.high < f[["open", "close"]].max(axis=1)).any()
                or (f.high < f.low).any()):
            raise ValueError("Invalid OHLC geometry")
        counts = pd.to_numeric(f["count"], errors="coerce").to_numpy(dtype="float64")
        if not np.isfinite(counts).all() or (counts < 0).any():
            raise ValueError("count must be finite and non-negative")
        f = f.copy(deep=False)
        f["count"] = counts

    if f.empty:
        return {
            "opportunities": pd.DataFrame(columns=OPPORTUNITY_COLUMNS),
            "entries": pd.DataFrame(columns=ENTRY_COLUMNS),
            "diagnostics": {
                "schema_version": 1, "symbol": symbol,
                "quality": {"input_rows": 0, "complete_5m_rows": 0,
                            "incomplete_rows": 0, "source_segments": 0,
                            "sma50_ready_rows": 0,
                            "unknown_crypto_prefix_rows": 0},
                "funnel": {"first_touches": 0, "valid_prefix": 0,
                           "sma50_warm": 0, "cross": 0, "retest": 0,
                           "signal": 0, "invalid_prefix": 0,
                           "invalid_geometry": 0, "no_cross": 0,
                           "no_retest": 0, "expired": 0, "censored": 0,
                           "no_observed_post_entry": 0},
                "random_controls": {"requested": 0, "available": 0,
                                    "shortfall": 0, "signals_with_controls": 0},
            },
        }

    base = base_context(f, symbol, blocked)
    f = base["f"]
    complete = f["count"].to_numpy(dtype="float64") == 5
    known_prefix = (_prefix_known(base) if symbol != "XAUUSD"
                    else np.ones(len(f), dtype=bool))
    segment_ends = _segment_ends(f, symbol)
    sma = _sma50(f, complete, symbol)
    previous = _previous_complete(f, complete, symbol)
    local = base["local5"]
    weeks = base["week5"]
    rng = np.random.default_rng(seed)

    opportunities: list[dict[str, Any]] = []
    signal_entries: list[dict[str, Any]] = []
    control_requests: list[dict[str, Any]] = []
    quality_segments = (int(f.quality_segment.nunique(dropna=False))
                        if symbol == "XAUUSD" and "quality_segment" in f else 1)

    anchors = base["anchors"].sort_index()
    for week, anchor in anchors.iterrows():
        week = str(week)
        monday_high, monday_low = float(anchor.high), float(anchor.low)
        if not np.isfinite([monday_high, monday_low]).all() or monday_high <= monday_low:
            continue
        week_end = _week_start_utc(
            (pd.Timestamp(week) + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        )
        deadline_i = _week_expiry_i(f, week_end)
        # An entry is a close-time decision. A close exactly at next Monday
        # 00:00 belongs to the next NY week and is outside this opportunity.
        if deadline_i >= 0 and f.index[deadline_i] + STEP5 >= week_end:
            deadline_i -= 1
        in_week = (weeks == week) & (local.dayofweek >= 1) & complete
        week_rows = np.flatnonzero(in_week)
        if not len(week_rows):
            continue

        for side in (1, -1):
            level = (monday_low - 0.618 * (monday_high - monday_low)
                     if side == 1 else monday_high + 0.618 * (monday_high - monday_low))
            touches = (f.low.to_numpy()[week_rows] <= level if side == 1
                       else f.high.to_numpy()[week_rows] >= level)
            hit_rows = week_rows[np.flatnonzero(touches)]
            if not len(hit_rows):
                continue

            touch_i = int(hit_rows[0])
            stop = float(f.low.iloc[touch_i] if side == 1 else f.high.iloc[touch_i])
            target = float(monday_high if side == 1 else monday_low)
            pair_id = f"{symbol}-{week}-{side:+d}"
            end_i = int(segment_ends[touch_i])
            scan_end_i = min(end_i, deadline_i) if deadline_i >= 0 else -1
            if scan_end_i < touch_i:
                scan_end_i = touch_i
            monitor_end_reason = _monitor_end_reason(
                f, symbol, scan_end_i, deadline_i, week_end
            )
            opportunity: dict[str, Any] = {
                "opportunity_id": pair_id,
                "pair_id": pair_id,
                "symbol": symbol,
                "week": week,
                "side": int(side),
                "level": float(level),
                "monday_high": monday_high,
                "monday_low": monday_low,
                "touch_i": touch_i,
                "touch_open_time": f.index[touch_i],
                "end_i": end_i,
                "scan_end_i": int(scan_end_i),
                "cross_i": -1,
                "confirm_i": -1,
                "entry_i": -1,
                "entry": np.nan,
                "stop": stop,
                "risk": np.nan,
                "target_price": target,
                "bucket": -999,
                "cross_count": 0,
                "warm_bars": 0,
                "status": "pending",
                "reason": "",
                "monitor_end_reason": monitor_end_reason,
            }

            if symbol != "XAUUSD" and not known_prefix[touch_i]:
                opportunity.update(status="invalid", reason="unknown_monday_prefix")
                opportunities.append(opportunity)
                continue

            active_cross_i = -1
            last_cross_i = -1
            warm_bars = 0
            cross_count = 0
            confirmed_i = -1
            if scan_end_i >= touch_i:
                for i in range(touch_i, scan_end_i + 1):
                    if not complete[i]:
                        active_cross_i = -1
                        continue
                    if np.isfinite(sma[i]):
                        warm_bars += 1
                    prev_i = int(previous[i])
                    can_cross = (prev_i >= 0 and np.isfinite(sma[prev_i])
                                 and np.isfinite(sma[i]))
                    if active_cross_i >= 0 and i > active_cross_i:
                        wrong_side = (f.close.iloc[i] >= sma[i] if side == -1
                                      else f.close.iloc[i] <= sma[i])
                        if wrong_side:
                            active_cross_i = -1
                        else:
                            retest = (f.high.iloc[i] >= sma[i] if side == -1
                                      else f.low.iloc[i] <= sma[i])
                            if retest:
                                confirmed_i = i
                                break
                    if active_cross_i < 0 and can_cross:
                        crossed = (
                            f.close.iloc[prev_i] >= sma[prev_i]
                            and f.close.iloc[i] < sma[i]
                            if side == -1 else
                            f.close.iloc[prev_i] <= sma[prev_i]
                            and f.close.iloc[i] > sma[i]
                        )
                        if crossed:
                            active_cross_i = i
                            last_cross_i = i
                            cross_count += 1

            opportunity["cross_i"] = int(active_cross_i if confirmed_i >= 0 else last_cross_i)
            opportunity["confirm_i"] = int(confirmed_i)
            opportunity["cross_count"] = int(cross_count)
            opportunity["warm_bars"] = int(warm_bars)

            if confirmed_i < 0:
                if warm_bars == 0:
                    state = "no_sma50_warmup"
                elif cross_count == 0:
                    state = "no_cross"
                else:
                    state = "no_retest"
                if monitor_end_reason == "week_expiry":
                    opportunity.update(status="expired", reason=state)
                else:
                    opportunity.update(status="censored", reason=state)
                opportunities.append(opportunity)
                continue

            entry = float(f.close.iloc[confirmed_i])
            geometry = _geometry(entry, stop, target, side)
            opportunity.update(
                entry_i=int(confirmed_i), entry=entry,
                cross_i=int(active_cross_i), confirm_i=int(confirmed_i),
                bucket=int(base["bucket"][confirmed_i]),
                risk=geometry["risk"],
            )
            if confirmed_i >= end_i:
                opportunity.update(status="censored", reason="no_observed_post_entry")
                opportunities.append(opportunity)
                continue
            if not geometry["valid"]:
                opportunity.update(status="invalid_geometry", reason=geometry["reason"])
                opportunities.append(opportunity)
                continue

            opportunity.update(status="signal", reason="")
            signal_entries.append(_entry_row(
                pair_id=pair_id, trade_id=f"{pair_id}-signal", population="signal",
                control_id=0, symbol=symbol, week=week, side=side,
                entry_i=confirmed_i, entry=entry, stop=stop,
                risk=geometry["risk"], target=target, end_i=end_i,
                touch_i=touch_i, cross_i=active_cross_i,
                bucket=int(base["bucket"][confirmed_i]), f=f,
                split_time=split_time,
            ))
            control_requests.append({
                "pair_id": pair_id, "symbol": symbol, "week": week,
                "side": side, "touch_i": touch_i, "scan_end_i": scan_end_i,
                "end_i": end_i, "cross_i": active_cross_i,
                "signal_i": confirmed_i, "bucket": int(base["bucket"][confirmed_i]),
                "stop": stop, "target": target,
            })
            opportunities.append(opportunity)

    control_entries: list[dict[str, Any]] = []
    shortfalls = 0
    for request in control_requests:
        eligible = []
        for i in range(request["touch_i"] + 1, request["scan_end_i"] + 1):
            if (i == request["signal_i"] or i >= request["end_i"]
                    or not complete[i] or not np.isfinite(sma[i])):
                continue
            bucket = int(base["bucket"][i])
            if bucket == -999 or bucket != request["bucket"]:
                continue
            entry = float(f.close.iloc[i])
            geometry = _geometry(entry, request["stop"], request["target"], request["side"])
            if geometry["valid"]:
                eligible.append((i, entry, geometry["risk"]))
        take = min(CONTROLS_PER_SIGNAL, len(eligible))
        shortfalls += CONTROLS_PER_SIGNAL - take
        if take == 0:
            continue
        chosen = rng.choice(len(eligible), size=take, replace=False)
        for control_id, pool_i in enumerate(chosen, start=1):
            entry_i, entry, risk = eligible[int(pool_i)]
            control_entries.append(_entry_row(
                pair_id=request["pair_id"],
                trade_id=f"{request['pair_id']}-c{control_id}",
                population="random", control_id=control_id,
                symbol=request["symbol"], week=request["week"],
                side=request["side"], entry_i=entry_i, entry=entry,
                stop=request["stop"], risk=risk, target=request["target"],
                end_i=request["end_i"], touch_i=request["touch_i"],
                cross_i=-1, bucket=request["bucket"], f=f,
                split_time=split_time,
            ))

    opportunities_df = pd.DataFrame(opportunities, columns=OPPORTUNITY_COLUMNS)
    entries_df = pd.DataFrame([*signal_entries, *control_entries], columns=ENTRY_COLUMNS)
    if len(entries_df):
        entries_df = entries_df.sort_values(
            ["entry_i", "population", "pair_id", "control_id"], kind="stable"
        ).reset_index(drop=True)

    statuses = Counter(row["status"] for row in opportunities)
    funnel = {
        "first_touches": int(len(opportunities)),
        "valid_prefix": int(len(opportunities) - statuses["invalid"]),
        "sma50_warm": int(sum(row["warm_bars"] > 0 for row in opportunities)),
        "cross": int(sum(row["cross_count"] > 0 for row in opportunities)),
        "retest": int(sum(row["confirm_i"] >= 0 for row in opportunities)),
        "signal": int(statuses["signal"]),
        "invalid_prefix": int(statuses["invalid"]),
        "invalid_geometry": int(statuses["invalid_geometry"]),
        "no_cross": int(sum(row["reason"] == "no_cross" for row in opportunities)),
        "no_retest": int(sum(row["reason"] == "no_retest" for row in opportunities)),
        "expired": int(statuses["expired"]),
        "censored": int(statuses["censored"]),
        "no_observed_post_entry": int(sum(
            row["reason"] == "no_observed_post_entry" for row in opportunities
        )),
    }
    requested = len(signal_entries) * CONTROLS_PER_SIGNAL
    random_count = len(control_entries)
    diagnostics = {
        "schema_version": 1,
        "symbol": symbol,
        "quality": {
            "input_rows": int(len(f)),
            "complete_5m_rows": int(complete.sum()),
            "incomplete_rows": int((~complete).sum()),
            "source_segments": int(quality_segments),
            "sma50_ready_rows": int(np.isfinite(sma).sum()),
            "unknown_crypto_prefix_rows": int((~known_prefix).sum()) if symbol != "XAUUSD" else 0,
        },
        "funnel": funnel,
        "random_controls": {
            "requested": int(requested),
            "available": int(random_count),
            "shortfall": int(shortfalls),
            "signals_with_controls": int(sum(
                1 for row in signal_entries
                if any(c["pair_id"] == row["pair_id"] for c in control_entries)
            )),
        },
    }
    return {"opportunities": opportunities_df, "entries": entries_df,
            "diagnostics": diagnostics}

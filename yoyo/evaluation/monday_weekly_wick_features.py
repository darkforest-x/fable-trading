"""Causal close-time features for first NY-Monday weekly 1.618 wick signals.

Each row is keyed by the confirmed event and scored at its own 1h/4h/12h
signal-bar close. The Monday anchor is rebuilt from the event symbol's frozen
H1 OHLC. Rolling price features use only fully closed H1 bars at or before
that close; missing history or a timestamp gap makes the affected window NaN.
No volume is used because the frozen contract has no reliable volume field.

Run after ``monday_weekly_wick_census3r`` has written ``first_breaches.csv.gz``:

    ~/.local/bin/vectorbt-python -m yoyo.evaluation.monday_weekly_wick_features

The stable reusable entry points are :func:`build_feature_row`,
:func:`build_symbol_features`, and :func:`build_features`.
"""
from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-binance-monday-wick-score3r-20261116-v1"
EVENTS = EXP / "first_breaches.csv.gz"
OUTPUT = EXP / "monday_weekly_wick_features.csv.gz"
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
NY = "America/New_York"
BTC_SYMBOL = "BTCUSDT"
CONFIRMED_TF = (1, 4, 12)
CONFIRMED_STATUSES = ("entered", "confirmed_no_next_open", "confirmed_open_beyond_stop")

# Monthly-score-compatible baseline fields, plus weekly Monday and signal-bar
# geometry. The training code can compare this stable baseline to FEATURES.
CORE_FEATURES = (
    "side",
    "tf4",
    "tf12",
    "week_range_frac",
    "week_close_position",
    "breach_delay_days",
    "breach_offset_tf_frac",
    "other_side_touch_count_24h",
    "side_return_24h",
    "side_return_168h",
    "tr_pct_24h",
    "tr_pct_168h",
    "wick_outside_frac",
    "log1p_wick_reclaim_ratio",
    "log1p_outside_wick_to_body_ratio",
    "signal_close_risk_frac",
    "monday_side_return",
    "monday_efficiency",
    "wick_depth_bar_frac",
    "body_frac_range",
    "signal_close_position_side",
    "signal_range_week_frac",
)

# Expanded causal context: additional trends, realized movement, pre-signal
# ranges/locations, and BTC context as-of each event's own close. BTC features
# are NaN if the BTC tape does not have complete causal coverage.
FEATURES = (
    *CORE_FEATURES,
    "side_return_6h",
    "side_return_72h",
    "side_return_336h",
    "realized_vol_pct_24h",
    "realized_vol_pct_168h",
    "realized_vol_pct_336h",
    "range_pct_24h",
    "range_pct_168h",
    "range_pct_336h",
    "range_position_24h",
    "range_position_168h",
    "range_position_336h",
    "efficiency_24h",
    "efficiency_168h",
    "efficiency_336h",
    "btc_side_return_24h",
    "btc_side_return_168h",
    "btc_tr_pct_24h",
)

BASE_COLUMNS = (
    "event_id",
    "symbol",
    "week",
    "side",
    "tf",
    "bar_ms",
    "close_ms",
    "first_breach_ms",
    "status",
)
OUTPUT_COLUMNS = tuple(dict.fromkeys((*BASE_COLUMNS, *FEATURES)))
FEATURE_GROUPS = {"core": CORE_FEATURES, "expanded": FEATURES}

_EVENT_REQUIRED = (
    "event_id", "symbol", "week", "side", "tf", "bar_ms", "close_ms",
    "first_breach_ms", "anchor_start_ms", "anchor_ready_ms", "week_high",
    "week_low", "rail",
)


def _as_hourly(raw, asof_close_ms: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Normalize frozen H1 OHLC and, when requested, discard post-close rows.

    DataFrame input may use ``open_ms`` or ``timestamp`` plus named OHLC
    columns. The feature contract itself is an N x 5 array in the order
    ``[open_ms, open, high, low, close]``.
    """
    if isinstance(raw, pd.DataFrame):
        frame = raw
        time_col = "open_ms" if "open_ms" in frame else "timestamp" if "timestamp" in frame else None
        if time_col is None or not {"open", "high", "low", "close"}.issubset(frame.columns):
            raise ValueError("Frozen H1 DataFrame must contain open_ms/timestamp and OHLC columns")
        time = frame[time_col]
        if not pd.api.types.is_numeric_dtype(time):
            parsed = pd.to_datetime(time, utc=True).dt.tz_localize(None)
            time = parsed.to_numpy(dtype="datetime64[ms]").astype(np.int64)
        raw = np.column_stack((np.asarray(time), frame[["open", "high", "low", "close"]].to_numpy()))
    values = np.asarray(raw, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 5:
        raise ValueError("Frozen hourly tape must be N x 5 [open_ms, O, H, L, C]")
    if len(values):
        raw_ts = values[:, 0]
        ts = raw_ts.astype(np.int64)
        if (np.any(raw_ts != ts) or np.any(ts % HOUR_MS)
                or np.any(np.diff(ts) <= 0)):
            raise ValueError("Frozen hourly tape has invalid or unsorted timestamps")
        if asof_close_ms is not None:
            stop = int(np.searchsorted(ts, int(asof_close_ms), side="left"))
            values, ts = values[:stop], ts[:stop]
        if len(values):
            o, h, l, c = values[:, 1:5].T
            if (not np.isfinite(values).all() or np.any(o <= 0) or np.any(h <= 0)
                    or np.any(l <= 0) or np.any(c <= 0)
                    or np.any(h < np.maximum(o, c)) or np.any(l > np.minimum(o, c))):
                raise ValueError("Frozen hourly tape has invalid OHLC before the feature cutoff")
    else:
        ts = np.empty(0, dtype=np.int64)
    return values, ts


def _contiguous(ts: np.ndarray, start: int, stop: int) -> bool:
    """Whether half-open H1 index range [start, stop) has no missing hour."""
    return start >= 0 and stop <= len(ts) and stop >= start and (
        stop - start <= 1 or bool(np.all(np.diff(ts[start:stop]) == HOUR_MS))
    )


def _event_timing(event: Mapping) -> tuple[int, int, int, int, int]:
    """Read and validate event clocks without consulting status or outcomes."""
    missing = set(_EVENT_REQUIRED).difference(event)
    if missing:
        raise ValueError(f"Weekly wick event lacks fields: {sorted(missing)}")
    tf = int(event["tf"])
    bar_ms, close_ms = int(event["bar_ms"]), int(event["close_ms"])
    breach_ms = int(event["first_breach_ms"])
    anchor_start_ms, anchor_ready_ms = int(event["anchor_start_ms"]), int(event["anchor_ready_ms"])
    if tf not in CONFIRMED_TF or close_ms != bar_ms + tf * HOUR_MS:
        raise ValueError(f"Invalid selected timeframe bounds for {event['event_id']}")
    if bar_ms % (tf * HOUR_MS):
        raise ValueError(f"Selected signal bar is not UTC-aligned for {event['event_id']}")
    if not (bar_ms <= breach_ms < close_ms):
        raise ValueError(f"First breach falls outside signal bar for {event['event_id']}")
    if anchor_ready_ms - anchor_start_ms != DAY_MS:
        raise ValueError(f"NY Monday anchor is not exactly 24 hours for {event['event_id']}")
    if anchor_ready_ms > bar_ms:
        raise ValueError(f"Signal bar starts before its frozen Monday anchor completes: {event['event_id']}")
    if anchor_start_ms % HOUR_MS or anchor_ready_ms % HOUR_MS:
        raise ValueError(f"NY Monday anchor is not H1-aligned for {event['event_id']}")
    local_start = pd.Timestamp(anchor_start_ms, unit="ms", tz="UTC").tz_convert(NY)
    if local_start.hour or local_start.minute or local_start.second or local_start.dayofweek != 0:
        raise ValueError(f"Anchor does not begin at NY Monday 00:00 for {event['event_id']}")
    if str(event["week"])[:10] != local_start.strftime("%Y-%m-%d"):
        raise ValueError(f"Event week disagrees with NY Monday anchor for {event['event_id']}")
    return tf, bar_ms, close_ms, breach_ms, anchor_start_ms


def _signal_bar(event: Mapping, values: np.ndarray, ts: np.ndarray,
                tf: int, bar_ms: int, close_ms: int) -> tuple[float, float, float, float]:
    start = int(np.searchsorted(ts, bar_ms, side="left"))
    stop = int(np.searchsorted(ts, close_ms, side="left"))
    if (stop - start != tf or not _contiguous(ts, start, stop)
            or start >= len(ts) or ts[start] != bar_ms or ts[stop - 1] != close_ms - HOUR_MS):
        raise ValueError(f"Selected signal bar is incomplete in frozen tape: {event['event_id']}")
    block = values[start:stop]
    actual = (float(block[0, 1]), float(np.max(block[:, 2])),
              float(np.min(block[:, 3])), float(block[-1, 4]))
    # Stored event OHLC is part of the causal signal definition. Verify it
    # against the frozen tape, while never reading entry/stop/outcome fields.
    for name, value in zip(("bar_open", "bar_high", "bar_low", "bar_close"), actual):
        if name in event and not np.isclose(value, float(event[name]), rtol=1e-12, atol=0.0):
            raise ValueError(f"Signal {name} differs from frozen tape: {event['event_id']}")
    return actual


def _anchor_features(event: Mapping, values: np.ndarray, ts: np.ndarray,
                     anchor_start_ms: int) -> tuple[float, float, float, float, float]:
    ready_ms = int(event["anchor_ready_ms"])
    start = int(np.searchsorted(ts, anchor_start_ms, side="left"))
    stop = int(np.searchsorted(ts, ready_ms, side="left"))
    if (stop - start != 24 or not _contiguous(ts, start, stop)
            or start >= len(ts) or ts[start] != anchor_start_ms
            or ts[stop - 1] != ready_ms - HOUR_MS):
        raise ValueError(f"Frozen NY Monday anchor is incomplete: {event['event_id']}")
    anchor = values[start:stop]
    week_high, week_low = float(np.max(anchor[:, 2])), float(np.min(anchor[:, 3]))
    span = week_high - week_low
    if not np.isfinite(span) or span <= 0:
        raise ValueError(f"Frozen NY Monday anchor has no range: {event['event_id']}")
    if (not np.isclose(week_high, float(event["week_high"]), rtol=1e-12, atol=0.0)
            or not np.isclose(week_low, float(event["week_low"]), rtol=1e-12, atol=0.0)):
        raise ValueError(f"Frozen Monday anchor differs from event ledger: {event['event_id']}")
    close = float(anchor[-1, 4])
    anchor_open = float(anchor[0, 1])
    close_position = (close - week_low) / span
    direction = int(event["side"])
    monday_return = direction * (close / anchor_open - 1.0)
    monday_moves = np.abs(np.diff(np.r_[anchor_open, anchor[:, 4]]))
    path = float(monday_moves.sum())
    efficiency = abs(close - anchor_open) / path if path > 0 else np.nan
    return span / week_low, close_position, monday_return, efficiency, close


def _window_values(values: np.ndarray, ts: np.ndarray, close_ms: int,
                   hours: int) -> dict[str, float]:
    """Causal trailing-window metrics; all windows include the signal close.

    ``return`` and realized volatility use the last ``hours`` completed H1
    close-to-close returns. TR and range use the last ``hours`` completed
    H1 candles. A gap anywhere within a metric's required history yields NaN
    for that metric. The feature docstring contract is OHLC only through the
    selected close, with each metric using exactly this trailing window.
    """
    end = int(np.searchsorted(ts, close_ms, side="left")) - 1
    if end < 0 or ts[end] != close_ms - HOUR_MS:
        return {"return": np.nan, "tr_pct": np.nan, "rv_pct": np.nan,
                "range_pct": np.nan, "range_position": np.nan, "efficiency": np.nan}
    current_close = float(values[end, 4])
    start = end - hours + 1
    has_bars = start >= 0 and _contiguous(ts, start, end + 1)
    has_closes = start > 0 and _contiguous(ts, start - 1, end + 1)
    result = {"return": np.nan, "tr_pct": np.nan, "rv_pct": np.nan,
              "range_pct": np.nan, "range_position": np.nan, "efficiency": np.nan}
    if has_closes:
        prior_close = float(values[start - 1, 4])
        result["return"] = current_close / prior_close - 1.0
        closes = values[start - 1:end + 1, 4]
        log_returns = np.diff(np.log(closes))
        result["rv_pct"] = float(np.std(log_returns, ddof=0))
        path = float(np.abs(np.diff(closes)).sum())
        result["efficiency"] = abs(current_close - prior_close) / path if path > 0 else np.nan
        bars = values[start:end + 1]
        prior = values[start - 1:end, 4]
        true_range = np.maximum(
            bars[:, 2] - bars[:, 3],
            np.maximum(np.abs(bars[:, 2] - prior), np.abs(bars[:, 3] - prior)),
        )
        result["tr_pct"] = float(np.mean(true_range) / current_close)
    if has_bars:
        bars = values[start:end + 1]
        high, low = float(np.max(bars[:, 2])), float(np.min(bars[:, 3]))
        width = high - low
        result["range_pct"] = width / current_close
        result["range_position"] = (current_close - low) / width if width > 0 else np.nan
    return result


def _context(values: np.ndarray, ts: np.ndarray, close_ms: int, side: int) -> dict[str, float]:
    contexts = {hours: _window_values(values, ts, close_ms, hours)
                for hours in (6, 24, 72, 168, 336)}
    result = {}
    for hours in (6, 24, 72, 168, 336):
        result[f"side_return_{hours}h"] = side * contexts[hours]["return"]
    for hours in (24, 168, 336):
        for metric in ("tr_pct", "rv_pct", "range_pct", "range_position", "efficiency"):
            result[f"{metric}_{hours}h"] = contexts[hours][metric]
    return result


def prepare_breadth_index(events: pd.DataFrame) -> dict[tuple[str, int], tuple[np.ndarray, np.ndarray]]:
    """Index all weekly first breaches by side and H1 close when they became known.

    Each breach's source ``first_breach_ms`` is an H1 open time, so the fact is
    only available at ``first_breach_ms + 1h``. ``status`` and timeframe
    confirmation are deliberately ignored: first-breach breadth is known from
    the source H1 bar even when that asset never qualifies at 1h/4h/12h.
    """
    needed = {"week", "symbol", "side", "first_breach_ms"}
    missing = needed.difference(events.columns)
    if missing:
        raise ValueError(f"First-breach ledger lacks columns: {sorted(missing)}")
    frame = events.loc[:, ["week", "symbol", "side", "first_breach_ms"]].dropna(
        subset=["week", "symbol", "side", "first_breach_ms"]
    ).copy()
    if frame.empty:
        return {}
    frame["week"] = frame["week"].astype(str).str.slice(0, 10)
    frame["symbol"] = frame["symbol"].astype(str)
    frame["side"] = pd.to_numeric(frame["side"], errors="raise").astype(int)
    if not frame.side.isin((-1, 1)).all():
        raise ValueError("First-breach ledger has invalid side values")
    touch_ms = pd.to_numeric(frame["first_breach_ms"], errors="raise").astype("int64")
    frame["known_ms"] = touch_ms + HOUR_MS
    result: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}
    for (week, side), group in frame.groupby(["week", "side"], sort=False):
        ordered = group.sort_values("known_ms", kind="stable")
        result[(str(week), int(side))] = (
            ordered["known_ms"].to_numpy(dtype=np.int64),
            ordered["symbol"].to_numpy(dtype=object),
        )
    return result


def _past_other_touch_count(event: Mapping, close_ms: int,
                            breadth_index: Mapping[tuple[str, int], tuple[np.ndarray, np.ndarray]] | None) -> float:
    """Count other same-side first breaches known by close_ms minus one hour.

    The right edge matches the monthly baseline: the last included source H1
    candle must have closed at least one hour before this signal close. This
    excludes first touches from the signal's final H1 candle, including a
    cross-asset touch in that same hour.
    """
    if breadth_index is None:
        return np.nan
    key = (str(event["week"])[:10], int(event["side"]))
    indexed = breadth_index.get(key)
    if indexed is None:
        return 0.0
    known_ms, symbols = indexed
    end_ms = int(close_ms) - HOUR_MS
    left = int(np.searchsorted(known_ms, end_ms - DAY_MS, side="left"))
    right = int(np.searchsorted(known_ms, end_ms, side="right"))
    window_symbols = symbols[left:right]
    if not len(window_symbols):
        return 0.0
    other = window_symbols[window_symbols != str(event["symbol"])]
    return float(len(np.unique(other))) if len(other) else 0.0


def _signal_geometry(event: Mapping, bar: tuple[float, float, float, float],
                     tf: int, bar_ms: int, breach_ms: int, monday_range_frac: float) -> dict[str, float]:
    side = int(event["side"])
    if side not in (-1, 1):
        raise ValueError(f"Invalid event side for {event['event_id']}")
    open_, high, low, close = bar
    rail = float(event["rail"])
    if rail <= 0:
        raise ValueError(f"Invalid weekly rail for {event['event_id']}")
    tip = low if side == 1 else high
    eps = rail * 1e-12
    if not (side * (open_ - rail) > eps and side * (close - rail) > eps
            and side * (tip - rail) < -eps):
        raise ValueError(f"Signal bar is not a strict outward wick with inward open/close: {event['event_id']}")
    outside = abs(tip - rail)
    span = high - low
    body = abs(close - open_)
    outside_wick = min(open_, close) - low if side == 1 else high - max(open_, close)
    if outside <= 0 or span <= 0 or outside_wick <= 0:
        raise ValueError(f"Signal bar has invalid strict-wick geometry: {event['event_id']}")
    stop = tip * (1.0 - side * 0.01)
    wick_reclaim = side * (close - tip) / outside
    wick_body = outside_wick / body if body > 0 else np.nan
    oriented_close_position = ((close - low) / span if side == 1 else (high - close) / span)
    return {
        "breach_delay_days": (breach_ms - int(event["anchor_ready_ms"])) / DAY_MS,
        "breach_offset_tf_frac": (breach_ms - bar_ms) / (tf * HOUR_MS),
        "wick_outside_frac": outside / rail,
        "log1p_wick_reclaim_ratio": float(np.log1p(max(0.0, wick_reclaim))),
        "log1p_outside_wick_to_body_ratio": (
            float(np.log1p(max(0.0, wick_body))) if np.isfinite(wick_body) else np.nan
        ),
        "signal_close_risk_frac": side * (close - stop) / close,
        "wick_depth_bar_frac": outside / span,
        "body_frac_range": body / span,
        "signal_close_position_side": oriented_close_position,
        "signal_range_week_frac": span / (monday_range_frac * float(event["week_low"])),
    }


def _finite_or_nan(features: Mapping[str, float], event_id: str) -> None:
    for name, value in features.items():
        if not (np.isfinite(value) or np.isnan(value)):
            raise ValueError(f"Non-finite feature {name} for {event_id}")


def _build_prepared_feature_row(event: Mapping, values: np.ndarray, ts: np.ndarray,
                                btc_values: np.ndarray | None, btc_ts: np.ndarray | None,
                                breadth_index=None) -> dict:
    """Build one row from already normalized tapes, slicing by close via views."""
    tf, bar_ms, close_ms, breach_ms, anchor_start_ms = _event_timing(event)
    own_stop = int(np.searchsorted(ts, close_ms, side="left"))
    own_values, own_ts = values[:own_stop], ts[:own_stop]
    bar = _signal_bar(event, own_values, own_ts, tf, bar_ms, close_ms)
    (week_range_frac, week_close_position, monday_side_return,
     monday_efficiency, _anchor_close) = _anchor_features(event, own_values, own_ts, anchor_start_ms)
    side = int(event["side"])
    rail = float(event["rail"])
    week_high, week_low = float(event["week_high"]), float(event["week_low"])
    expected_rail = week_low - .618 * (week_high - week_low) if side == 1 else week_high + .618 * (week_high - week_low)
    if not np.isclose(rail, expected_rail, rtol=1e-10, atol=max(abs(rail), 1.0) * 1e-12):
        raise ValueError(f"Event rail is not the frozen Monday 1.618 extension: {event['event_id']}")
    features = {
        "side": float(side),
        "tf4": float(tf == 4),
        "tf12": float(tf == 12),
        "week_range_frac": week_range_frac,
        "week_close_position": week_close_position,
        "monday_side_return": monday_side_return,
        "monday_efficiency": monday_efficiency,
        "other_side_touch_count_24h": _past_other_touch_count(event, close_ms, breadth_index),
        **_signal_geometry(event, bar, tf, bar_ms, breach_ms, week_range_frac),
    }
    own_context = _context(own_values, own_ts, close_ms, side)
    features.update({
        "side_return_6h": own_context["side_return_6h"],
        "side_return_72h": own_context["side_return_72h"],
        "side_return_336h": own_context["side_return_336h"],
        "side_return_24h": own_context["side_return_24h"],
        "side_return_168h": own_context["side_return_168h"],
        "tr_pct_24h": own_context["tr_pct_24h"],
        "tr_pct_168h": own_context["tr_pct_168h"],
        "realized_vol_pct_24h": own_context["rv_pct_24h"],
        "realized_vol_pct_168h": own_context["rv_pct_168h"],
        "realized_vol_pct_336h": own_context["rv_pct_336h"],
        "range_pct_24h": own_context["range_pct_24h"],
        "range_pct_168h": own_context["range_pct_168h"],
        "range_pct_336h": own_context["range_pct_336h"],
        "range_position_24h": own_context["range_position_24h"],
        "range_position_168h": own_context["range_position_168h"],
        "range_position_336h": own_context["range_position_336h"],
        "efficiency_24h": own_context["efficiency_24h"],
        "efficiency_168h": own_context["efficiency_168h"],
        "efficiency_336h": own_context["efficiency_336h"],
    })
    btc_context = {"btc_side_return_24h": np.nan, "btc_side_return_168h": np.nan,
                   "btc_tr_pct_24h": np.nan}
    if btc_values is not None and btc_ts is not None:
        btc_stop = int(np.searchsorted(btc_ts, close_ms, side="left"))
        btc_asof_values, btc_asof_ts = btc_values[:btc_stop], btc_ts[:btc_stop]
        context = _context(btc_asof_values, btc_asof_ts, close_ms, side)
        btc_context.update({
            "btc_side_return_24h": context["side_return_24h"],
            "btc_side_return_168h": context["side_return_168h"],
            "btc_tr_pct_24h": context["tr_pct_24h"],
        })
    features.update(btc_context)
    _finite_or_nan(features, str(event["event_id"]))
    return {
        "event_id": str(event["event_id"]),
        "symbol": str(event["symbol"]),
        "week": str(event["week"]),
        "side": side,
        "tf": tf,
        "bar_ms": bar_ms,
        "close_ms": close_ms,
        "first_breach_ms": breach_ms,
        "status": str(event.get("status", "")),
        **features,
    }


def build_feature_row(event: Mapping, frozen_tape, btc_tape=None,
                      breadth_index=None) -> dict:
    """Build one confirmed event row using only OHLC through ``close_ms``.

    ``frozen_tape`` and optional ``btc_tape`` use the N x 5 H1 contract
    ``[open_ms, open, high, low, close]``. Event ``status``, ``entry``,
    ``stop``, ``risk_frac``, and every outcome field are excluded from all
    feature calculations. Stop risk is rebuilt from the known wick tip using
    the fixed 1% stop buffer, matching the event contract.
    """
    _tf, _bar_ms, close_ms, _breach_ms, _anchor_start_ms = _event_timing(event)
    values, ts = _as_hourly(frozen_tape, close_ms)
    btc_values, btc_ts = None, None
    if btc_tape is not None:
        btc_values, btc_ts = _as_hourly(btc_tape, close_ms)
    return _build_prepared_feature_row(event, values, ts, btc_values, btc_ts, breadth_index)


def _is_signal_row(row: Mapping) -> bool:
    """Return whether a ledger row has complete signal-bar identity fields."""
    try:
        return (int(row.get("tf")) in CONFIRMED_TF
                and all(pd.notna(row.get(k)) for k in _EVENT_REQUIRED))
    except (TypeError, ValueError):
        return False


def _build_symbol_features_prepared(events: pd.DataFrame, values: np.ndarray, ts: np.ndarray,
                                    btc_values: np.ndarray | None, btc_ts: np.ndarray | None,
                                    breadth_index=None) -> pd.DataFrame:
    signals = events.loc[events.apply(_is_signal_row, axis=1)]
    if signals.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    rows = [_build_prepared_feature_row(row, values, ts, btc_values, btc_ts, breadth_index)
            for row in signals.to_dict("records")]
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def build_symbol_features(events: pd.DataFrame, frozen_tape, btc_tape=None,
                          breadth_index=None) -> pd.DataFrame:
    """Build features for all candidate rows for one symbol.

    Rows with no selected signal timeframe are skipped. Status is copied to
    the result for auditing only and never controls feature values.
    """
    if events.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    signals = events.loc[events.apply(_is_signal_row, axis=1)]
    if signals.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    max_close = int(pd.to_numeric(signals.close_ms).max())
    values, ts = _as_hourly(frozen_tape, max_close)
    btc_values, btc_ts = (None, None) if btc_tape is None else _as_hourly(btc_tape, max_close)
    return _build_symbol_features_prepared(signals, values, ts, btc_values, btc_ts, breadth_index)


def build_features(events: pd.DataFrame, frozen_tapes: Mapping[str, object],
                   btc_tape=None, breadth_index=None) -> pd.DataFrame:
    """Build one output row per confirmed candidate across symbol tapes.

    ``frozen_tapes`` maps symbols to cached N x 5 hourly tapes. If ``btc_tape``
    is omitted, ``BTCUSDT`` is read from that mapping when available. Every
    event uses BTC OHLC as-of its own ``close_ms``; a later BTC close cannot
    affect it.
    """
    if events.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    if breadth_index is None:
        breadth_index = prepare_breadth_index(events)
    events = events.loc[events.apply(_is_signal_row, axis=1)].copy()
    if events.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    btc = btc_tape if btc_tape is not None else frozen_tapes.get(BTC_SYMBOL)
    if btc is None:
        btc_values, btc_ts = None, None
    else:
        max_close = int(pd.to_numeric(events.close_ms).max())
        btc_values, btc_ts = _as_hourly(btc, max_close)
    outputs = []
    for symbol, group in events.groupby("symbol", sort=False):
        tape = frozen_tapes.get(str(symbol))
        if tape is None:
            raise KeyError(f"No frozen H1 tape for event symbol {symbol}")
        max_close = int(pd.to_numeric(group.close_ms).max())
        values, ts = _as_hourly(tape, max_close)
        outputs.append(_build_symbol_features_prepared(
            group, values, ts, btc_values, btc_ts, breadth_index
        ))
    if not outputs:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    result = pd.concat(outputs, ignore_index=True)
    if result.event_id.duplicated().any():
        raise ValueError("Weekly wick event ids must be unique in the feature output")
    return result.sort_values(["close_ms", "event_id"], kind="stable").reset_index(drop=True)


def _load_tape_loader(spec: str):
    module_name, separator, function_name = spec.partition(":")
    if not separator:
        raise ValueError("--tape-loader must be module:function")
    function = getattr(importlib.import_module(module_name), function_name)
    if not callable(function):
        raise TypeError(f"Tape loader is not callable: {spec}")
    return function


def main(argv=None) -> pd.DataFrame:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=EVENTS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--tape-loader", default="yoyo.evaluation.monday_weekly_wick_census3r:load_frozen_tape",
                        help="module:function returning a symbol's N x 5 frozen H1 OHLC tape")
    args = parser.parse_args(argv)
    events = pd.read_csv(args.events)
    breadth_index = prepare_breadth_index(events)
    candidates = events.loc[events.apply(_is_signal_row, axis=1)].copy()
    if not len(candidates):
        raise ValueError(f"No confirmed signal rows in {args.events}")
    load_tape = _load_tape_loader(args.tape_loader)
    max_close = int(pd.to_numeric(candidates.close_ms).max())
    try:
        btc_raw = load_tape(BTC_SYMBOL)
        btc_values, btc_ts = _as_hourly(btc_raw, max_close)
    except (FileNotFoundError, KeyError):
        btc_raw, btc_values, btc_ts = None, None, None
    outputs = []
    symbols = candidates.symbol.astype(str).drop_duplicates().tolist()
    for n, (symbol, group) in enumerate(candidates.groupby("symbol", sort=False), 1):
        symbol = str(symbol)
        raw = btc_raw if symbol == BTC_SYMBOL and btc_raw is not None else load_tape(symbol)
        symbol_max_close = int(pd.to_numeric(group.close_ms).max())
        values, ts = _as_hourly(raw, symbol_max_close)
        outputs.append(_build_symbol_features_prepared(
            group, values, ts, btc_values, btc_ts, breadth_index
        ))
        if n % 100 == 0 or n == len(symbols):
            print(f"{n}/{len(symbols)} symbols; {sum(len(x) for x in outputs)} feature rows", flush=True)
        del raw, values, ts
    features = pd.concat(outputs, ignore_index=True).sort_values(
        ["close_ms", "event_id"], kind="stable"
    ).reset_index(drop=True)
    if features.event_id.duplicated().any():
        raise ValueError("Weekly wick event ids must be unique in the feature output")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(args.output, index=False, compression={"method": "gzip", "mtime": 0})
    print(f"wrote {len(features)} feature rows; {len(FEATURES)} features ({len(CORE_FEATURES)} core) to {args.output}",
          flush=True)
    return features


if __name__ == "__main__":
    main()

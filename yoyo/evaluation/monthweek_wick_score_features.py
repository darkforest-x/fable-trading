"""Causal close-time features for frozen Binance month-week wick signals.

Every signal-bar feature is rebuilt from the frozen H1 tape and is available
only through the selected 1h/4h/12h bar's ``close_ms``. Returns and true-range
windows also end at that close. Cross-symbol touch breadth uses first-touch
events whose source H1 candle had closed before the signal's last H1 candle.

The source reader intentionally supplies OHLC only; reliable volume is absent
from this frozen contract, so this module does not create volume features.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from typing import Mapping

import numpy as np
import pandas as pd

from yoyo.evaluation import month_firstweek_12h_wick_census as census


HOUR_MS = census.HOUR_MS
DAY_MS = 24 * HOUR_MS
FEATURES = (
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
)
BASE_COLUMNS = ("event_id", "symbol", "month", "tf", "side", "close_ms", "status")
OUTPUT_COLUMNS = tuple(dict.fromkeys((*BASE_COLUMNS, *FEATURES)))
CONFIRMED_STATUSES = ("entered", "confirmed_no_next_open", "confirmed_open_beyond_stop")


def prepare_touch_index(touches: pd.DataFrame) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Index parent first-touch events by side and known H1 close time.

    ``touch_ms`` is the open time of the source H1 candle. Its first-touch
    fact is available at ``touch_ms + 1h``; later consumers must still apply
    their own as-of boundary. Arrays are sorted for binary-search windows.
    """
    required = {"symbol", "side", "touch_ms"}
    missing = required.difference(touches.columns)
    if missing:
        raise ValueError(f"Touch ledger lacks required columns: {sorted(missing)}")
    frame = touches.loc[:, ["symbol", "side", "touch_ms"]].copy()
    frame["symbol"] = frame["symbol"].astype(str)
    frame["side"] = pd.to_numeric(frame["side"], errors="raise").astype(int)
    frame["known_ms"] = pd.to_numeric(frame["touch_ms"], errors="raise").astype("int64") + HOUR_MS
    result: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for side, group in frame.groupby("side", sort=False):
        ordered = group.sort_values("known_ms", kind="stable")
        result[int(side)] = (
            ordered["known_ms"].to_numpy(dtype=np.int64),
            ordered["symbol"].to_numpy(dtype=object),
        )
    return result


def _past_other_touch_count(
    symbol: str,
    side: int,
    close_ms: int,
    touch_index: Mapping[int, tuple[np.ndarray, np.ndarray]],
) -> int:
    """Count unique other symbols touching this side in the prior 24h.

    The window ends at ``close_ms - 1h`` (the previous fully completed H1
    candle), and begins 24h before that endpoint. The current signal candle,
    an unfinished last H1 candle, future events, and the same symbol are out.
    """
    if side not in touch_index:
        return 0
    known_ms, symbols = touch_index[side]
    end_ms = close_ms - HOUR_MS
    start_ms = end_ms - DAY_MS
    left = int(np.searchsorted(known_ms, start_ms, side="right"))
    right = int(np.searchsorted(known_ms, end_ms, side="right"))
    window_symbols = symbols[left:right]
    if not len(window_symbols):
        return 0
    others = window_symbols[window_symbols != symbol]
    return int(len(np.unique(others))) if len(others) else 0


def _as_hourly(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(raw, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 5:
        raise ValueError("Frozen hourly tape must be N x 5 [open_ms, O, H, L, C]")
    ts = values[:, 0].astype(np.int64)
    if len(ts) and (
        np.any(values[:, 0] != ts)
        or np.any(ts % HOUR_MS)
        or np.any(np.diff(ts) <= 0)
        or not np.isfinite(values).all()
        or np.any(values[:, 1:5] <= 0)
        or np.any(values[:, 2] < np.maximum(values[:, 1], values[:, 4]))
        or np.any(values[:, 3] > np.minimum(values[:, 1], values[:, 4]))
    ):
        raise ValueError("Frozen hourly tape has invalid timestamps or OHLC")
    return values, ts


def _contiguous(ts: np.ndarray, start: int, stop: int) -> bool:
    """Whether half-open H1 row range [start, stop) has no timestamp gaps."""
    return start >= 0 and stop <= len(ts) and stop >= start and (
        stop - start <= 1 or bool(np.all(np.diff(ts[start:stop]) == HOUR_MS))
    )


def _signal_ohlc(event: Mapping, values: np.ndarray, ts: np.ndarray) -> tuple[int, int, float, float, float, float]:
    tf = int(event["tf"])
    bar_ms, close_ms = int(event["bar_ms"]), int(event["close_ms"])
    if tf not in (1, 4, 12) or close_ms != bar_ms + tf * HOUR_MS:
        raise ValueError(f"Invalid selected timeframe bounds for {event['event_id']}")
    if bar_ms % (tf * HOUR_MS):
        raise ValueError(f"Selected bar is not UTC aligned for {event['event_id']}")
    start = int(np.searchsorted(ts, bar_ms, side="left"))
    stop = int(np.searchsorted(ts, close_ms, side="left"))
    if stop - start != tf or not _contiguous(ts, start, stop) or ts[start] != bar_ms:
        raise ValueError(f"Selected signal bar is incomplete in frozen tape: {event['event_id']}")
    block = values[start:stop]
    return (
        start,
        stop,
        float(block[0, 1]),
        float(np.max(block[:, 2])),
        float(np.min(block[:, 3])),
        float(block[-1, 4]),
    )


def _anchor_features(
    event: Mapping,
    values: np.ndarray,
    ts: np.ndarray,
    window: Mapping,
) -> tuple[float, float]:
    """Use the exact completed NY first-week H1 range and final H1 close."""
    start_ms, ready_ms = int(window["start_ms"]), int(window["ready_ms"])
    start = int(np.searchsorted(ts, start_ms, side="left"))
    stop = int(np.searchsorted(ts, ready_ms, side="left"))
    expected = int(window["expected_1h_bars"])
    if (stop - start != expected or not _contiguous(ts, start, stop)
            or not len(ts[start:stop]) or ts[start] != start_ms
            or ts[stop - 1] != ready_ms - HOUR_MS):
        raise ValueError(f"Frozen NY-week anchor is incomplete: {event['event_id']}")
    anchor = values[start:stop]
    week_high, week_low = float(np.max(anchor[:, 2])), float(np.min(anchor[:, 3]))
    span = week_high - week_low
    if not np.isfinite(span) or span <= 0:
        raise ValueError(f"Frozen NY-week anchor has no range: {event['event_id']}")
    if not (np.isclose(week_high, float(event["week_high"]), rtol=1e-12, atol=0.0)
            and np.isclose(week_low, float(event["week_low"]), rtol=1e-12, atol=0.0)):
        raise ValueError(f"Frozen anchor differs from first_breaches ledger: {event['event_id']}")
    anchor_close = float(anchor[-1, 4])
    return (span / week_low, (anchor_close - week_low) / span)


def _close_context(
    values: np.ndarray,
    ts: np.ndarray,
    close_ms: int,
) -> tuple[float, float, float, float]:
    """Causal returns and mean true range for trailing 24h and 168h windows.

    The last included row opens at ``close_ms - 1h``. A return compares that
    close with the close 24 or 168 hours earlier. Each TR window contains the
    named number of completed H1 bars, including the signal's final H1 bar, and
    requires one extra prior close plus an uninterrupted timestamp grid.
    Missing history or a tape gap yields NaN for the affected feature only.
    """
    end = int(np.searchsorted(ts, close_ms, side="left")) - 1
    if end < 0 or ts[end] != close_ms - HOUR_MS:
        raise ValueError(f"No completed signal H1 close at {close_ms}")
    current_close = float(values[end, 4])

    def directional_return(hours: int) -> float:
        prior = end - hours
        if prior < 0 or ts[prior] != ts[end] - hours * HOUR_MS:
            return np.nan
        return current_close / float(values[prior, 4]) - 1.0

    def mean_tr_pct(hours: int) -> float:
        first = end - hours + 1
        if first <= 0 or not _contiguous(ts, first - 1, end + 1):
            return np.nan
        bars = values[first:end + 1]
        prior_close = values[first - 1:end, 4]
        true_range = np.maximum(
            bars[:, 2] - bars[:, 3],
            np.maximum(np.abs(bars[:, 2] - prior_close), np.abs(bars[:, 3] - prior_close)),
        )
        return float(np.mean(true_range) / current_close)

    return (directional_return(24), directional_return(168), mean_tr_pct(24), mean_tr_pct(168))


def build_feature_row(
    event: Mapping,
    raw: np.ndarray,
    window: Mapping,
    touch_index: Mapping[int, tuple[np.ndarray, np.ndarray]],
    stop_buffer: float,
) -> dict:
    """Build one feature row from a confirmed event and its frozen H1 tape.

    This helper is reusable for later scoring: pass a new confirmed event, its
    frozen OHLC tape, the matching NY calendar window, and an as-of-indexable
    first-touch ledger. It never reads entry, stop, outcome, exit, or later-TF
    state fields from the event.
    """
    if not (0 < float(stop_buffer) < 1):
        raise ValueError("stop_buffer must be between zero and one")
    values, ts = _as_hourly(raw)
    tf = int(event["tf"])
    close_ms = int(event["close_ms"])
    breach_ms = int(event["first_breach_ms"])
    bar_ms = int(event["bar_ms"])
    side = int(event["side"])
    if side not in (-1, 1) or not (bar_ms <= breach_ms < close_ms):
        raise ValueError(f"Invalid side or first-breach boundary for {event['event_id']}")

    _, _, bar_open, bar_high, bar_low, bar_close = _signal_ohlc(event, values, ts)
    anchor_range, anchor_close_position = _anchor_features(event, values, ts, window)
    breach_delay_days = (breach_ms - int(window["ready_ms"])) / DAY_MS
    breach_offset = (breach_ms - bar_ms) / (tf * HOUR_MS)
    tip = bar_low if side == 1 else bar_high
    rail = float(event["rail"])
    outside = abs(tip - rail)
    if rail <= 0 or outside <= 0:
        raise ValueError(f"Invalid wick or rail for {event['event_id']}")
    tolerance = rail * 1e-12
    if not (
        side * (bar_open - rail) > tolerance
        and side * (bar_close - rail) > tolerance
        and side * (tip - rail) < -tolerance
    ):
        raise ValueError(f"Signal bar does not have a strict outward wick and inward open/close: {event['event_id']}")
    outside_wick = min(bar_open, bar_close) - bar_low if side == 1 else bar_high - max(bar_open, bar_close)
    body = abs(bar_close - bar_open)
    stop = tip * (1.0 - side * float(stop_buffer))
    close_context = _close_context(values, ts, close_ms)
    reclaim_ratio = side * (bar_close - tip) / outside
    wick_body_ratio = outside_wick / body if body > 0 else np.nan
    features = {
        "side": float(side),
        "tf4": float(tf == 4),
        "tf12": float(tf == 12),
        "week_range_frac": float(anchor_range),
        "week_close_position": float(anchor_close_position),
        "breach_delay_days": float(breach_delay_days),
        "breach_offset_tf_frac": float(breach_offset),
        "other_side_touch_count_24h": float(_past_other_touch_count(
            str(event["symbol"]), side, close_ms, touch_index
        )),
        "side_return_24h": float(side * close_context[0]),
        "side_return_168h": float(side * close_context[1]),
        "tr_pct_24h": float(close_context[2]),
        "tr_pct_168h": float(close_context[3]),
        "wick_outside_frac": float(outside / rail),
        # Both raw ratios can be extremely skewed when their denominator is
        # tiny. log1p preserves order while limiting leverage from near-zero
        # wick excursions or near-doji bodies. The underlying ratios are
        # strictly positive for a qualified signal bar; zero-body is missing.
        "log1p_wick_reclaim_ratio": float(np.log1p(max(0.0, reclaim_ratio))),
        "log1p_outside_wick_to_body_ratio": (
            float(np.log1p(max(0.0, wick_body_ratio))) if np.isfinite(wick_body_ratio) else np.nan
        ),
        "signal_close_risk_frac": float(side * (bar_close - stop) / bar_close),
    }
    for name, value in features.items():
        if not (np.isfinite(value) or np.isnan(value)):
            raise ValueError(f"Non-finite feature {name} for {event['event_id']}")
    return {
        "event_id": str(event["event_id"]),
        "symbol": str(event["symbol"]),
        "month": str(event["month"]),
        "tf": tf,
        "side": side,
        "close_ms": close_ms,
        "status": str(event["status"]),
        **features,
    }


def _load_read_raw():
    """Load the already registered census reader; never fetch source data."""
    source_dir = str(census.PARENT)
    if source_dir not in sys.path:
        sys.path.insert(0, source_dir)
    from census12 import read_raw

    return read_raw


def build_features() -> pd.DataFrame:
    """Build one causal row for each confirmed signal in frozen first_breaches.

    Reads the 2026-11-16 priority experiment, its parent first-touch ledger,
    the registered hourly OHLC source and NY calendar. Only ``entered`` and
    ``confirmed_no_next_open`` records are selected; the latter remains in the
    output with its status even though no executable entry was recorded. Status
    is metadata only and is intentionally excluded from ``FEATURES``.
    """
    # Defer the priority experiment import until the frozen source pipeline is
    # requested. Its module imports the VectorBT exit adapter, which is not
    # needed by the reusable single-event scorer or its synthetic tests.
    from yoyo.evaluation import month_firstweek_wick_priority as priority

    config = json.loads((priority.EXP / "config.json").read_text())
    parent_config = json.loads((census.EXP / "config.json").read_text())
    if config["parent"] != census.EXP.name:
        raise ValueError("Priority experiment no longer points at the registered parent")
    if config["cutoff_exclusive"] != parent_config["data_cutoff_exclusive"]:
        raise ValueError("Priority and parent data cutoffs differ")
    cutoff_ms = int(pd.Timestamp(config["cutoff_exclusive"]).timestamp() * 1000)
    windows = {row["month"]: row for row in census.calendar(parent_config)}

    all_events = pd.read_csv(priority.EXP / "first_breaches.csv.gz", float_precision="round_trip")
    confirmed = all_events.loc[all_events["status"].isin(CONFIRMED_STATUSES)].copy()
    if confirmed.empty or confirmed["event_id"].duplicated().any():
        raise ValueError("Expected unique confirmed events in first_breaches.csv.gz")
    if confirmed[["tf", "bar_ms", "close_ms", "first_breach_ms"]].isna().any().any():
        raise ValueError("A confirmed event is missing selected signal-bar timestamps")

    parent_touches = pd.read_csv(census.EXP / "first_touches.csv.gz", float_precision="round_trip")
    touch_index = prepare_touch_index(parent_touches)
    contracts = pd.read_csv(census.EXP / "contracts.csv").set_index("symbol")
    ends = pd.read_csv(census.REFRESH / "roster.csv").set_index("symbol")["end_ms"]
    ends = pd.to_numeric(ends, errors="coerce")
    read_raw = _load_read_raw()
    stop_buffer = float(config["stop_buffer"])

    groups = list(confirmed.groupby("symbol", sort=False))

    def one_symbol(item):
        symbol, records = item
        if symbol not in contracts.index or symbol not in ends.index:
            raise ValueError(f"Confirmed event symbol is absent from frozen contracts: {symbol}")
        end_ms = ends.loc[symbol]
        raw = priority.frozen_tape(
            symbol,
            contracts.loc[symbol],
            int(end_ms) if pd.notna(end_ms) and 0 < end_ms <= cutoff_ms else end_ms,
            cutoff_ms,
            read_raw,
        )
        rows = []
        for event in records.to_dict(orient="records"):
            month_window = windows.get(str(event["month"]))
            if month_window is None:
                raise ValueError(f"Missing NY calendar window for {event['event_id']}")
            rows.append(build_feature_row(event, raw, month_window, touch_index, stop_buffer))
        return rows

    rows = []
    # Frozen tapes are loaded per symbol. Four workers bound simultaneous disk
    # reads and resident arrays while keeping the universe scan practical.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for n, symbol_rows in enumerate(pool.map(one_symbol, groups), 1):
            rows.extend(symbol_rows)
            if n % 100 == 0:
                print(f"{n}/{len(groups)} symbols; {len(rows)} confirmed wick rows", flush=True)

    # ``side`` is a required base column and is also a numeric model feature.
    # Keep one physical column while retaining it in the public FEATURES list.
    result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    if len(result) != len(confirmed) or result["event_id"].duplicated().any():
        raise ValueError("Feature output does not preserve every confirmed event exactly once")
    for name in FEATURES:
        result[name] = pd.to_numeric(result[name], errors="raise")
    for name in ("tf", "side", "close_ms"):
        result[name] = pd.to_numeric(result[name], errors="raise").astype("int64")
    return result


def main() -> None:
    """Build and summarize the score input without writing into frozen inputs."""
    features = build_features()
    print(f"Built {len(features)} rows x {len(FEATURES)} features")
    print(f"Statuses: {features['status'].value_counts().to_dict()}")
    print(f"Feature columns: {', '.join(FEATURES)}")


if __name__ == "__main__":
    main()

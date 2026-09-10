"""Read one imported replay event's frozen OHLC context without market I/O.

The monitor receives only a selected event id.  Its venue, symbol, timeframe,
and signal clock come from the already-imported, signal-only replay record.
This loader reads the experiment's immutable normalized 30-minute gzip file on
demand, aggregates exactly as the V1 evaluation did, computes causal close MAs,
and returns a bounded before/after display window.  Later bars are labelled
historical review context and are never passed to the live scanner or YOLO.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import features

EXPERIMENT_ID = "exp-spike-v1-twoyear-allmarkets-20260911-v1"
REPLAY_DATA_ROOT = Path(__file__).resolve().parents[2] / "experiments" / "active" / EXPERIMENT_ID / "data" / "normalized"
WINDOW_BEFORE = 90
WINDOW_AFTER = 90


class ReplayChartUnavailable(ValueError):
    """A truthful unavailable reason for a replay event without frozen OHLC."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _source_path(event: dict, root: Path) -> tuple[Path, int]:
    venue, symbol = event.get("venue"), event.get("symbol")
    if venue not in {"binance", "okx", "gate"} or not isinstance(symbol, str):
        raise ReplayChartUnavailable("unsupported_replay_provenance")
    if not (1 <= len(symbol) <= 80) or "\x00" in symbol or "/" in symbol or "\\" in symbol:
        raise ReplayChartUnavailable("invalid_replay_symbol")
    base = root.resolve()
    if venue == "gate":
        minutes = int(event["timeframe_min"])
        path = (base.parent / "normalized_gate_direct" / f"{symbol}_{minutes}m.csv.gz").resolve()
        expected_parent, native_minutes = (base.parent / "normalized_gate_direct").resolve(), minutes
    else:
        path = (base / venue / f"{symbol}_30m.csv.gz").resolve()
        expected_parent, native_minutes = (base / venue).resolve(), 30
    if path.parent != expected_parent or not path.is_file():
        raise ReplayChartUnavailable("frozen_ohlc_missing")
    return path, native_minutes


def _aggregate(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Match the frozen evaluation's left-closed, epoch-aligned aggregation."""
    grouped = frame.resample(f"{minutes}min", origin="epoch", closed="left", label="left")
    result = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return result.loc[grouped.size().eq(minutes // 30)]


def _segments(frame: pd.DataFrame, minutes: int):
    expected = pd.Timedelta(minutes=minutes).value
    splits = np.flatnonzero(np.diff(frame.index.asi8) != expected) + 1
    edges = [0, *splits.tolist(), len(frame)]
    return [frame.iloc[left:right] for left, right in zip(edges, edges[1:])]


def load_replay_chart(event: dict, *, root: Path = REPLAY_DATA_ROOT) -> dict:
    """Return exact frozen replay OHLC around one source-bar, or a reason code.

    Uses columns ``time/open/high/low/close/volume`` from the frozen gzip.  MA
    values are calculated with the frozen causal V1 feature implementation;
    slicing happens only afterwards, so no future bar contributes to a value.
    """
    if event.get("source") != "replay" or event.get("confirmation") != "raw":
        raise ReplayChartUnavailable("not_replay_raw_event")
    try:
        minutes = int(event["timeframe_min"])
        bar_open = int(event["bar_open_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayChartUnavailable("invalid_replay_clock") from exc
    if minutes not in (30, 60, 240) or bar_open < 0 or bar_open % (minutes * 60_000):
        raise ReplayChartUnavailable("invalid_replay_clock")
    path, native_minutes = _source_path(event, Path(root))
    try:
        frame = pd.read_csv(path, compression="gzip", parse_dates=["time"])
        frame = frame.set_index("time")[["open", "high", "low", "close", "volume"]]
        frame.index = pd.DatetimeIndex(frame.index, tz="UTC") if frame.index.tz is None else frame.index.tz_convert("UTC")
        bars = frame if native_minutes == minutes else _aggregate(frame, minutes)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise ReplayChartUnavailable("frozen_ohlc_unreadable") from exc
    target = pd.Timestamp(bar_open, unit="ms", tz="UTC")
    segment = next((part for part in _segments(bars, minutes) if target in part.index), None)
    if segment is None:
        raise ReplayChartUnavailable("signal_bar_missing_from_frozen_ohlc")
    enriched = features(segment)
    position = int(enriched.index.get_loc(target))
    visible = enriched.iloc[max(0, position - WINDOW_BEFORE):position + WINDOW_AFTER + 1]
    ma_names = {"s20": "sma20", "e20": "ema20", "s60": "sma60", "e60": "ema60", "s120": "sma120", "e120": "ema120"}
    candles = []
    for timestamp, row in visible.iterrows():
        candle = {"t": int(timestamp.value // 10**6), "o": float(row.open), "h": float(row.high),
                  "l": float(row.low), "c": float(row.close), "v": float(row.volume)}
        for source, target_name in ma_names.items():
            value = row[source]
            candle[target_name] = float(value) if pd.notna(value) else None
        candles.append(candle)
    return {"source": "replay", "historical": True, "symbol": event["symbol"], "venue": event["venue"],
            "timeframe": event["timeframe"], "timeframe_min": minutes, "event_id": event["id"],
            "signal_bar_open_ms": bar_open, "signal_close_ms": int(event["bar_close_ms"]), "candles": candles,
            "context_before_bars": position - max(0, position - WINDOW_BEFORE),
            "context_after_bars": max(0, len(visible) - (position - max(0, position - WINDOW_BEFORE)) - 1),
            "future_context": "historical_review_only_not_model_input",
            "provenance": {"experiment_id": EXPERIMENT_ID, "ohlc_file": str(path.relative_to(Path(root).parents[1])),
                           "ohlc_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "ohlc_timeframe_min": native_minutes,
                           "source_sha256": event.get("source_sha256")},
            "state": {"phase": "historical_replay", "stale": False, "error": None}}

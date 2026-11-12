"""Read local Binance USD-M 5m OHLC bars for one UTC calendar month."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_retest_flow_data as native


ROOT = Path(__file__).resolve().parents[3]
STEP_MS = 5 * 60_000
EMPTY = np.empty((0, 5), dtype=np.float64)

_MONTH_ZIP_ROOTS = (
    ROOT / "data/research/alt-isolated-12h-wicks-20261111/expanded12/all_replay_v1/intraday_retest/binance_monthly",
    ROOT / "data/crypto/market/kline_preholdout_binance_um5m/downloads",
    ROOT / "data/crypto/market/kline_binance_um5m_forward_20260922/downloads",
    ROOT / "data/crypto/market/research-spike_v128_recent_20260923/downloads",
    ROOT / "data/crypto/market/breadth_rebound_targets_20261107/downloads",
)


def _month_bounds(month: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{4}-\d{2}", str(month)):
        raise ValueError(f"month must be YYYY-MM, got {month!r}")
    try:
        start = pd.Timestamp(f"{month}-01", tz="UTC")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid UTC month: {month!r}") from exc
    end = start + pd.offsets.MonthBegin(1)
    return int(start.value // 1_000_000), int(end.value // 1_000_000)


def _validate(rows: np.ndarray, label: str, start_ms: int, end_ms: int) -> np.ndarray:
    if not len(rows):
        return EMPTY.copy()
    rows = np.asarray(rows, dtype=np.float64)
    if rows.ndim != 2 or rows.shape[1] != 5:
        raise ValueError(f"{label}: expected [ms, open, high, low, close], got {rows.shape}")
    ts = rows[:, 0]
    prices = rows[:, 1:]
    if (not np.isfinite(rows).all() or not np.equal(ts, np.floor(ts)).all()
            or (ts % STEP_MS != 0).any()):
        raise ValueError(f"{label}: timestamps/prices are non-finite or off the 5m UTC grid")
    if ((ts < start_ms) | (ts >= end_ms)).any():
        raise ValueError(f"{label}: bar falls outside requested UTC month")
    if ((prices <= 0).any() or (prices[:, 1] < np.maximum.reduce(
            (prices[:, 0], prices[:, 2], prices[:, 3]))).any()
            or (prices[:, 2] > np.minimum.reduce(
                (prices[:, 0], prices[:, 1], prices[:, 3]))).any()):
        raise ValueError(f"{label}: invalid OHLC range")
    return rows


def _read_month_zip(path: Path, month: str, start_ms: int, end_ms: int) -> np.ndarray:
    # Reuse the established Binance monthly-archive parser and validation.
    raw = native._read_month(path, month, STEP_MS)
    return _validate(raw[:, :5], str(path), start_ms, end_ms)


@lru_cache(maxsize=4)
def _read_series_full(path_text: str) -> np.ndarray:
    """Cache a registered series once so missing months do not rescan it."""
    path = Path(path_text)
    try:
        columns = list(pd.read_csv(path, nrows=0, compression="infer").columns)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise ValueError(f"cannot read local Binance bar header: {path}") from exc
    time_col = "ts" if "ts" in columns else "open_time" if "open_time" in columns else None
    required = {"open", "high", "low", "close"}
    if time_col is None or not required.issubset(columns):
        # Some REST fallback caches contain only O/H/C. Never invent missing lows.
        return EMPTY.copy()
    wanted = [time_col, "open", "high", "low", "close"]
    chunks: list[np.ndarray] = []
    try:
        reader = pd.read_csv(
            path, compression="infer", usecols=wanted,
            dtype={time_col: "int64", "open": "float64", "high": "float64",
                   "low": "float64", "close": "float64"},
            chunksize=100_000,
        )
        with reader:
            for frame in reader:
                if frame.empty:
                    continue
                chunks.append(frame[wanted].to_numpy(dtype=np.float64))
    except (OSError, ValueError, KeyError, pd.errors.ParserError, EOFError) as exc:
        raise ValueError(f"cannot read local Binance bars: {path}") from exc
    if not chunks:
        return EMPTY.copy()
    return np.concatenate(chunks)


def _read_series(path: Path, start_ms: int, end_ms: int) -> np.ndarray:
    """Read one month from the cached, headered registered local 5m series."""
    rows = _read_series_full(str(path))
    if not len(rows):
        return EMPTY.copy()
    times = rows[:, 0]
    selected = rows[(times >= start_ms) & (times < end_ms)]
    return _validate(selected, str(path), start_ms, end_ms)


def _series_paths(symbol: str, month: str, start_ms: int, end_ms: int) -> list[Path]:
    market = ROOT / "data/crypto/market"
    candidates: list[Path] = []
    if month < "2026-05":
        # Historical continuation is restricted to the frozen pre-archive.
        preholdout = market / "kline_preholdout_binance_um5m/series"
        candidates.extend(sorted(preholdout.glob(f"binance_um_{symbol}_5m_*.csv")))
    elif month <= "2026-09":
        # Forward/recent data cover May through Sep 22; Sep's tail is added below.
        candidates.extend([
            market / "kline_binance_um5m_forward_20260922/series" / f"{symbol}.csv",
            market / "research-spike_v128_recent_20260923/series" / f"{symbol}.csv.gz",
        ])
    if "2026-09" <= month <= "2026-10":
        candidates.extend([
            market / "binance_um5m_20260923_20261031/series" / f"{symbol}.csv.gz",
            ROOT / "experiments/active/exp-monday-limit-live-october-20261101-v1/symbols" / symbol / "new_bars.csv.gz",
        ])
    if month == "2026-11":
        candidates.append(market / "breadth_rebound_targets_20261107" / f"{symbol}-november-through-04.csv.gz")
        # REST fallback is fragmented by requested time. Ignore other months
        # before opening any file, and incomplete O/H/C-only files are excluded.
        fallback = market / "binance_um5m_rest_fallback/series" / symbol
        if fallback.is_dir():
            for path in sorted(fallback.glob("*.csv")):
                match = re.fullmatch(r"(\d+)_(\d+)\.csv", path.name)
                if match and int(match.group(1)) < end_ms and int(match.group(2)) >= start_ms:
                    candidates.append(path)
    return [path for path in candidates if path.is_file()]


def _merge(rows_by_source: list[tuple[str, np.ndarray]]) -> np.ndarray:
    available = [(label, rows) for label, rows in rows_by_source if len(rows)]
    if not available:
        return EMPTY.copy()
    if len(available) == 1:
        only = available[0][1]
        if np.all(np.diff(only[:, 0]) > 0):
            return only
    rows = np.concatenate([data for _, data in available], axis=0)
    rows = rows[np.argsort(rows[:, 0], kind="stable")]
    times, first, counts = np.unique(rows[:, 0], return_index=True, return_counts=True)
    for ts, pos, count in zip(times, first, counts):
        if count <= 1:
            continue
        values = rows[pos:pos + count, 1:]
        if not np.allclose(values, values[0], rtol=1e-12, atol=1e-12):
            sources = [label for label, data in available
                       if np.any(data[:, 0] == ts)]
            raise ValueError(f"conflicting Binance OHLC at {int(ts)} from {sources}")
    return rows[first]


def load_bars(symbol: str, month: str) -> np.ndarray:
    """Return sorted unique ``[open_ms, open, high, low, close]`` rows.

    ``symbol`` may be bare (``ETC``) or a USDT perpetual ticker. Missing source
    months return shape ``(0, 5)``. Duplicate timestamps with different OHLC
    raise instead of being silently selected.
    """
    symbol = str(symbol).upper()
    ticker = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    if not ticker.endswith("USDT") or not ticker[:-4].isalnum():
        raise ValueError(f"invalid Binance USDT symbol: {symbol!r}")
    start_ms, end_ms = _month_bounds(month)

    monthly = [(root / ticker / f"{ticker}-5m-{month}.zip") for root in _MONTH_ZIP_ROOTS]
    # A few archived February 2022 repair days supplement the official month ZIP.
    if month == "2022-02":
        patch_root = ROOT / "data/research/cloud_close_binance_feb2022_daily"
        monthly.extend(patch_root / f"{ticker}-5m-2022-02-{day}.zip" for day in (26, 27, 28))
    zip_paths = [path for path in monthly if path.is_file()]
    if zip_paths:
        loaded = [(str(path), _read_month_zip(path, month, start_ms, end_ms))
                  for path in zip_paths]
    else:
        loaded = [(str(path), _read_series(path, start_ms, end_ms))
                  for path in _series_paths(ticker, month, start_ms, end_ms)]
    return _merge(loaded)

"""Read and materialize Binance UM 15m streams for the isolated-alt scan.

Inputs are the registered local Binance UM 15m history (through 2026-04-30),
the registered 5m forward cache (from 2026-05-01 through 2026-09-22), and the
registered 5m daily tail (2026-09-23 onward). 5m bars are aggregated only when
all three expected candles for a 15m interval are present. The output is one
float64 N x 5 array per symbol: UTC open time in milliseconds, open, high, low,
close. Timestamp values remain exactly representable as float64 at this scale.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional, Tuple

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data" / "research" / "alt-isolated-12h-wicks-20261111" / "binance"
SERIES_DIR = DATA_ROOT / "series"
BASE_15M_DIR = REPO_ROOT / "data" / "crypto" / "market" / "kline_preholdout_binance_um15m" / "series"
FORWARD_5M_DIR = REPO_ROOT / "data" / "crypto" / "market" / "kline_binance_um5m_forward_20260922" / "series"
DAILY_5M_DIR = REPO_ROOT / "data" / "crypto" / "market" / "binance_um5m_20260923_20261031" / "series"
MANIFEST_PATH = DATA_ROOT / "streams_manifest.json"
BAR_MS = 15 * 60 * 1000
FIVE_MIN_MS = 5 * 60 * 1000


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _read_ohlc(path: Path) -> np.ndarray:
    """Read ts/open/high/low/close from the local normalized series CSV."""
    kwargs = {
        "usecols": ["ts", "open", "high", "low", "close"],
        "dtype": {"ts": "int64", "open": "float64", "high": "float64", "low": "float64", "close": "float64"},
    }
    last_error = None
    for attempt in range(2):
        try:
            frame = pd.read_csv(path, **kwargs)
            break
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.25)
                continue
            try:
                # A few cold external-volume reads fail midway through pandas'
                # C parser. Retry once with the Python parser before recording
                # the symbol as a source-read failure.
                frame = pd.read_csv(path, engine="python", **kwargs)
                break
            except Exception as fallback_exc:
                raise RuntimeError(
                    f"could not read {path} with C or Python CSV parser: "
                    f"{type(last_error).__name__}: {last_error}; "
                    f"fallback {type(fallback_exc).__name__}: {fallback_exc}"
                ) from fallback_exc
    return frame.to_numpy(dtype=np.float64, copy=False)


def _aggregate_5m_to_15m(rows: np.ndarray, cutoff_ms: Optional[int]) -> Tuple[np.ndarray, dict]:
    """Aggregate complete aligned three-bar groups from 5m OHLC rows."""
    if rows.size == 0:
        return np.empty((0, 5), dtype=np.float64), {"source_rows": 0, "complete_bars": 0, "incomplete_groups": 0, "duplicate_5m_rows": 0}

    # Inputs are source CSVs already sorted; defensively apply the cutoff and
    # remove duplicate timestamps before grouping.
    rows = rows[np.argsort(rows[:, 0], kind="stable")]
    if cutoff_ms is not None:
        rows = rows[rows[:, 0] < cutoff_ms]
    if rows.size == 0:
        return np.empty((0, 5), dtype=np.float64), {"source_rows": 0, "complete_bars": 0, "incomplete_groups": 0, "duplicate_5m_rows": 0}

    unique_ts, first_idx = np.unique(rows[:, 0], return_index=True)
    duplicate_count = int(rows.shape[0] - unique_ts.shape[0])
    rows = rows[first_idx]
    bucket = (rows[:, 0] // BAR_MS) * BAR_MS
    starts = np.r_[0, np.flatnonzero(bucket[1:] != bucket[:-1]) + 1]
    ends = np.r_[starts[1:], len(rows)]
    out = np.empty((len(starts), 5), dtype=np.float64)
    complete = np.zeros(len(starts), dtype=bool)
    for j, (a, b) in enumerate(zip(starts, ends)):
        expected = bucket[a] + np.array([0, FIVE_MIN_MS, 2 * FIVE_MIN_MS], dtype=np.float64)
        if b - a == 3 and np.array_equal(rows[a:b, 0], expected):
            group = rows[a:b]
            out[j] = (bucket[a], group[0, 1], np.max(group[:, 2]), np.min(group[:, 3]), group[-1, 4])
            complete[j] = True
    result = out[complete]
    return result, {
        "source_rows": int(rows.shape[0]),
        "complete_bars": int(result.shape[0]),
        "incomplete_groups": int((~complete).sum()),
        "duplicate_5m_rows": duplicate_count,
    }


def _coverage(array: np.ndarray) -> dict:
    if not len(array):
        return {"rows": 0, "first_ts": None, "last_ts": None, "gap_segments": 0, "missing_15m_bars": 0, "gap_examples": []}
    ts = array[:, 0].astype(np.int64, copy=False)
    delta = np.diff(ts)
    where = np.flatnonzero(delta > BAR_MS)
    missing = 0
    examples = []
    for i in where[:100]:
        steps = int(delta[i] // BAR_MS)
        missing += max(0, steps - 1)
        examples.append({"after_ts": int(ts[i]), "before_ts": int(ts[i + 1]), "missing_15m_bars": max(0, steps - 1)})
    # Include all gap counts even when the human-readable examples are capped.
    all_missing = int(sum(max(0, int(x // BAR_MS) - 1) for x in delta[where]))
    return {
        "rows": int(array.shape[0]),
        "first_ts": int(ts[0]),
        "last_ts": int(ts[-1]),
        "gap_segments": int(where.size),
        "missing_15m_bars": all_missing,
        "gap_examples": examples,
    }


@lru_cache(maxsize=1)
def _base_files() -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    for path in BASE_15M_DIR.glob("binance_um_*_15m_*.csv"):
        name = path.name
        symbol = name[len("binance_um_") :].split("_15m_", 1)[0]
        if symbol:
            found[symbol] = path
    return found


@lru_cache(maxsize=1)
def _forward_files() -> Dict[str, Path]:
    return {p.stem: p for p in FORWARD_5M_DIR.glob("*.csv")}


@lru_cache(maxsize=1)
def _daily_files() -> Dict[str, Path]:
    return {p.name[: -len(".csv.gz")]: p for p in DAILY_5M_DIR.glob("*.csv.gz")}


def _merge_rows(parts: Iterable[np.ndarray], cutoff_ms: Optional[int]) -> Tuple[np.ndarray, int]:
    arrays = [part for part in parts if part is not None and len(part)]
    if not arrays:
        return np.empty((0, 5), dtype=np.float64), 0
    rows = np.concatenate(arrays, axis=0)
    if cutoff_ms is not None:
        rows = rows[rows[:, 0] < cutoff_ms]
    rows = rows[np.argsort(rows[:, 0], kind="stable")]
    before = len(rows)
    _, first_idx = np.unique(rows[:, 0], return_index=True)
    rows = rows[np.sort(first_idx)]
    return rows, int(before - len(rows))


def build_symbol(symbol: str, cutoff_ms: Optional[int] = None) -> dict:
    """Build and atomically publish one normalized 15m N x 5 source."""
    symbol = symbol.upper()
    base_path = _base_files().get(symbol)
    forward_path = _forward_files().get(symbol)
    daily_path = _daily_files().get(symbol)
    parts = []
    sources = []
    agg_audits = {}

    if base_path is not None:
        base = _read_ohlc(base_path)
        if cutoff_ms is not None:
            base = base[base[:, 0] < cutoff_ms]
        parts.append(base)
        sources.append({"type": "registered_monthly_15m", "path": str(base_path), "rows": int(base.shape[0])})

    if forward_path is not None:
        forward = _read_ohlc(forward_path)
        forward_15m, agg_audits["forward_5m"] = _aggregate_5m_to_15m(forward, cutoff_ms)
        parts.append(forward_15m)
        sources.append({"type": "registered_forward_5m_aggregated", "path": str(forward_path), "rows": int(forward.shape[0]), "bars_15m": int(forward_15m.shape[0])})

    if daily_path is not None:
        daily = _read_ohlc(daily_path)
        daily_15m, agg_audits["daily_tail_5m"] = _aggregate_5m_to_15m(daily, cutoff_ms)
        parts.append(daily_15m)
        sources.append({"type": "registered_daily_tail_5m_aggregated", "path": str(daily_path), "rows": int(daily.shape[0]), "bars_15m": int(daily_15m.shape[0])})

    rows, duplicate_rows = _merge_rows(parts, cutoff_ms)
    output_path = SERIES_DIR / f"{symbol}.npy"
    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + f".tmp-{os.getpid()}")
    with tmp_path.open("wb") as f:
        np.save(f, rows, allow_pickle=False)
    os.replace(tmp_path, output_path)

    cov = _coverage(rows)
    record = {
        "symbol": symbol,
        "path": str(output_path),
        "status": "ready",
        "dtype": "float64",
        "columns": ["utc_ms", "open", "high", "low", "close"],
        "sources": sources,
        "aggregation": agg_audits,
        "duplicate_timestamps_removed": duplicate_rows,
        **cov,
    }
    manifest = {"version": 1, "interval": "15m", "cutoff_exclusive_ms": cutoff_ms, "symbols": {}}
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text())
        except json.JSONDecodeError:
            pass
    manifest.setdefault("version", 1)
    manifest["interval"] = "15m"
    manifest["cutoff_exclusive_ms"] = cutoff_ms
    manifest.setdefault("failures", {}).pop(symbol, None)
    manifest.setdefault("symbols", {})[symbol] = record
    _atomic_json(MANIFEST_PATH, manifest)
    return record


def build_local(cutoff_ms: Optional[int] = None, symbols: Optional[Iterable[str]] = None) -> Iterator[dict]:
    """Build registered local symbols one at a time, publishing each atomically."""
    base_files = _base_files()
    forward_files = _forward_files()
    daily_files = _daily_files()
    available = set(base_files) | set(forward_files) | set(daily_files)
    if symbols:
        selected = sorted({s.upper() for s in symbols})
    else:
        eligible_path = DATA_ROOT / "universe_eligible.csv"
        if eligible_path.exists():
            eligible = pd.read_csv(eligible_path)
            eligible_symbols = set(eligible.loc[eligible["eligible"].astype(bool), "symbol"].astype(str).str.upper())
            selected = sorted(available & eligible_symbols)
        else:
            selected = sorted(available)
    for symbol in selected:
        try:
            yield build_symbol(symbol, cutoff_ms=cutoff_ms)
        except Exception as exc:
            failure = {
                "symbol": symbol,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            manifest = {"version": 1, "interval": "15m", "cutoff_exclusive_ms": cutoff_ms, "symbols": {}, "failures": {}}
            if MANIFEST_PATH.exists():
                try:
                    manifest = json.loads(MANIFEST_PATH.read_text())
                except json.JSONDecodeError:
                    pass
            manifest.setdefault("failures", {})[symbol] = failure
            _atomic_json(MANIFEST_PATH, manifest)
            yield failure


def iter_sources(data_root: Path | str = DATA_ROOT) -> Iterator[dict]:
    """Yield completed source descriptors in the target root, ordered by symbol."""
    root = Path(data_root)
    manifest_path = root / "streams_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for symbol, record in sorted(manifest.get("symbols", {}).items()):
            path = Path(record["path"])
            if not path.is_absolute():
                path = root / path
            if path.exists() and record.get("status") == "ready":
                yield {**record, "symbol": symbol, "path": str(path)}
        return
    for path in sorted((root / "series").glob("*.npy")):
        yield {"symbol": path.stem, "path": str(path), "status": "ready"}


def load_source(symbol: str, data_root: Path | str = DATA_ROOT, mmap_mode: Optional[str] = "r") -> np.ndarray:
    """Load one symbol as UTC-ms/open/high/low/close rows."""
    path = Path(data_root) / "series" / f"{symbol.upper()}.npy"
    if not path.exists():
        raise FileNotFoundError(f"No materialized Binance UM 15m stream for {symbol.upper()}: {path}")
    return np.load(path, mmap_mode=mmap_mode, allow_pickle=False)


def _parse_cutoff(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return int(stamp.value // 1_000_000)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff-exclusive", help="UTC timestamp; bars at or after the cutoff are excluded")
    parser.add_argument("--symbols", help="comma-separated symbol allow-list; defaults to the registered local union")
    parser.add_argument("--symbols-file", help="CSV with a symbol column; eligible rows are selected when present")
    args = parser.parse_args()
    cutoff = _parse_cutoff(args.cutoff_exclusive)
    symbols = args.symbols.split(",") if args.symbols else None
    if args.symbols_file:
        eligible = pd.read_csv(args.symbols_file)
        if "eligible" in eligible:
            eligible = eligible.loc[eligible["eligible"].astype(bool)]
        symbols = eligible["symbol"].astype(str).tolist()
    for record in build_local(cutoff_ms=cutoff, symbols=symbols):
        keys = ("symbol", "status", "rows", "first_ts", "last_ts", "gap_segments", "missing_15m_bars", "error_type", "error")
        print(json.dumps({k: record[k] for k in keys if k in record}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch official Binance UM 1h candles for Oct 2026 through the Nov 11 cutoff.

October uses Binance Vision monthly archives. November 1 through the fully
closed 09:00 UTC candle on November 11 uses the Binance UM REST klines API.
Outputs are atomic float64 N x 5 arrays: UTC open time in ms, O, H, L, C.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import threading
import time
import zipfile
from collections import deque
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np
import requests


ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data" / "research" / "alt-isolated-12h-wicks-20261111" / "binance"
OUT = DATA / "hourly12tail"
KEYS = DATA / "monthly_keys"
ELIGIBLE = DATA / "universe_eligible.csv"
VISION_BASE = "https://data.binance.vision/"
UM_KLINES = "https://fapi.binance.com/fapi/v1/klines"
HOUR_MS = 3_600_000
START_MS = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
CUTOFF_MS = int(datetime(2026, 11, 11, 10, tzinfo=timezone.utc).timestamp() * 1000)
NOV_START_MS = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp() * 1000)
PRIORITY = ["IOSTUSDT", "ETCUSDT", "SOLUSDT", "RUNEUSDT", "AVAXUSDT", "NEARUSDT"]
LIQUID = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
    "LINKUSDT", "LTCUSDT", "BCHUSDT", "TRXUSDT", "SUIUSDT", "HYPEUSDT",
    "1000PEPEUSDT", "1000SHIBUSDT", "1000BONKUSDT", "WIFUSDT", "APTUSDT",
    "ARBUSDT", "OPUSDT", "INJUSDT", "FILUSDT", "DOTUSDT", "ATOMUSDT",
    "UNIUSDT", "AAVEUSDT", "NEARUSDT", "ETCUSDT", "XLMUSDT", "AVAXUSDT",
    "SOLUSDT", "RUNEUSDT", "IOSTUSDT",
]

# Conservative rolling REST weight budget. Binance publishes limits per endpoint
# and account/IP; keeping below 1,800 weight/min leaves room for other activity.
_rate_lock = threading.Lock()
_recent_weight: deque[tuple[float, int]] = deque()
_WEIGHT_BUDGET = 1_800


class BinanceApiError(RuntimeError):
    def __init__(self, status_code: int, code: Any, message: str):
        super().__init__(f"Binance UM HTTP {status_code} code={code}: {message}")
        self.status_code = status_code
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}-{threading.get_ident()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _atomic_npy(path: Path, rows: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}-{threading.get_ident()}")
    with tmp.open("wb") as handle:
        np.save(handle, rows, allow_pickle=False)
    os.replace(tmp, path)


def _wait_for_weight(weight: int) -> None:
    while True:
        with _rate_lock:
            now = time.monotonic()
            while _recent_weight and now - _recent_weight[0][0] >= 60:
                _recent_weight.popleft()
            used = sum(item[1] for item in _recent_weight)
            if used + weight <= _WEIGHT_BUDGET:
                _recent_weight.append((now, weight))
                return
            wait_s = max(0.05, 60 - (now - _recent_weight[0][0]))
        time.sleep(min(wait_s, 2.0))


def _archive_key(symbol: str) -> str:
    """Reuse the existing 15m monthly index, changing only interval tokens."""
    key_file = KEYS / f"{symbol}.json"
    if key_file.is_file():
        entries = json.loads(key_file.read_text())
        for key in entries:
            if key.endswith("-15m-2026-10.zip"):
                return key.replace("/15m/", "/1h/").replace("-15m-", "-1h-")
    escaped = quote(symbol, safe="")
    return f"data/futures/um/monthly/klines/{escaped}/1h/{escaped}-1h-2026-10.zip"


def _has_october_monthly_key(symbol: str) -> bool:
    key_file = KEYS / f"{symbol}.json"
    if not key_file.is_file():
        return False
    return any(key.endswith("-15m-2026-10.zip") for key in json.loads(key_file.read_text()))


@lru_cache(maxsize=1)
def _universe_metadata() -> dict[str, dict[str, str]]:
    with ELIGIBLE.open(newline="") as handle:
        return {row["symbol"].upper(): row for row in csv.DictReader(handle)}


def _read_vision_month(symbol: str) -> tuple[np.ndarray, str]:
    url = VISION_BASE + _archive_key(symbol)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = requests.get(url, timeout=(10, 60))
            if response.status_code == 404:
                raise FileNotFoundError(f"Vision archive not found: {url}")
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                csv_names = [name for name in archive.namelist() if name.endswith(".csv")]
                if not csv_names:
                    raise ValueError("Vision zip has no CSV member")
                with archive.open(csv_names[0]) as member:
                    text = io.TextIOWrapper(member, encoding="utf-8-sig")
                    parsed = []
                    for line_no, fields in enumerate(csv.reader(text)):
                        if not fields:
                            continue
                        try:
                            parsed.append([float(value) for value in fields[:5]])
                        except ValueError:
                            if line_no == 0:  # Newer Vision CSVs include a column-name header.
                                continue
                            raise ValueError(f"non-numeric field in Vision CSV at row {line_no + 1}")
                    rows = np.asarray(parsed, dtype=np.float64).reshape((-1, 5))
            rows = _clean_rows(rows, START_MS, NOV_START_MS)
            return rows, url
        except FileNotFoundError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _rest_klines(symbol: str, start_ms: int, end_exclusive_ms: int, limit: int) -> np.ndarray:
    # Kline endpoint weight: 2 for 100-500 rows, 10 above 1,000.
    weight = 2 if limit <= 500 else 10
    params = {
        "symbol": symbol,
        "interval": "1h",
        "startTime": start_ms,
        "endTime": end_exclusive_ms - 1,
        "limit": limit,
    }
    last_error: Exception | None = None
    for attempt in range(6):
        _wait_for_weight(weight)
        try:
            response = requests.get(UM_KLINES, params=params, timeout=(10, 35))
            if response.status_code in (418, 429):
                last_error = RuntimeError(f"Binance UM REST rate limited with HTTP {response.status_code}")
                delay = float(response.headers.get("Retry-After", 2 ** min(attempt + 1, 5)))
                time.sleep(min(max(delay, 1.0), 60.0))
                continue
            payload = response.json()
            if isinstance(payload, dict):
                raise BinanceApiError(response.status_code, payload.get("code"), str(payload.get("msg", payload)))
            response.raise_for_status()
            if not payload:
                return np.empty((0, 5), dtype=np.float64)
            rows = np.asarray([[bar[0], *bar[1:5]] for bar in payload], dtype=np.float64)
            return _clean_rows(rows, start_ms, end_exclusive_ms)
        except Exception as exc:
            last_error = exc
            if attempt == 5:
                break
            time.sleep(min(1.5 * (attempt + 1), 8.0))
    assert last_error is not None
    raise last_error


def _clean_rows(rows: np.ndarray, start_ms: int, end_exclusive_ms: int) -> np.ndarray:
    if rows.size == 0:
        return np.empty((0, 5), dtype=np.float64)
    rows = np.asarray(rows, dtype=np.float64).reshape((-1, 5))
    rows = rows[(rows[:, 0] >= start_ms) & (rows[:, 0] < end_exclusive_ms)]
    rows = rows[(rows[:, 0] % HOUR_MS) == 0]
    rows = rows[np.argsort(rows[:, 0], kind="stable")]
    _, first = np.unique(rows[:, 0], return_index=True)
    rows = rows[np.sort(first)]
    if rows.size and (not np.isfinite(rows).all() or (rows[:, 1:] <= 0).any()):
        raise ValueError("invalid OHLC values in Binance candle rows")
    return rows.astype(np.float64, copy=False)


def _coverage(rows: np.ndarray) -> dict[str, Any]:
    if not len(rows):
        return {"rows": 0, "first_ts": None, "last_ts": None, "missing_hours": None, "gap_segments": 0, "gap_examples": []}
    ts = rows[:, 0].astype(np.int64, copy=False)
    delta = np.diff(ts)
    gap_at = np.flatnonzero(delta > HOUR_MS)
    examples = [
        {"after_ts": int(ts[i]), "before_ts": int(ts[i + 1]), "missing_hours": int(delta[i] // HOUR_MS - 1)}
        for i in gap_at[:100]
    ]
    return {
        "rows": int(len(rows)),
        "first_ts": int(ts[0]),
        "last_ts": int(ts[-1]),
        "missing_hours": int(sum(int(x // HOUR_MS - 1) for x in delta[gap_at])),
        "gap_segments": int(len(gap_at)),
        "gap_examples": examples,
    }


def _eligible_symbols() -> list[str]:
    with ELIGIBLE.open(newline="") as handle:
        return [row["symbol"].upper() for row in csv.DictReader(handle) if row.get("eligible", "").lower() == "true"]


def _cached_valid(symbol: str) -> bool:
    output = OUT / f"{symbol}.npy"
    receipt = OUT / f"{symbol}.json"
    if not output.is_file() or not receipt.is_file():
        return False
    try:
        record = json.loads(receipt.read_text())
        return record.get("status") in {"ready", "absent_in_tail"} and record.get("cutoff_exclusive_ms") == CUTOFF_MS
    except Exception:
        return False


def build_symbol(symbol: str, force: bool = False) -> dict[str, Any]:
    symbol = symbol.upper()
    if not force and _cached_valid(symbol):
        return {"symbol": symbol, "status": "cached", "path": str(OUT / f"{symbol}.npy")}

    sources: list[dict[str, Any]] = []
    failures: list[str] = []
    metadata = _universe_metadata().get(symbol, {})
    basis = metadata.get("basis", "")
    instrument_status = metadata.get("status", "").upper()
    absence_candidate = (
        basis == "archive_only_unclassified"
        or instrument_status == "PENDING_TRADING"
        or metadata.get("current", "").lower() == "false"
    )
    month_rows = np.empty((0, 5), dtype=np.float64)
    try:
        month_rows, month_url = _read_vision_month(symbol)
        sources.append({"source": "binance_vision_monthly_um_1h", "url": month_url, "rows": int(len(month_rows))})
    except FileNotFoundError as exc:
        failures.append(f"monthly_archive_unavailable: {exc}")
        if absence_candidate and not _has_october_monthly_key(symbol):
            # An old archive-only symbol with no October monthly listing has no
            # October window to fill. Probe the recent tail once; -1121/-1122 or an
            # empty response is an absent-in-window receipt, not a data gap.
            try:
                tail_rows = _rest_klines(symbol, NOV_START_MS, CUTOFF_MS, 300)
                sources.append({"source": "binance_um_rest_1h_tail_probe", "url": UM_KLINES, "rows": int(len(tail_rows))})
                if not len(tail_rows):
                    return _publish_absent(symbol, sources, failures + ["no_october_monthly_key_and_rest_tail_empty"])
                return _publish(symbol, tail_rows, sources, failures)
            except BinanceApiError as exc2:
                if exc2.code in {-1121, -1122}:
                    sources.append({"source": "binance_um_rest_1h_tail_probe", "url": UM_KLINES, "rows": 0, "api_code": exc2.code})
                    return _publish_absent(symbol, sources, failures + [f"no_october_monthly_key_and_rest_unknown_symbol: {exc2}"])
                return _publish_failure(symbol, sources, failures + [f"rest_tail_probe_failed: {exc2}"])
            except Exception as exc2:
                return _publish_failure(symbol, sources, failures + [f"rest_tail_probe_failed: {type(exc2).__name__}: {exc2}"])
        # One bounded REST request recovers October when the official archive
        # is absent; max 1,500 bars covers the requested 1,258-hour window.
        try:
            all_rows = _rest_klines(symbol, START_MS, CUTOFF_MS, 1_500)
            sources.append({"source": "binance_um_rest_full_fallback", "url": UM_KLINES, "rows": int(len(all_rows))})
            month_rows = all_rows[all_rows[:, 0] < NOV_START_MS]
            tail_rows = all_rows[all_rows[:, 0] >= NOV_START_MS]
            return _publish(symbol, np.concatenate([month_rows, tail_rows]) if len(month_rows) or len(tail_rows) else all_rows, sources, failures)
        except Exception as exc2:
            if absence_candidate and isinstance(exc2, BinanceApiError) and exc2.code in {-1121, -1122}:
                sources.append({"source": "binance_um_rest_full_probe", "url": UM_KLINES, "rows": 0, "api_code": exc2.code})
                return _publish_absent(symbol, sources, failures + [f"unknown_symbol_no_usable_2026_window: {exc2}"])
            return _publish_failure(symbol, sources, failures + [f"rest_full_fallback_failed: {type(exc2).__name__}: {exc2}"])
    except Exception as exc:
        return _publish_failure(symbol, sources, [f"monthly_archive_failed: {type(exc).__name__}: {exc}"])

    try:
        tail_rows = _rest_klines(symbol, NOV_START_MS, CUTOFF_MS, 300)
        sources.append({"source": "binance_um_rest_1h_tail", "url": UM_KLINES, "rows": int(len(tail_rows))})
    except Exception as exc:
        if absence_candidate and isinstance(exc, BinanceApiError) and exc.code in {-1121, -1122}:
            sources.append({"source": "binance_um_rest_1h_tail_probe", "url": UM_KLINES, "rows": 0, "api_code": exc.code})
            return _publish_absent(symbol, sources, [f"pending_or_archived_symbol_has_no_tail_candles: {exc}"])
        return _publish_failure(symbol, sources, [f"rest_tail_failed: {type(exc).__name__}: {exc}"])

    rows = _clean_rows(np.concatenate([month_rows, tail_rows]), START_MS, CUTOFF_MS)
    return _publish(symbol, rows, sources, failures)


def _publish(symbol: str, rows: np.ndarray, sources: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
    rows = _clean_rows(rows, START_MS, CUTOFF_MS)
    output = OUT / f"{symbol}.npy"
    _atomic_npy(output, rows)
    receipt = {
        "symbol": symbol,
        "status": "ready" if len(rows) else "empty",
        "exchange": "Binance USDⓈ-M Futures",
        "interval": "1h",
        "dtype": "float64",
        "columns": ["utc_ms", "open", "high", "low", "close"],
        "start_inclusive_ms": START_MS,
        "cutoff_exclusive_ms": CUTOFF_MS,
        "cutoff_exclusive_utc": "2026-11-11T10:00:00Z",
        "last_allowed_open_utc": "2026-11-11T09:00:00Z",
        "sources": sources,
        "warnings": warnings,
        "generated_at_utc": _utc_now(),
        **_coverage(rows),
    }
    _atomic_json(OUT / f"{symbol}.json", receipt)
    return {"symbol": symbol, "status": receipt["status"], "rows": len(rows), "sources": sources}


def _publish_failure(symbol: str, sources: list[dict[str, Any]], errors: list[str]) -> dict[str, Any]:
    receipt = {
        "symbol": symbol,
        "status": "failed",
        "exchange": "Binance USDⓈ-M Futures",
        "interval": "1h",
        "start_inclusive_ms": START_MS,
        "cutoff_exclusive_ms": CUTOFF_MS,
        "cutoff_exclusive_utc": "2026-11-11T10:00:00Z",
        "sources": sources,
        "errors": errors,
        "generated_at_utc": _utc_now(),
    }
    _atomic_json(OUT / f"{symbol}.json", receipt)
    return {"symbol": symbol, "status": "failed", "errors": errors}


def _publish_absent(symbol: str, sources: list[dict[str, Any]], evidence: list[str]) -> dict[str, Any]:
    _atomic_npy(OUT / f"{symbol}.npy", np.empty((0, 5), dtype=np.float64))
    receipt = {
        "symbol": symbol,
        "status": "absent_in_tail",
        "exchange": "Binance USDⓈ-M Futures",
        "interval": "1h",
        "dtype": "float64",
        "columns": ["utc_ms", "open", "high", "low", "close"],
        "start_inclusive_ms": START_MS,
        "cutoff_exclusive_ms": CUTOFF_MS,
        "cutoff_exclusive_utc": "2026-11-11T10:00:00Z",
        "sources": sources,
        "evidence": evidence,
        "generated_at_utc": _utc_now(),
        **_coverage(np.empty((0, 5), dtype=np.float64)),
    }
    _atomic_json(OUT / f"{symbol}.json", receipt)
    return {"symbol": symbol, "status": "absent_in_tail", "rows": 0}


def _write_manifest() -> None:
    records = []
    for path in sorted(OUT.glob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            records.append(json.loads(path.read_text()))
        except Exception:
            continue
    manifest = {
        "version": 1,
        "exchange": "Binance USDⓈ-M Futures",
        "interval": "1h",
        "start_inclusive_ms": START_MS,
        "cutoff_exclusive_ms": CUTOFF_MS,
        "cutoff_exclusive_utc": "2026-11-11T10:00:00Z",
        "eligible_symbols": len(_eligible_symbols()),
        "ready": sum(record.get("status") == "ready" for record in records),
        "empty": sum(record.get("status") == "empty" for record in records),
        "absent_in_tail": sum(record.get("status") == "absent_in_tail" for record in records),
        "failed": sum(record.get("status") == "failed" for record in records),
        "receipts": records,
    }
    _atomic_json(OUT / "manifest.json", manifest)


def _order(symbols: list[str]) -> list[str]:
    have = set(symbols)
    ordered = []
    seen = set()
    for symbol in PRIORITY + LIQUID + sorted(have):
        if symbol in have and symbol not in seen:
            ordered.append(symbol)
            seen.add(symbol)
    return ordered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", help="optional symbol subset")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true", help="refetch even when a valid receipt exists")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 16:
        parser.error("--workers must be between 1 and 16")
    symbols = _order([s.upper() for s in args.symbols]) if args.symbols else _order(_eligible_symbols())
    OUT.mkdir(parents=True, exist_ok=True)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    print(f"symbols={len(symbols)} start=2026-10-01T00:00Z cutoff_exclusive=2026-11-11T10:00Z workers={args.workers}", flush=True)
    failed = ready = cached = empty = absent = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(build_symbol, symbol, args.force): symbol for symbol in symbols}
        for done, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = _publish_failure(symbol, [], [f"unhandled: {type(exc).__name__}: {exc}"])
            status = result["status"]
            if status == "failed":
                failed += 1
                print(f"failed {symbol}: {'; '.join(result.get('errors', []))}", flush=True)
            elif status == "cached":
                cached += 1
            elif status == "absent_in_tail":
                absent += 1
                print(f"absent_in_tail {symbol}", flush=True)
            elif status == "empty":
                empty += 1
                print(f"empty {symbol}", flush=True)
            else:
                ready += 1
                print(f"ready {symbol} rows={result.get('rows', '?')}", flush=True)
            if done % 25 == 0 or done == len(symbols):
                print(f"progress={done}/{len(symbols)} ready={ready} cached={cached} empty={empty} absent={absent} failed={failed}", flush=True)
            if done % 25 == 0 or done == len(symbols):
                _write_manifest()


if __name__ == "__main__":
    main()

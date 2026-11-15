"""Refresh Binance UM hourly candles for monthly/first-week research.

The historical base is the all-lifecycle roster and H1 reader used by
``exp-alt-isolated-12h-firstweek-wicks-20261111-v1``. This utility never
rewrites that base. It writes only incremental ``[utc_ms, O, H, L, C]`` arrays
under ``stores/`` and receipts under ``data/research/monthweek-12h-first-touch-20261115``.

The default run fetches the fixed recent window from Binance's public UM REST
API. ``--repair-history`` additionally checks observed large head/internal
gaps and settled-contract delivery months against Binance Vision monthly 1h
archives. Historical bars are admitted only when the official archive reports
both positive base volume and at least one trade; this excludes zero-volume
flatline placeholders. No synthetic candles are created.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import threading
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import numpy as np
import requests


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "data/research/alt-isolated-12h-wicks-20261111/binance"
SOURCE_ROSTER = SOURCE_ROOT / "universe_perpetuals.csv"
OUTPUT_ROOT = ROOT / "data/research/monthweek-12h-first-touch-20261115"
EXPERIMENT_ROOT = ROOT / "experiments/active/exp-alt-isolated-12h-firstweek-wicks-20261111-v1"

FAPI = "https://fapi.binance.com"
UM_KLINES = f"{FAPI}/fapi/v1/klines"
UM_EXCHANGE_INFO = f"{FAPI}/fapi/v1/exchangeInfo"
UM_TIME = f"{FAPI}/fapi/v1/time"
VISION = "https://data.binance.vision/"

HOUR_MS = 3_600_000
DEFAULT_START = "2026-11-11T10:00:00Z"
DEFAULT_CUTOFF = "2026-11-15T15:00:00Z"
MAX_EXPECTED_FUTURE_MS = int(datetime(2100, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
MONTH_RE = re.compile(r"-(\d{4}-\d{2})\.zip$")
API_LIMIT = 499  # one request covers the default ~101 hourly bars at low weight
WEIGHT_BUDGET_PER_MINUTE = 1_800
_weight_lock = threading.Lock()
_recent_weights: deque[tuple[float, int]] = deque()


def _parse_utc(value: str) -> int:
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        raise ValueError(f"Timestamp must include a timezone: {value}")
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).isoformat()


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _atomic_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    with tmp.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def _atomic_npy(path: Path, rows: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    with tmp.open("wb") as handle:
        np.save(handle, rows, allow_pickle=False)
    os.replace(tmp, path)


def _wait_for_weight(weight: int) -> None:
    while True:
        with _weight_lock:
            now = time.monotonic()
            while _recent_weights and now - _recent_weights[0][0] >= 60:
                _recent_weights.popleft()
            used = sum(item[1] for item in _recent_weights)
            if used + weight <= WEIGHT_BUDGET_PER_MINUTE:
                _recent_weights.append((now, weight))
                return
            delay = max(0.05, 60 - (now - _recent_weights[0][0]))
        time.sleep(min(delay, 1.0))


def _get_json(url: str, params: dict[str, Any] | None = None, *, weight: int = 1) -> Any:
    last_error: Exception | None = None
    for attempt in range(4):
        _wait_for_weight(weight)
        try:
            response = requests.get(url, params=params, timeout=(10, 45), headers={
                "User-Agent": "fable-trading-monthweek-hourly-refresh/1.0",
            })
            if response.status_code in (418, 429, 500, 502, 503, 504):
                response.raise_for_status()
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and "code" in payload and int(payload.get("code", 0)) < 0:
                raise RuntimeError(f"Binance error {payload.get('code')}: {payload.get('msg')}")
            return payload
        except Exception as exc:  # retry transient/network errors; preserve final detail in the receipt
            last_error = exc
            if attempt < 3:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"GET {url} failed after retries: {last_error}")


def _load_census_reader():
    if not EXPERIMENT_ROOT.is_dir():
        raise FileNotFoundError(f"Expected source experiment: {EXPERIMENT_ROOT}")
    experiment = str(EXPERIMENT_ROOT)
    if experiment not in sys.path:
        sys.path.insert(0, experiment)
    from census12 import read_raw  # type: ignore[import-not-found]

    return read_raw


def _source_rows(read_raw, symbols: Iterable[str]) -> dict[str, np.ndarray]:
    rows: dict[str, np.ndarray] = {}
    for symbol in symbols:
        raw = np.asarray(read_raw(symbol), dtype=np.float64)
        if raw.ndim != 2 or raw.shape[1] != 5:
            raise ValueError(f"{symbol}: source reader did not return N x 5 OHLC rows")
        if len(raw):
            raw = raw[np.argsort(raw[:, 0], kind="stable")]
        rows[symbol] = raw
    return rows


def _load_base_roster() -> list[dict[str, str]]:
    if not SOURCE_ROSTER.is_file():
        raise FileNotFoundError(f"Missing historical all-lifecycle roster: {SOURCE_ROSTER}")
    with SOURCE_ROSTER.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or "symbol" not in rows[0]:
        raise ValueError(f"Invalid source roster: {SOURCE_ROSTER}")
    symbols = [row["symbol"].strip() for row in rows]
    if any(not symbol for symbol in symbols) or len(symbols) != len(set(symbols)):
        raise ValueError("Source roster has empty or duplicate symbols")
    return rows


def _fetch_live_info(start_ms: int) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    # A cache-buster and no-cache headers avoid reusing an older exchangeInfo edge response.
    live_time = _get_json(UM_TIME)
    time_ms = _safe_int(live_time.get("serverTime")) if isinstance(live_time, dict) else None
    info = _get_json(UM_EXCHANGE_INFO + f"?refresh={int(time.time())}")
    exchange_time_ms = _safe_int(info.get("serverTime"))
    all_symbols = info.get("symbols", [])
    candidates = [
        row for row in all_symbols
        if row.get("quoteAsset") == "USDT"
        and str(row.get("contractType", "")).endswith("PERPETUAL")
        and row.get("symbol")
    ]
    if time_ms is None or time_ms < start_ms:
        raise RuntimeError(f"Binance /time is before requested refresh window: {_iso(time_ms)}")
    if exchange_time_ms is None or exchange_time_ms < start_ms:
        raise RuntimeError(
            f"Binance exchangeInfo is stale relative to requested window: {_iso(exchange_time_ms)}"
        )
    live = {str(row["symbol"]): row for row in candidates}
    return live, {
        "server_time_ms": time_ms,
        "exchange_info_server_time_ms": exchange_time_ms,
        "exchange_info_total_symbols": len(all_symbols),
        "exchange_info_usdt_perpetuals": len(live),
        "exchange_info_status_counts": _count(row.get("status", "") for row in candidates),
    }


def _count(values: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = str(value or "")
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _date_ms(row: dict[str, Any], key: str) -> int | None:
    return _safe_int(row.get(key))


def _is_live_candidate(row: dict[str, Any], cutoff_ms: int, start_ms: int) -> tuple[bool, str]:
    status = str(row.get("status", ""))
    delivery = _date_ms(row, "deliveryDate")
    onboard = _date_ms(row, "onboardDate")
    if status == "PENDING_TRADING":
        return False, "pending_trading"
    if status not in {"TRADING", "SETTLING"}:
        return False, f"status_{status.lower() or 'unknown'}"
    if delivery is not None and delivery < MAX_EXPECTED_FUTURE_MS and delivery <= start_ms:
        return False, "delivery_before_refresh_window"
    if onboard is not None and onboard >= cutoff_ms:
        return False, "onboard_at_or_after_cutoff"
    return True, ""


def _fetch_recent(symbol: str, start_ms: int, cutoff_ms: int, delivery_ms: int | None) -> dict[str, Any]:
    stop_ms = cutoff_ms
    if delivery_ms is not None and delivery_ms < MAX_EXPECTED_FUTURE_MS:
        stop_ms = min(stop_ms, delivery_ms)
    if start_ms >= stop_ms:
        return {"symbol": symbol, "status": "skipped_empty_window", "start_ms": start_ms,
                "cutoff_ms": stop_ms, "rows": [], "error": ""}
    cursor = start_ms
    payload_rows: list[Any] = []
    try:
        while cursor < stop_ms:
            payload = _get_json(UM_KLINES, {
                "symbol": symbol,
                "interval": "1h",
                "startTime": cursor,
                "endTime": stop_ms - 1,
                "limit": API_LIMIT,
            }, weight=2)
            if not isinstance(payload, list):
                raise RuntimeError(f"Unexpected klines response type {type(payload).__name__}")
            if not payload:
                break
            payload_rows.extend(payload)
            last_open = _safe_int(payload[-1][0])
            if last_open is None or last_open < cursor:
                raise RuntimeError("Klines cursor did not advance")
            next_cursor = last_open + HOUR_MS
            if next_cursor <= cursor:
                raise RuntimeError("Klines cursor stalled")
            cursor = next_cursor
            if len(payload) < API_LIMIT:
                break
        parsed = []
        latest_trade_ms = None
        trade_rows = 0
        for row in payload_rows:
            if len(row) < 9:
                continue
            open_ms = _safe_int(row[0])
            if open_ms is None or open_ms < start_ms or open_ms >= stop_ms:
                continue
            values = [float(row[index]) for index in (1, 2, 3, 4)]
            if not all(math.isfinite(value) and value > 0 for value in values):
                continue
            parsed.append([float(open_ms), *values])
            volume = float(row[5])
            trades = _safe_int(row[8]) or 0
            if volume > 0 and trades > 0:
                latest_trade_ms = open_ms if latest_trade_ms is None else max(latest_trade_ms, open_ms)
                trade_rows += 1
        data = np.asarray(parsed, dtype=np.float64).reshape((-1, 5))
        if len(data):
            data = data[np.argsort(data[:, 0], kind="stable")]
            _, unique = np.unique(data[:, 0], return_index=True)
            data = data[np.sort(unique)]
        return {
            "symbol": symbol,
            "status": "ok" if len(data) else "no_closed_bars",
            "start_ms": start_ms,
            "cutoff_ms": stop_ms,
            "rows": data,
            "raw_rows": len(payload_rows),
            "trade_rows": trade_rows,
            "latest_trade_ms": latest_trade_ms,
            "error": "",
        }
    except Exception as exc:
        return {"symbol": symbol, "status": "error", "start_ms": start_ms,
                "cutoff_ms": stop_ms, "rows": np.empty((0, 5)), "raw_rows": len(payload_rows),
                "trade_rows": 0, "latest_trade_ms": None, "error": str(exc)}


def _month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m")


def _month_start(month: str) -> int:
    year, number = map(int, month.split("-"))
    return int(datetime(year, number, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _month_after(month: str) -> str:
    year, number = map(int, month.split("-"))
    return f"{year + 1:04d}-01" if number == 12 else f"{year:04d}-{number + 1:02d}"


def _months(start_ms: int, stop_ms_inclusive: int) -> list[str]:
    first, last = _month(start_ms), _month(stop_ms_inclusive)
    result = []
    month = first
    while month <= last:
        result.append(month)
        month = _month_after(month)
    return result


def _parse_archive_csv(blob: bytes) -> np.ndarray:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not members:
            raise ValueError("Binance Vision ZIP has no CSV member")
        with archive.open(members[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
            rows = []
            for fields in reader:
                if len(fields) < 9:
                    continue
                try:
                    open_ms = int(float(fields[0]))
                    values = [float(fields[index]) for index in (1, 2, 3, 4, 5)]
                    trades = int(float(fields[8]))
                except (TypeError, ValueError, OverflowError):
                    continue  # header row or malformed record
                if not all(math.isfinite(value) for value in values):
                    continue
                if open_ms > 10**15:  # retain compatibility if an archive uses microseconds
                    open_ms //= 1000
                rows.append([open_ms, *values, trades])
    array = np.asarray(rows, dtype=np.float64).reshape((-1, 7))
    if len(array):
        array = array[np.argsort(array[:, 0], kind="stable")]
        _, unique = np.unique(array[:, 0], return_index=True)
        array = array[np.sort(unique)]
    return array  # open_ms, O, H, L, C, base_volume, trade_count


def _fetch_archive_month(symbol: str, month: str) -> tuple[np.ndarray, dict[str, Any]]:
    key = f"data/futures/um/monthly/klines/{symbol}/1h/{symbol}-1h-{month}.zip"
    url = VISION + quote(key, safe="/-_.")
    last_error = ""
    for attempt in range(4):
        try:
            response = requests.get(url, timeout=(10, 60), headers={
                "User-Agent": "fable-trading-monthweek-hourly-refresh/1.0",
            })
            if response.status_code == 404:
                return np.empty((0, 7)), {"symbol": symbol, "month": month,
                    "status": "not_published", "rows": 0, "traded_rows": 0, "url": url, "error": ""}
            response.raise_for_status()
            rows = _parse_archive_csv(response.content)
            traded = (rows[:, 5] > 0) & (rows[:, 6] > 0) if len(rows) else np.zeros(0, dtype=bool)
            return rows, {"symbol": symbol, "month": month, "status": "downloaded",
                "rows": int(len(rows)), "traded_rows": int(traded.sum()), "url": url, "error": ""}
        except Exception as exc:
            last_error = str(exc)
            if attempt < 3:
                time.sleep(0.5 * (2**attempt))
    return np.empty((0, 7)), {"symbol": symbol, "month": month, "status": "error",
        "rows": 0, "traded_rows": 0, "url": url, "error": last_error}


def _first_month_from_keys(symbol: str) -> str | None:
    path = SOURCE_ROOT / "monthly_keys" / f"{symbol}.json"
    if not path.is_file():
        return None
    try:
        keys = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    found = sorted(match.group(1) for key in keys if (match := MONTH_RE.search(str(key))))
    return found[0] if found else None


def _historical_gap_requests(
    base_rows: dict[str, dict[str, str]],
    source_rows: dict[str, np.ndarray],
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    needed: set[tuple[str, str]] = set()
    gap_rows: list[dict[str, Any]] = []
    for symbol, row in base_rows.items():
        raw = source_rows.get(symbol, np.empty((0, 5)))
        first_ms = int(raw[0, 0]) if len(raw) else None
        listed_ms = _safe_int(row.get("listed_ms"))
        if first_ms is not None:
            if listed_ms is not None and first_ms - listed_ms > 72 * HOUR_MS:
                start_ms = listed_ms
                gap_rows.append({"symbol": symbol, "gap_type": "listed_to_first_source",
                    "gap_start_ms": listed_ms, "gap_end_ms": first_ms,
                    "gap_hours": round((first_ms - listed_ms) / HOUR_MS, 2),
                    "observed_first_ms": first_ms})
                needed.update((symbol, month) for month in _months(start_ms, first_ms))
            elif listed_ms is None:
                # Archive-only contracts such as BTCST have no reliable onboardDate.
                # Check the first official monthly key's full 1h archive for an earlier trade.
                first_key_month = _first_month_from_keys(symbol)
                if first_key_month:
                    start_ms = _month_start(first_key_month)
                    needed.add((symbol, first_key_month))
                    gap_rows.append({"symbol": symbol, "gap_type": "archive_only_first_month_check",
                        "gap_start_ms": start_ms, "gap_end_ms": first_ms,
                        "gap_hours": round((first_ms - start_ms) / HOUR_MS, 2),
                        "observed_first_ms": first_ms})
            gaps = np.diff(raw[:, 0].astype(np.int64))
            for index in np.flatnonzero(gaps > 72 * HOUR_MS):
                gap_start = int(raw[index, 0]) + HOUR_MS
                gap_end = int(raw[index + 1, 0])
                gap_rows.append({"symbol": symbol, "gap_type": "internal_over_72h",
                    "gap_start_ms": gap_start, "gap_end_ms": gap_end,
                    "gap_hours": round((gap_end - gap_start) / HOUR_MS, 2),
                    "observed_first_ms": first_ms})
                needed.update((symbol, month) for month in _months(gap_start, gap_end))
        else:
            # No base series: leave lifecycle history unresolved; recent REST may still add current rows.
            continue
    requests = {key: {"symbol": key[0], "month": key[1]} for key in sorted(needed)}
    return requests, gap_rows


def _fetch_archives(keys: Iterable[tuple[str, str]], workers: int) -> dict[tuple[str, str], tuple[np.ndarray, dict[str, Any]]]:
    ordered = sorted(set(keys))
    found: dict[tuple[str, str], tuple[np.ndarray, dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_archive_month, symbol, month): (symbol, month)
                   for symbol, month in ordered}
        for future in as_completed(futures):
            key = futures[future]
            try:
                found[key] = future.result()
            except Exception as exc:  # _fetch_archive_month normally returns an error receipt
                symbol, month = key
                found[key] = (np.empty((0, 7)), {"symbol": symbol, "month": month,
                    "status": "error", "rows": 0, "traded_rows": 0, "url": "", "error": str(exc)})
    return found


def _merge_incremental(destination: Path, additions: list[np.ndarray]) -> np.ndarray:
    parts = [array for array in additions if len(array)]
    if destination.is_file():
        prior = np.load(destination, allow_pickle=False)
        if prior.ndim != 2 or prior.shape[1] != 5:
            raise ValueError(f"Invalid existing increment: {destination}")
        if len(prior):
            parts.append(np.asarray(prior, dtype=np.float64))
    if not parts:
        return np.empty((0, 5), dtype=np.float64)
    merged = np.concatenate(parts, axis=0)
    merged = merged[np.argsort(merged[:, 0], kind="stable")]
    _, unique = np.unique(merged[:, 0], return_index=True)
    return merged[np.sort(unique)]


def run(start_ms: int, cutoff_ms: int, *, repair_history: bool = False, workers: int = 12) -> dict[str, Any]:
    if cutoff_ms <= start_ms:
        raise ValueError("cutoff-exclusive must be later than start")
    if cutoff_ms > int(datetime.now(timezone.utc).timestamp() * 1000):
        raise ValueError("cutoff-exclusive is in the future")
    base_list = _load_base_roster()
    base_by_symbol = {row["symbol"].strip(): row for row in base_list}
    live, live_meta = _fetch_live_info(start_ms)
    all_symbols = sorted(set(base_by_symbol) | set(live))
    read_raw = _load_census_reader()
    source_rows = _source_rows(read_raw, all_symbols)

    additions: dict[str, list[np.ndarray]] = {symbol: [] for symbol in all_symbols}
    recent_receipts: list[dict[str, Any]] = []
    recent_symbols = []
    for symbol, exchange_row in sorted(live.items()):
        include, skip_reason = _is_live_candidate(exchange_row, cutoff_ms, start_ms)
        base_raw = source_rows.get(symbol, np.empty((0, 5)))
        last_source_ms = int(base_raw[-1, 0]) if len(base_raw) else None
        onboard_ms = _date_ms(exchange_row, "onboardDate")
        delivery_ms = _date_ms(exchange_row, "deliveryDate")
        requested_start = max(start_ms, (last_source_ms + HOUR_MS) if last_source_ms is not None else start_ms)
        if onboard_ms is not None:
            requested_start = max(requested_start, onboard_ms)
        if include:
            recent_symbols.append(symbol)
            recent_receipts.append({"symbol": symbol, "exchange_row": exchange_row,
                "start_ms": requested_start, "delivery_ms": delivery_ms})
        else:
            recent_receipts.append({"symbol": symbol, "exchange_row": exchange_row,
                "start_ms": requested_start, "delivery_ms": delivery_ms,
                "skip_reason": skip_reason})

    recent_results: dict[str, dict[str, Any]] = {}
    active_jobs = [item for item in recent_receipts if "skip_reason" not in item]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for item in active_jobs:
            future = pool.submit(_fetch_recent, item["symbol"], item["start_ms"], cutoff_ms, item["delivery_ms"])
            futures[future] = item["symbol"]
        for future in as_completed(futures):
            symbol = futures[future]
            result = future.result()
            recent_results[symbol] = result
            if len(result["rows"]):
                additions.setdefault(symbol, []).append(result["rows"])

    archive_records: dict[tuple[str, str], tuple[np.ndarray, dict[str, Any]]] = {}
    gap_rows: list[dict[str, Any]] = []
    head_added = 0
    internal_added = 0
    last_trade_from_archive: dict[str, tuple[int, str]] = {}
    if repair_history:
        gap_requests, gap_rows = _historical_gap_requests(base_by_symbol, source_rows)
        settlement_keys = set()
        for symbol, base in base_by_symbol.items():
            end_ms = _safe_int(base.get("end_ms"))
            if end_ms is not None and 0 < end_ms < MAX_EXPECTED_FUTURE_MS:
                settlement_keys.add((symbol, _month(end_ms)))
        archive_records = _fetch_archives(set(gap_requests) | settlement_keys, workers=max(4, min(workers, 8)))

        for gap in gap_rows:
            symbol = gap["symbol"]
            left, right = int(gap["gap_start_ms"]), int(gap["gap_end_ms"])
            pieces = []
            for month in _months(left, right):
                archive_data, _receipt = archive_records.get((symbol, month), (np.empty((0, 7)), {}))
                if not len(archive_data):
                    continue
                mask = ((archive_data[:, 0] >= left) & (archive_data[:, 0] < right)
                        & (archive_data[:, 5] > 0) & (archive_data[:, 6] > 0))
                if mask.any():
                    pieces.append(archive_data[mask][:, :5])
            added = np.concatenate(pieces, axis=0) if pieces else np.empty((0, 5))
            if len(added):
                additions.setdefault(symbol, []).append(added)
            gap["official_positive_volume_rows_added"] = int(len(added))
            gap["first_official_trade_ms_in_gap"] = int(added[0, 0]) if len(added) else None
            if gap["gap_type"] == "listed_to_first_source":
                head_added += len(added)
            elif gap["gap_type"] == "internal_over_72h":
                internal_added += len(added)

        for (symbol, month), (archive_data, _receipt) in archive_records.items():
            base = base_by_symbol.get(symbol)
            if base is None:
                continue
            end_ms = _safe_int(base.get("end_ms"))
            if end_ms is None or end_ms >= MAX_EXPECTED_FUTURE_MS or month != _month(end_ms):
                continue
            if not len(archive_data):
                continue
            traded = archive_data[(archive_data[:, 0] <= end_ms)
                                  & (archive_data[:, 5] > 0)
                                  & (archive_data[:, 6] > 0)]
            if len(traded):
                latest = int(traded[-1, 0])
                last_trade_from_archive[symbol] = (latest, "binance_vision_1h_volume_and_trade_count")

    output_root = OUTPUT_ROOT
    symbol_output = output_root / "stores"
    emitted_rows = 0
    for symbol, parts in additions.items():
        if not parts:
            continue
        merged = _merge_incremental(symbol_output / f"{symbol}.npy", parts)
        if len(merged):
            _atomic_npy(symbol_output / f"{symbol}.npy", merged)
            emitted_rows += len(merged)

    roster_rows = []
    for symbol in all_symbols:
        base = base_by_symbol.get(symbol, {})
        current = live.get(symbol, {})
        raw = source_rows.get(symbol, np.empty((0, 5)))
        first_ms = int(raw[0, 0]) if len(raw) else None
        last_ms = int(raw[-1, 0]) if len(raw) else None
        status = current.get("status", base.get("status", ""))
        contract_type = current.get("contractType", base.get("contract_type", ""))
        underlying = current.get("underlyingType", base.get("underlying", ""))
        quote_asset = current.get("quoteAsset", "")
        listed_ms = _date_ms(current, "onboardDate") or _safe_int(base.get("listed_ms"))
        end_ms = _date_ms(current, "deliveryDate") or _safe_int(base.get("end_ms"))
        post_end = raw[raw[:, 0] > end_ms] if len(raw) and end_ms is not None else np.empty((0, 5))
        post_end_constant = bool(len(post_end) and np.all(post_end[:, 1:5] == post_end[0, 1:5]))
        latest_trade_ms = None
        trade_basis = ""
        recent = recent_results.get(symbol)
        if recent and recent.get("latest_trade_ms") is not None:
            latest_trade_ms = int(recent["latest_trade_ms"])
            trade_basis = "binance_um_rest_1h_volume_and_trade_count"
        elif symbol in last_trade_from_archive:
            latest_trade_ms, trade_basis = last_trade_from_archive[symbol]
        elif not len(raw):
            trade_basis = "no_cached_ohlc_or_confirmed_trade"
        else:
            trade_basis = "cached_ohlc_only_volume_unverified"
        recent = recent_results.get(symbol, {})
        roster_rows.append({
            "symbol": symbol,
            "status": status,
            "contract_type": contract_type,
            "underlying": underlying,
            "quote_asset": quote_asset or ("USDT" if symbol.endswith("USDT") else ""),
            "current": bool(symbol in live),
            "exchange_info_present": bool(symbol in live),
            "baseline_current": base.get("current", ""),
            "archive": base.get("archive", ""),
            "listed_ms": listed_ms,
            "end_ms": end_ms,
            "source_first_ms": first_ms,
            "source_last_ms": last_ms,
            "source_rows": len(raw),
            "post_end_rows": len(post_end),
            "post_end_constant_ohlc": post_end_constant,
        "last_confirmed_trade_h1_open_ms": latest_trade_ms,
        "last_confirmed_trade_basis": trade_basis,
            "recent_refresh_status": recent.get("status", "not_queried"),
            "recent_trade_rows": recent.get("trade_rows", 0),
            "first_official_15m_month": _first_month_from_keys(symbol),
        })

    base_set = set(base_by_symbol)
    live_set = set(live)
    new_live = sorted(live_set - base_set)
    new_symbols = sorted(base_set - live_set)
    end_rows = [row for row in roster_rows if row["end_ms"] and int(row["end_ms"]) < MAX_EXPECTED_FUTURE_MS]
    early_end = [row for row in end_rows if row["source_last_ms"] is not None
                 and int(row["source_last_ms"]) < int(row["end_ms"]) - 72 * HOUR_MS]
    post_end_constant_count = sum(bool(row["post_end_rows"]) and row["post_end_constant_ohlc"] for row in end_rows)

    recent_csv = []
    for symbol in all_symbols:
        base = source_rows.get(symbol, np.empty((0, 5)))
        result = recent_results.get(symbol)
        if result is None:
            exchange_row = live.get(symbol, {})
            include, reason = _is_live_candidate(exchange_row, cutoff_ms, start_ms) if exchange_row else (False, "not_in_current_exchange_info")
            last_ms = int(base[-1, 0]) if len(base) else None
            recent_csv.append({"symbol": symbol, "status": "skipped", "skip_reason": reason,
                "source_last_ms": last_ms, "requested_start_ms": None, "cutoff_exclusive_ms": cutoff_ms,
                "returned_rows": 0, "traded_rows": 0, "first_open_ms": None, "last_open_ms": None, "error": ""})
            continue
        rows = result.get("rows", np.empty((0, 5)))
        recent_csv.append({"symbol": symbol, "status": result.get("status", ""), "skip_reason": "",
            "source_last_ms": int(base[-1, 0]) if len(base) else None,
            "requested_start_ms": result.get("start_ms"), "cutoff_exclusive_ms": result.get("cutoff_ms"),
            "returned_rows": len(rows), "traded_rows": result.get("trade_rows", 0),
            "first_open_ms": int(rows[0, 0]) if len(rows) else None,
            "last_open_ms": int(rows[-1, 0]) if len(rows) else None,
            "latest_trade_ms": result.get("latest_trade_ms"), "error": result.get("error", "")})

    archive_rows = [receipt for _key, (_data, receipt) in sorted(archive_records.items())]
    _atomic_csv(output_root / "roster.csv", roster_rows, [
        "symbol", "status", "contract_type", "underlying", "quote_asset", "current",
        "exchange_info_present", "baseline_current", "archive", "listed_ms", "end_ms",
        "source_first_ms", "source_last_ms", "source_rows", "post_end_rows",
        "post_end_constant_ohlc", "last_confirmed_trade_h1_open_ms", "last_confirmed_trade_basis",
        "recent_refresh_status", "recent_trade_rows", "first_official_15m_month",
    ])
    _atomic_csv(output_root / "recent_coverage.csv", recent_csv, [
        "symbol", "status", "skip_reason", "source_last_ms", "requested_start_ms",
        "cutoff_exclusive_ms", "returned_rows", "traded_rows", "first_open_ms", "last_open_ms",
        "latest_trade_ms", "error",
    ])
    _atomic_csv(output_root / "history_gaps.csv", gap_rows, [
        "symbol", "gap_type", "gap_start_ms", "gap_end_ms", "gap_hours",
        "observed_first_ms", "official_positive_volume_rows_added", "first_official_trade_ms_in_gap",
    ])
    _atomic_csv(output_root / "archive_checks.csv", archive_rows, [
        "symbol", "month", "status", "rows", "traded_rows", "url", "error",
    ])

    recent_results_values = list(recent_results.values())
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_roster": str(SOURCE_ROSTER.relative_to(ROOT)),
        "source_reader": "exp-alt-isolated-12h-firstweek-wicks-20261111-v1/census12.py::read_raw",
        "output_format": "float64 N x 5 [utc_open_ms, open, high, low, close]",
        "historical_base_modified": False,
        "recent_window": {"start_inclusive_ms": start_ms, "start_inclusive_utc": _iso(start_ms),
                          "cutoff_exclusive_ms": cutoff_ms, "cutoff_exclusive_utc": _iso(cutoff_ms)},
        "live_exchange_info": live_meta,
        "base_roster_count": len(base_by_symbol),
        "roster_count": len(all_symbols),
        "new_live_symbols_since_base_roster": new_live,
        "base_symbols_missing_from_live_exchange_info": new_symbols,
        "recent_query_symbols": len(recent_symbols),
        "recent_rows_received": sum(len(value.get("rows", [])) for value in recent_results_values),
        "recent_rows_with_positive_volume_and_trades": sum(value.get("trade_rows", 0) for value in recent_results_values),
        "recent_query_status_counts": _count(value.get("status", "") for value in recent_results_values),
        "recent_query_errors": [{"symbol": symbol, "error": result.get("error", "")}
                                 for symbol, result in sorted(recent_results.items()) if result.get("error")],
        "increment_rows_written_or_merged": emitted_rows,
        "increment_symbols": sorted(symbol for symbol, parts in additions.items() if any(len(part) for part in parts)),
        "repair_history_enabled": repair_history,
        "historical_gap_count": len(gap_rows),
        "head_backfill_rows": int(head_added),
        "internal_gap_backfill_rows": int(internal_added),
        "settled_contracts_with_delivery_month_trade_evidence": len(last_trade_from_archive),
        "finite_delivery_end_count": len(end_rows),
        "source_ends_more_than_72h_before_delivery": len(early_end),
        "source_has_constant_post_delivery_ohlc": post_end_constant_count,
        "note": "For settled/archive-only symbols, source_last_ms may be a zero-volume flatline. Use end_ms and last_effective_trade_basis; cached OHLC alone does not prove a trade.",
    }
    _atomic_json(output_root / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START, help="inclusive UTC ISO timestamp")
    parser.add_argument("--cutoff-exclusive", default=DEFAULT_CUTOFF,
                        help="exclusive UTC ISO timestamp, normally the first not-yet-closed H1 open")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--repair-history", action="store_true",
                        help="check/fill listed-to-first and >72h internal gaps from official 1h monthly archives; also verify finite delivery-month trades")
    args = parser.parse_args()
    result = run(_parse_utc(args.start), _parse_utc(args.cutoff_exclusive),
                 repair_history=args.repair_history, workers=max(1, min(args.workers, 24)))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

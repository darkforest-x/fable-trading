"""Materialize Binance UM hourly OHLC inputs for the isolated 12H scan.

The builder reuses compact native H1 quote arrays already produced by the
Binance transfer and archive-cohort work, preferring the February 2022 gap
repair where it overlaps the original stream. It downloads only required
official Binance Vision monthly 1h archives, in memory, to complete lifecycle
edges and observed gaps. Output is one sorted float64 N x 5 array per symbol:
UTC open time in milliseconds, open, high, low, close. No synthetic bars are
inserted, and candles with zero reported volume are retained.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

import numpy as np
import requests


REPO_ROOT = Path(__file__).resolve().parents[3]
BINANCE_ROOT = REPO_ROOT / "data/research/alt-isolated-12h-wicks-20261111/binance"
ELIGIBLE_PATH = BINANCE_ROOT / "universe_eligible.csv"
MONTHLY_KEYS_DIR = BINANCE_ROOT / "monthly_keys"
OUTPUT_DIR = BINANCE_ROOT / "hourly12input"
RECEIPT_PATH = OUTPUT_DIR / "coverage.json"
NATIVE_WORK = REPO_ROOT / "experiments/active/exp-cloud-close-binance-transfer-20261109-v1/work"
GAP_PATCH = REPO_ROOT / "data/research/cloud_close_binance_feb2022_replay"
ARCHIVE_COHORT = REPO_ROOT / "data/research/cloud_close_archive_cohort/symbols"
BINANCE_VISION = "https://data.binance.vision/"
HOUR_MS = 60 * 60 * 1000
CUTOFF = datetime(2026, 10, 1, tzinfo=timezone.utc)
CUTOFF_MS = int(CUTOFF.timestamp() * 1000)
MONTH_RE = re.compile(r"-(\d{4}-\d{2})\.zip$")
PATCH_START_MS = int(datetime(2022, 2, 26, tzinfo=timezone.utc).timestamp() * 1000)
PATCH_END_MS = int(datetime(2022, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
SCHEMA_VERSION = 1


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _utc_month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def _month_start_ms(month: str) -> int:
    year, number = (int(part) for part in month.split("-"))
    return int(datetime(year, number, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _month_next(month: str) -> str:
    year, number = (int(part) for part in month.split("-"))
    if number == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{number + 1:02d}"


def _months_between(start_ms: int, end_ms: int) -> List[str]:
    first, last = _utc_month(start_ms), _utc_month(end_ms)
    result = []
    month = first
    while month <= last:
        result.append(month)
        month = _month_next(month)
    return result


def _month_from_key(key: str) -> Optional[str]:
    match = MONTH_RE.search(key)
    return match.group(1) if match else None


def _load_universe() -> List[dict]:
    with ELIGIBLE_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    symbols = [row["symbol"].strip() for row in rows if row.get("symbol", "").strip()]
    if len(symbols) != len(set(symbols)):
        raise ValueError("universe_eligible.csv contains duplicate symbols")
    return rows


def _monthly_keys(symbol: str) -> Tuple[List[str], dict]:
    path = MONTHLY_KEYS_DIR / f"{symbol}.json"
    stat = path.stat() if path.exists() else None
    signature = {
        "path": str(path.relative_to(REPO_ROOT)),
        "exists": bool(stat),
        "size_bytes": stat.st_size if stat else None,
        "mtime_ns": stat.st_mtime_ns if stat else None,
    }
    if not path.exists():
        return [], signature
    keys = json.loads(path.read_text())
    if not isinstance(keys, list):
        raise ValueError(f"Expected a JSON list of monthly keys: {path}")
    months = sorted({m for key in keys if (m := _month_from_key(key)) and m <= "2026-09"})
    return months, signature


def _source_paths(symbol: str) -> List[Tuple[str, int, Path]]:
    # Higher ranks take precedence if two native products include the same hour.
    candidates = [
        ("binance_transfer_h1", 10, NATIVE_WORK / symbol / "quotes.npz"),
        ("archive_cohort_h1", 20, ARCHIVE_COHORT / symbol / "quotes.npz"),
        ("feb2022_gap_patch_h1", 30, GAP_PATCH / symbol / "quotes.npz"),
    ]
    return [(name, priority, path) for name, priority, path in candidates if path.is_file()]


def _file_signature(path: Path) -> dict:
    stat = path.stat()
    try:
        shown = str(path.relative_to(REPO_ROOT))
    except ValueError:
        shown = str(path)
    return {"path": shown, "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _read_quote(path: Path, cutoff_ms: int) -> Tuple[np.ndarray, dict]:
    with np.load(path, allow_pickle=False) as archive:
        if not {"clock", "prices"}.issubset(archive.files):
            raise ValueError(f"H1 quote archive has no clock/prices fields: {path}")
        clock = np.asarray(archive["clock"], dtype=np.int64)
        prices = np.asarray(archive["prices"], dtype=np.float64)
    if prices.shape != (len(clock), 4):
        raise ValueError(f"Invalid H1 OHLC shape in {path}: {prices.shape}")
    if len(clock) and (np.diff(clock) <= 0).any():
        raise ValueError(f"H1 source clock is not strictly increasing: {path}")
    if len(clock) and (clock % HOUR_MS != 0).any():
        raise ValueError(f"H1 source clock is not aligned to UTC hour: {path}")
    if len(prices) and (not np.isfinite(prices).all() or (prices <= 0).any()):
        raise ValueError(f"H1 source contains non-finite or nonpositive prices: {path}")
    keep = clock < cutoff_ms
    result = np.column_stack((clock[keep].astype(np.float64), prices[keep]))
    meta = {
        **_file_signature(path),
        "source_rows_before_cutoff": int(len(clock)),
        "rows_before_cutoff": int(keep.sum()),
        "rows_after_cutoff": int((~keep).sum()),
        "first_utc_ms": int(clock[keep][0]) if keep.any() else None,
        "last_utc_ms": int(clock[keep][-1]) if keep.any() else None,
    }
    return result, meta


def _official_key(symbol: str, month: str) -> str:
    return f"data/futures/um/monthly/klines/{symbol}/1h/{symbol}-1h-{month}.zip"


def _download_month(symbol: str, month: str, retries: int = 4) -> Tuple[np.ndarray, dict]:
    key = _official_key(symbol, month)
    url = BINANCE_VISION + quote(key, safe="/-_.")
    result = {"month": month, "archive_key": key, "url": url, "auth": "none"}
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=(10, 45))
            if response.status_code == 404:
                result.update(status="not_published", rows=0, attempt=attempt + 1)
                return np.empty((0, 5), dtype=np.float64), result
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
                if len(names) != 1:
                    raise ValueError(f"Expected one CSV member in {key}, found {names}")
                with zf.open(names[0]) as raw:
                    text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                    rows, zero_volume = [], 0
                    for fields in csv.reader(text):
                        if len(fields) < 6:
                            continue
                        try:
                            stamp = int(fields[0])
                            ohlc = [float(value) for value in fields[1:5]]
                            volume = float(fields[5])
                        except (TypeError, ValueError):
                            # Recent monthly dumps may include a header row.
                            continue
                        if stamp % HOUR_MS:
                            raise ValueError(f"Unaligned UTC H1 time in {key}: {stamp}")
                        if _utc_month(stamp) != month:
                            raise ValueError(f"Archive row falls outside its requested month: {key} {stamp}")
                        if not np.isfinite(ohlc).all() or min(ohlc) <= 0 or not np.isfinite(volume):
                            raise ValueError(f"Invalid OHLCV row in official archive {key} at {stamp}")
                        rows.append([stamp, *ohlc])
                        zero_volume += int(volume == 0)
            array = np.asarray(rows, dtype=np.float64).reshape((-1, 5))
            if len(array) and (np.diff(array[:, 0]) <= 0).any():
                raise ValueError(f"Official archive rows are not uniquely sorted: {key}")
            result.update(
                status="downloaded" if len(array) else "published_empty",
                rows=int(len(array)),
                zero_volume_rows=int(zero_volume),
                first_utc_ms=int(array[0, 0]) if len(array) else None,
                last_utc_ms=int(array[-1, 0]) if len(array) else None,
                compressed_bytes=len(response.content),
                attempt=attempt + 1,
            )
            return array[array[:, 0] < CUTOFF_MS], result
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 404:
                result.update(status="not_published", rows=0, attempt=attempt + 1)
                return np.empty((0, 5), dtype=np.float64), result
            last_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < retries:
            time.sleep((0.5, 1.5, 3.0, 6.0)[attempt])
    result.update(status="failed", rows=0, error=last_error, attempt=retries)
    raise RuntimeError(f"Official Binance archive download failed for {key}: {last_error}")


def _download_day(symbol: str, day: str, retries: int = 4) -> Tuple[np.ndarray, dict]:
    key = f"data/futures/um/daily/klines/{symbol}/1h/{symbol}-1h-{day}.zip"
    url = BINANCE_VISION + quote(key, safe="/-_.")
    result = {"day": day, "archive_key": key, "url": url, "auth": "none"}
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=(10, 45))
            if response.status_code == 404:
                result.update(status="not_published", rows=0, attempt=attempt + 1)
                return np.empty((0, 5), dtype=np.float64), result
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
                if len(names) != 1:
                    raise ValueError(f"Expected one CSV member in {key}, found {names}")
                with zf.open(names[0]) as raw:
                    text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                    rows, zero_volume = [], 0
                    for fields in csv.reader(text):
                        if len(fields) < 6:
                            continue
                        try:
                            stamp = int(fields[0])
                            ohlc = [float(value) for value in fields[1:5]]
                            volume = float(fields[5])
                        except (TypeError, ValueError):
                            continue
                        if stamp % HOUR_MS or datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d") != day:
                            raise ValueError(f"Official H1 row falls outside the requested UTC day: {key} {stamp}")
                        if not np.isfinite(ohlc).all() or min(ohlc) <= 0 or not np.isfinite(volume):
                            raise ValueError(f"Invalid OHLCV row in official archive {key} at {stamp}")
                        rows.append([stamp, *ohlc])
                        zero_volume += int(volume == 0)
            array = np.asarray(rows, dtype=np.float64).reshape((-1, 5))
            if len(array) and (np.diff(array[:, 0]) <= 0).any():
                raise ValueError(f"Official daily archive rows are not uniquely sorted: {key}")
            result.update(
                status="downloaded" if len(array) else "published_empty",
                rows=int(len(array)), zero_volume_rows=int(zero_volume),
                first_utc_ms=int(array[0, 0]) if len(array) else None,
                last_utc_ms=int(array[-1, 0]) if len(array) else None,
                compressed_bytes=len(response.content), attempt=attempt + 1,
            )
            return array[array[:, 0] < CUTOFF_MS], result
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 404:
                result.update(status="not_published", rows=0, attempt=attempt + 1)
                return np.empty((0, 5), dtype=np.float64), result
            last_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < retries:
            time.sleep((0.5, 1.5, 3.0, 6.0)[attempt])
    result.update(status="failed", rows=0, error=last_error, attempt=retries)
    raise RuntimeError(f"Official Binance daily H1 archive failed for {key}: {last_error}")


def _days_in_gap(after_ms: int, before_ms: int) -> List[str]:
    first = datetime.fromtimestamp((after_ms + HOUR_MS) / 1000, tz=timezone.utc).date()
    last = datetime.fromtimestamp((before_ms - HOUR_MS) / 1000, tz=timezone.utc).date()
    days, day = [], first
    while day <= last:
        days.append(day.isoformat())
        day = day.fromordinal(day.toordinal() + 1)
    return days


def _daily_repair_symbol(symbol: str, old_record: Optional[dict], max_gap_hours: int = 72) -> dict:
    output_path = OUTPUT_DIR / f"{symbol}.npy"
    if not output_path.is_file() or not old_record or old_record.get("mode") != "full":
        raise FileNotFoundError(f"No full monthly materialization to repair for {symbol}")
    rows = np.load(output_path, allow_pickle=False)
    if rows.ndim != 2 or rows.shape[1] != 5 or (len(rows) and (np.diff(rows[:, 0]) <= 0).any()):
        raise ValueError(f"Invalid existing hourly input for daily gap repair: {symbol}")
    before = _gaps(rows)
    if len(rows) < 2:
        updated = dict(old_record)
        updated["daily_repair"] = {"status": "no_observed_span", "before": before, "after": before}
        return updated

    keys_months, _ = _monthly_keys(symbol)
    key_month_set = set(keys_months)
    times = rows[:, 0].astype(np.int64)
    delta = np.diff(times)
    gap_indexes = np.flatnonzero(delta > HOUR_MS)
    candidates_by_gap, probe_by_gap = [], []
    all_days = set()
    for idx in gap_indexes:
        missing_hours = int(delta[idx] // HOUR_MS) - 1
        after, before_ms = int(times[idx]), int(times[idx + 1])
        days = [day for day in _days_in_gap(after, before_ms) if day[:7] in key_month_set]
        if not days:
            continue
        if missing_hours <= max_gap_hours:
            selected = days
            probes = []
        else:
            midpoint = datetime.fromtimestamp((after + (missing_hours // 2) * HOUR_MS) / 1000, tz=timezone.utc).date().isoformat()
            probes = sorted({days[0], days[-1], midpoint} & set(days))
            # Short multi-day holes are cheap to check exhaustively. For longer
            # interruptions probe first, middle, and last dates before widening.
            selected = days if len(days) <= 4 else probes
        candidates_by_gap.append({"after_utc_ms": after, "before_utc_ms": before_ms,
                                  "missing_hours": missing_hours, "days": days,
                                  "initial_probe_days": probes, "initial_days": selected})
        probe_by_gap.append((selected, days, probes))
        all_days.update(selected)

    archive_rows, archive_meta = [], []
    cache = {}
    for day in sorted(all_days):
        data, meta = _download_day(symbol, day)
        cache[day] = data
        archive_meta.append(meta)
        if len(data):
            archive_rows.append(("binance_vision_daily_1h", 60, data))

    expanded_days = set()
    for gap, (initial, days, probes) in zip(candidates_by_gap, probe_by_gap):
        if not probes or len(days) <= 4:
            continue
        probe_has_rows = any(len(cache.get(day, ())) for day in initial)
        if not probe_has_rows:
            continue
        for day in days:
            if day in cache:
                continue
            data, meta = _download_day(symbol, day)
            cache[day] = data
            archive_meta.append(meta)
            expanded_days.add(day)
            if len(data):
                archive_rows.append(("binance_vision_daily_1h", 60, data))
        gap["expanded_after_probe_found_rows"] = True

    parts = [("previous_materialized_hourly", 50, rows)] + archive_rows
    merged, merge_audit = _merge(parts)
    after = _gaps(merged)
    added = int(len(merged) - len(rows))
    repairable_unfilled = []
    merged_times = merged[:, 0].astype(np.int64) if len(merged) else np.array([], dtype=np.int64)
    for gap in candidates_by_gap:
        missing = [int(stamp) for stamp in range(gap["after_utc_ms"] + HOUR_MS,
                                                 gap["before_utc_ms"], HOUR_MS)
                   if not np.searchsorted(merged_times, stamp) < len(merged_times)
                   or merged_times[np.searchsorted(merged_times, stamp)] != stamp]
        if missing:
            repairable_unfilled.append({"after_utc_ms": gap["after_utc_ms"],
                                        "before_utc_ms": gap["before_utc_ms"],
                                        "missing_hours_remaining": len(missing),
                                        "sample_missing_utc_ms": missing[:24]})
    if added:
        tmp = output_path.with_name(f"{output_path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        try:
            with tmp.open("wb") as handle:
                np.save(handle, np.asarray(merged, dtype=np.float64), allow_pickle=False)
            os.replace(tmp, output_path)
        finally:
            if tmp.exists():
                tmp.unlink()
    stat = output_path.stat()
    updated = dict(old_record)
    tail_missing = old_record.get("tail_missing_hours_to_cutoff_for_current_contract", 0)
    updated.update({
        "status": "ready" if after["gap_segments"] == 0 and not old_record.get("official_15m_months_without_any_output_rows")
                  and not old_record.get("official_1h_months_not_published") and tail_missing == 0 else "ready_with_gaps",
        "output_size_bytes": stat.st_size,
        "output_mtime_ns": stat.st_mtime_ns,
        "shape": list(merged.shape),
        "coverage": after,
        "merge_after_daily_repair": merge_audit,
        "last_action": "daily_archive_gap_repair",
        "daily_repair": {
            "status": "bars_added" if added else ("no_published_rows" if archive_meta else "no_eligible_gaps"),
            "max_gap_hours_filled_exhaustively": max_gap_hours,
            "gaps_considered": candidates_by_gap,
            "daily_archives": archive_meta,
            "days_expanded_after_probe": sorted(expanded_days),
            "bars_added": added,
            "bars_with_zero_volume": int(sum(item.get("zero_volume_rows", 0) for item in archive_meta)),
            "unfilled_hours_in_probed_gaps": repairable_unfilled,
            "before": before,
            "after": after,
            "no_price_synthesis": True,
        },
        "resume": "daily_repair_pass",
    })
    return updated


def _public_fapi_gap(symbol: str, start_ms: int, end_ms: int, retries: int = 3) -> Tuple[np.ndarray, dict]:
    """Query exact missing H1 open times from Binance's unauthenticated public klines API."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    cursor = start_ms
    rows, zero_volume = [], 0
    request_log = []
    while cursor < end_ms:
        params = {"symbol": symbol, "interval": "1h", "startTime": cursor,
                  "endTime": end_ms - 1, "limit": 1500}
        response = None
        last_error = None
        for attempt in range(retries):
            try:
                response = requests.get(url, params=params, timeout=(8, 30))
                if response.status_code >= 500 or response.status_code == 429:
                    response.raise_for_status()
                last_error = None
                break
            except requests.HTTPError as exc:
                last_error = f"HTTP {exc.response.status_code if exc.response is not None else 'unknown'}: {exc}"
                if exc.response is not None and exc.response.status_code < 500 and exc.response.status_code != 429:
                    break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < retries:
                time.sleep((0.5, 1.5, 3.0)[attempt])
        if response is None or last_error:
            request_log.append({"start_time_ms": cursor, "end_time_exclusive_ms": end_ms,
                                "status": "request_error", "error": last_error})
            return np.empty((0, 5), dtype=np.float64), {
                "status": "request_error", "url": url, "auth": "none", "requests": request_log,
                "error": last_error,
            }
        try:
            payload = response.json()
        except ValueError:
            request_log.append({"start_time_ms": cursor, "status": "invalid_json", "http_status": response.status_code})
            return np.empty((0, 5), dtype=np.float64), {
                "status": "invalid_json", "url": url, "auth": "none", "requests": request_log,
            }
        if response.status_code >= 400:
            message = payload.get("msg", response.text) if isinstance(payload, dict) else str(payload)
            request_log.append({"start_time_ms": cursor, "status": "http_error", "http_status": response.status_code,
                                "message": str(message)[:300]})
            return np.empty((0, 5), dtype=np.float64), {
                "status": "http_error", "url": url, "auth": "none", "requests": request_log,
                "http_status": response.status_code, "message": str(message)[:300],
            }
        if not isinstance(payload, list):
            request_log.append({"start_time_ms": cursor, "status": "unexpected_payload", "payload": str(payload)[:300]})
            return np.empty((0, 5), dtype=np.float64), {
                "status": "unexpected_payload", "url": url, "auth": "none", "requests": request_log,
            }
        request_log.append({"start_time_ms": cursor, "end_time_exclusive_ms": end_ms,
                            "status": "ok", "http_status": response.status_code, "rows": len(payload)})
        if not payload:
            break
        page_rows = []
        for item in payload:
            stamp = int(item[0])
            if stamp % HOUR_MS:
                raise ValueError(f"Binance public klines API returned a non-hour timestamp for {symbol}: {stamp}")
            if not start_ms <= stamp < end_ms:
                continue
            ohlc = [float(value) for value in item[1:5]]
            volume = float(item[5])
            if not np.isfinite(ohlc).all() or min(ohlc) <= 0 or not np.isfinite(volume):
                raise ValueError(f"Binance public klines API returned an invalid row for {symbol}: {stamp}")
            page_rows.append([stamp, *ohlc])
            zero_volume += int(volume == 0)
        rows.extend(page_rows)
        if len(payload) < 1500:
            break
        next_cursor = int(payload[-1][0]) + HOUR_MS
        if next_cursor <= cursor:
            raise ValueError(f"Binance public klines pagination did not advance for {symbol}")
        cursor = next_cursor
    array = np.asarray(rows, dtype=np.float64).reshape((-1, 5))
    if len(array):
        array = array[np.argsort(array[:, 0], kind="stable")]
        _, unique = np.unique(array[:, 0], return_index=True)
        array = array[np.sort(unique)]
    return array, {
        "status": "rows_returned" if len(array) else "no_rows",
        "url": url,
        "auth": "none",
        "requested_start_utc_ms": start_ms,
        "requested_end_exclusive_utc_ms": end_ms,
        "rows": int(len(array)),
        "zero_volume_rows": int(zero_volume),
        "first_utc_ms": int(array[0, 0]) if len(array) else None,
        "last_utc_ms": int(array[-1, 0]) if len(array) else None,
        "requests": request_log,
    }


def _api_repair_symbol(symbol: str, old_record: Optional[dict]) -> dict:
    output_path = OUTPUT_DIR / f"{symbol}.npy"
    if not output_path.is_file() or not old_record or old_record.get("mode") != "full":
        raise FileNotFoundError(f"No full monthly materialization to API-repair for {symbol}")
    rows = np.load(output_path, allow_pickle=False)
    before = _gaps(rows)
    if len(rows) < 2:
        updated = dict(old_record)
        updated["api_repair"] = {"status": "no_observed_span", "before": before, "after": before}
        return updated
    times = rows[:, 0].astype(np.int64)
    gap_indexes = np.flatnonzero(np.diff(times) > HOUR_MS)
    repairs, parts = [], []
    for idx in gap_indexes:
        start_ms, end_ms = int(times[idx] + HOUR_MS), int(times[idx + 1])
        data, evidence = _public_fapi_gap(symbol, start_ms, end_ms)
        evidence.update(after_utc_ms=int(times[idx]), before_utc_ms=int(times[idx + 1]),
                        missing_hours=int((end_ms - start_ms) // HOUR_MS))
        repairs.append(evidence)
        if len(data):
            parts.append(("binance_fapi_public_klines", 70, data))
    if not parts:
        updated = dict(old_record)
        updated["api_repair"] = {"status": "no_gap_rows_returned" if repairs else "no_gaps",
                                 "gaps_queried": repairs, "before": before, "after": before,
                                 "no_price_synthesis": True}
        return updated
    merged, merge_audit = _merge([("previous_materialized_hourly", 50, rows)] + parts)
    after = _gaps(merged)
    added = int(len(merged) - len(rows))
    tmp = output_path.with_name(f"{output_path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    try:
        with tmp.open("wb") as handle:
            np.save(handle, np.asarray(merged, dtype=np.float64), allow_pickle=False)
        os.replace(tmp, output_path)
    finally:
        if tmp.exists():
            tmp.unlink()
    stat = output_path.stat()
    updated = dict(old_record)
    tail_missing = old_record.get("tail_missing_hours_to_cutoff_for_current_contract", 0)
    updated.update({
        "status": "ready" if after["gap_segments"] == 0 and not old_record.get("official_15m_months_without_any_output_rows")
                  and not old_record.get("official_1h_months_not_published") and tail_missing == 0 else "ready_with_gaps",
        "output_size_bytes": stat.st_size,
        "output_mtime_ns": stat.st_mtime_ns,
        "shape": list(merged.shape),
        "coverage": after,
        "merge_after_api_repair": merge_audit,
        "last_action": "public_fapi_gap_repair",
        "api_repair": {"status": "bars_added", "gaps_queried": repairs, "bars_added": added,
                       "bars_with_zero_volume": int(sum(item.get("zero_volume_rows", 0) for item in repairs)),
                       "before": before, "after": after, "no_price_synthesis": True},
        "resume": "api_repair_pass",
    })
    return updated


def _merge(parts: Sequence[Tuple[str, int, np.ndarray]]) -> Tuple[np.ndarray, dict]:
    nonempty = [(name, priority, rows) for name, priority, rows in parts if len(rows)]
    if not nonempty:
        return np.empty((0, 5), dtype=np.float64), {
            "input_rows": 0, "duplicate_rows_removed": 0, "conflicting_timestamps": 0,
            "conflict_examples": [], "sources_used": [],
        }
    rows = np.concatenate([item[2] for item in nonempty], axis=0)
    rank = np.concatenate([np.full(len(item[2]), item[1], dtype=np.int16) for item in nonempty])
    order = np.lexsort((rank, rows[:, 0]))
    rows, rank = rows[order], rank[order]
    starts = np.r_[0, np.flatnonzero(np.diff(rows[:, 0]) != 0) + 1]
    ends = np.r_[starts[1:], len(rows)]
    chosen_idx = ends - 1  # highest ranked source wins each duplicate clock.
    chosen = rows[chosen_idx]
    duplicate_rows = int(len(rows) - len(chosen))
    group_id = np.repeat(np.arange(len(starts)), ends - starts)
    values_equal = np.isclose(rows[:, 1:5], chosen[group_id, 1:5], rtol=1e-10, atol=1e-12)
    mismatch_rows = np.flatnonzero(~values_equal.all(axis=1))
    conflict_groups = np.unique(group_id[mismatch_rows]) if len(mismatch_rows) else np.array([], dtype=np.int64)
    name_by_rank = {priority: name for name, priority, _ in nonempty}
    conflict_examples = []
    conflict_months = set()
    for group in conflict_groups:
        a, b, pick = int(starts[group]), int(ends[group]), int(chosen_idx[group])
        month = _utc_month(int(rows[pick, 0]))
        conflict_months.add(month)
        if len(conflict_examples) >= 50:
            continue
        conflict_examples.append({
            "utc_ms": int(rows[pick, 0]),
            "month": month,
            "sources": [name_by_rank.get(int(value), f"rank-{int(value)}") for value in sorted(set(rank[a:b].tolist()))],
            "selected_source": name_by_rank.get(int(rank[pick]), f"rank-{int(rank[pick])}"),
        })
    return chosen, {
        "input_rows": int(len(rows)),
        "duplicate_rows_removed": duplicate_rows,
        "conflicting_timestamps": int(len(conflict_groups)),
        "conflict_months": sorted(conflict_months),
        "conflict_examples": conflict_examples,
        "sources_used": [name for name, _, data in nonempty if len(data)],
    }


def _gaps(rows: np.ndarray) -> dict:
    if not len(rows):
        return {"rows": 0, "first_utc_ms": None, "last_utc_ms": None, "gap_segments": 0,
                "missing_hours": 0, "gap_examples": []}
    times = rows[:, 0].astype(np.int64)
    delta = np.diff(times)
    if (delta % HOUR_MS != 0).any():
        raise ValueError("Merged H1 timestamps contain a non-hour-aligned interval")
    where = np.flatnonzero(delta > HOUR_MS)
    examples, missing_hours = [], 0
    for idx in where:
        count = int(delta[idx] // HOUR_MS) - 1
        missing_hours += count
        if len(examples) < 100:
            examples.append({"after_utc_ms": int(times[idx]), "before_utc_ms": int(times[idx + 1]),
                             "missing_hours": count})
    return {"rows": int(len(rows)), "first_utc_ms": int(times[0]), "last_utc_ms": int(times[-1]),
            "gap_segments": int(len(where)), "missing_hours": int(missing_hours), "gap_examples": examples}


def _download_plan(months: Sequence[str], local: np.ndarray, conflicts: dict) -> List[str]:
    if not months:
        return []
    known = set(months)
    plan = {months[0], months[-1]}
    if not len(local):
        plan.update(months)
    else:
        times = local[:, 0].astype(np.int64)
        present = {_utc_month(int(ts)) for ts in times}
        first, last = _utc_month(int(times[0])), _utc_month(int(times[-1]))
        plan.update(m for m in months if m < first or m > last or m not in present)
        for after, before in zip(times[:-1], times[1:]):
            if before - after > HOUR_MS:
                plan.update(m for m in _months_between(int(after), int(before)) if m in known)
        plan.update(month for month in conflicts.get("conflict_months", []) if month in known)
    return sorted(plan)


def _source_inputs(symbol: str, cutoff_ms: int) -> Tuple[List[Tuple[str, int, np.ndarray]], List[dict], dict]:
    parts, source_meta = [], []
    for name, priority, path in _source_paths(symbol):
        rows, metadata = _read_quote(path, cutoff_ms)
        parts.append((name, priority, rows))
        source_meta.append(metadata)
    local, merge_audit = _merge(parts)
    return parts, source_meta, {"local_rows": local, "local_merge": merge_audit}


def _build_symbol(symbol: str, universe_row: dict, mode: str, old_record: Optional[dict], resume: bool) -> dict:
    keys_months, keys_signature = _monthly_keys(symbol)
    source_paths = _source_paths(symbol)
    source_signatures = [_file_signature(path) for _, _, path in source_paths]
    input_signature = {"builder_schema_version": SCHEMA_VERSION, "keys": keys_signature,
                       "sources": source_signatures, "cutoff_exclusive_ms": CUTOFF_MS}
    output_path = OUTPUT_DIR / f"{symbol}.npy"
    if (resume and output_path.is_file() and old_record and old_record.get("input_signature") == input_signature
            and old_record.get("mode") == mode and old_record.get("status") in ("ready", "ready_with_gaps", "native_partial", "awaiting_official")
            and old_record.get("output_size_bytes") == output_path.stat().st_size
            and old_record.get("output_mtime_ns") == output_path.stat().st_mtime_ns):
        cached = dict(old_record)
        cached["resume"] = "reused_existing_atomic_output"
        return cached

    parts, source_meta, local_data = _source_inputs(symbol, CUTOFF_MS)
    local = local_data["local_rows"]
    local_merge = local_data["local_merge"]
    month_downloads = []
    official_metadata = []
    if mode == "full":
        if not keys_months:
            # Empty 15m inventories can occur for a newly archived ticker. Check the
            # official 1h inventory once before declaring that it has no Vision data.
            try:
                from yoyo.data.binance_um_vision_listing import files
                listed = files(symbol, interval="1h")
                keys_months = sorted({m for key in listed if (m := _month_from_key(key)) and m <= "2026-09"})
            except Exception as exc:
                official_metadata.append({"status": "inventory_lookup_failed", "error": f"{type(exc).__name__}: {exc}"})
        plan = _download_plan(keys_months, local, local_merge)
        for month in plan:
            rows, meta = _download_month(symbol, month)
            official_metadata.append(meta)
            if len(rows):
                parts.append(("binance_vision_monthly_1h", 40, rows))
                month_downloads.append(month)

    merged, merge_audit = _merge(parts)
    merged = merged[merged[:, 0] < CUTOFF_MS]
    if len(merged) and (not np.isfinite(merged).all() or (merged[:, 1:] <= 0).any()):
        raise ValueError(f"Merged {symbol} hourly input has invalid OHLC values")
    coverage = _gaps(merged)
    observed_months = sorted({_utc_month(int(ts)) for ts in merged[:, 0]}) if len(merged) else []
    missing_archive_months = [month for month in keys_months if month not in set(observed_months)]
    not_published = [entry["month"] for entry in official_metadata if entry.get("status") == "not_published"]
    failed_inventory = any(entry.get("status") == "inventory_lookup_failed" for entry in official_metadata)
    listed_ms = universe_row.get("listed_ms", "")
    listed_ms_value = int(float(listed_ms)) if listed_ms not in (None, "") else None
    is_current = str(universe_row.get("current", "")).strip().lower() == "true"
    listing_precedes_cutoff = listed_ms_value is None or listed_ms_value < CUTOFF_MS
    expected_through_cutoff = mode == "full" and is_current and listing_precedes_cutoff
    tail_missing_hours = 0
    if expected_through_cutoff:
        if len(merged):
            tail_missing_hours = max(0, int((CUTOFF_MS - (int(merged[-1, 0]) + HOUR_MS)) // HOUR_MS))
        else:
            tail_missing_hours = -1
    tail_gap = expected_through_cutoff and tail_missing_hours != 0

    if mode == "native_only":
        status = "native_partial" if len(merged) else "awaiting_official"
    elif not len(merged):
        status = "awaiting_official"
    elif coverage["gap_segments"] or missing_archive_months or not_published or failed_inventory or tail_gap:
        status = "ready_with_gaps"
    else:
        status = "ready"

    # Timestamp is exactly representable in float64 for this UTC range.
    out = np.asarray(merged, dtype=np.float64).reshape((-1, 5))
    tmp = output_path.with_name(f"{output_path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with tmp.open("wb") as handle:
            np.save(handle, out, allow_pickle=False)
        os.replace(tmp, output_path)
    finally:
        if tmp.exists():
            tmp.unlink()
    stat = output_path.stat()

    before_listed = int(np.sum(out[:, 0] < listed_ms_value)) if listed_ms_value is not None and len(out) else 0
    return {
        "symbol": symbol,
        "status": status,
        "mode": mode,
        "auth": "none; public Binance Vision archives only",
        "input_signature": input_signature,
        "source_files": source_meta,
        "source_merge": local_merge,
        "output": str(output_path.relative_to(REPO_ROOT)),
        "output_size_bytes": stat.st_size,
        "output_mtime_ns": stat.st_mtime_ns,
        "dtype": "float64",
        "shape": list(out.shape),
        "columns": ["utc_ms", "open", "high", "low", "close"],
        "cutoff_exclusive_utc": CUTOFF.isoformat().replace("+00:00", "Z"),
        "first_official_month_from_15m_keys": keys_months[0] if keys_months else None,
        "last_official_month_from_15m_keys": keys_months[-1] if keys_months else None,
        "official_15m_key_months": len(keys_months),
        "official_1h_months_downloaded": month_downloads,
        "official_1h_downloads": official_metadata,
        "official_15m_months_without_any_output_rows": missing_archive_months,
        "official_1h_months_not_published": not_published,
        "declared_listed_ms": listed_ms_value,
        "declared_end_ms": int(float(universe_row["end_ms"])) if universe_row.get("end_ms") not in (None, "") else None,
        "current_in_instrument_snapshot": is_current,
        "instrument_status": universe_row.get("status", ""),
        "expected_through_cutoff": expected_through_cutoff,
        "tail_missing_hours_to_cutoff_for_current_contract": tail_missing_hours,
        "rows_before_declared_listing": before_listed,
        "merge": merge_audit,
        "coverage": coverage,
        "observed_months": observed_months,
        "zero_volume_rows_in_downloaded_archives": int(sum(entry.get("zero_volume_rows", 0) for entry in official_metadata)),
        "volume_note": "Cached quote arrays contain OHLC only; official H1 rows downloaded here preserve zero-volume candles and count them in the receipt.",
        "resume": "rebuilt",
    }


def _write_progress(records: dict, selected: Sequence[str], mode: str, started: str) -> None:
    values = list(records.values())
    ready = sum(record.get("status") == "ready" for record in values)
    gaps = sum(record.get("status") == "ready_with_gaps" for record in values)
    pending = sum(record.get("status") in ("native_partial", "awaiting_official") for record in values)
    failures = sum(record.get("status") == "failed" for record in values)
    _atomic_json(RECEIPT_PATH, {
        "schema_version": SCHEMA_VERSION,
        "experiment": "exp-alt-isolated-12h-firstweek-wicks-20261111-v1",
        "venue": "Binance USD-M",
        "interval": "1h",
        "universe_source": str(ELIGIBLE_PATH.relative_to(REPO_ROOT)),
        "expected_universe_symbols": len(_load_universe()),
        "selected_symbols_this_run": list(selected),
        "mode": mode,
        "started_utc": started,
        "cutoff_exclusive_utc": CUTOFF.isoformat().replace("+00:00", "Z"),
        "authentication": "none; public Binance Vision monthly archive downloads",
        "output_columns": ["utc_ms", "open", "high", "low", "close"],
        "sorting": "strictly ascending UTC hour; duplicate timestamps resolved by source priority and reported",
        "bar_policy": "preserve observed OHLC, including official zero-volume rows; never synthesize missing hours or post-delist prices",
        "progress": {"recorded": len(values), "ready": ready, "ready_with_gaps": gaps, "pending": pending, "failed": failures},
        "symbols": records,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", help="Optional symbol subset")
    parser.add_argument("--limit", type=int, help="Only process the first N eligible symbols")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--native-only", action="store_true", help="Materialize cached compact H1 quotes without official archive fill")
    parser.add_argument("--daily-repair", action="store_true", help="Fill gaps from published official daily 1h ZIPs, without price synthesis")
    parser.add_argument("--api-repair", action="store_true", help="Query remaining H1 gaps from Binance's public UM klines endpoint")
    parser.add_argument("--no-resume", action="store_true", help="Rebuild symbols even when matching output receipts exist")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if sum((args.native_only, args.daily_repair, args.api_repair)) > 1:
        parser.error("--native-only, --daily-repair, and --api-repair are separate build modes")
    rows = _load_universe()
    by_symbol = {row["symbol"].strip(): row for row in rows}
    selected = list(by_symbol)
    if args.symbols:
        unknown = sorted(set(args.symbols) - set(by_symbol))
        if unknown:
            parser.error(f"Symbols are not in universe_eligible.csv: {unknown}")
        wanted = set(args.symbols)
        selected = [symbol for symbol in selected if symbol in wanted]
    if args.limit is not None:
        selected = selected[:max(0, args.limit)]
    mode = "api_repair" if args.api_repair else ("daily_repair" if args.daily_repair else ("native_only" if args.native_only else "full"))
    started = datetime.now(timezone.utc).isoformat()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if RECEIPT_PATH.exists():
        try:
            records = json.loads(RECEIPT_PATH.read_text()).get("symbols", {})
        except (json.JSONDecodeError, AttributeError):
            records = {}
    else:
        records = {}
    failures = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                _daily_repair_symbol, symbol, records.get(symbol)
            ) if args.daily_repair else (executor.submit(
                _api_repair_symbol, symbol, records.get(symbol)
            ) if args.api_repair else executor.submit(
                _build_symbol, symbol, by_symbol[symbol], mode, records.get(symbol), not args.no_resume
            )): symbol
            for symbol in selected
        }
        for index, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                record = future.result()
                records[symbol] = record
                state = record["status"]
                print(f"{index}/{len(selected)} {symbol} {state} {record.get('shape', [0])[0]} bars", flush=True)
            except Exception as exc:
                failures += 1
                records[symbol] = {"symbol": symbol, "status": "failed", "mode": mode,
                                   "error": f"{type(exc).__name__}: {exc}", "auth": "none"}
                print(f"{index}/{len(selected)} {symbol} FAILED {type(exc).__name__}: {exc}", flush=True)
            _write_progress(records, selected, mode, started)
    _write_progress(records, selected, mode, started)
    if failures:
        raise SystemExit(f"{failures} symbols failed; see {RECEIPT_PATH}")


if __name__ == "__main__":
    main()

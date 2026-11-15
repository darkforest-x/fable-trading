"""Remove Binance USD-M flat, zero-trade lifecycle gaps from H1 inputs.

Only exact ``O=H=L=C=previous close`` runs of at least 72 contiguous hours are
investigated. A full UTC day is removed only when Binance's official 1d kline
confirms zero base volume, zero quote volume, and zero trades. Partial run-edge
hours require the same zero-trade evidence from official 1h klines. Verified
zero spans remain timestamp gaps in the returned tape so a caller can keep
positions from crossing separate contract lifecycles.

Sources: Binance USD-M ``GET /fapi/v1/klines`` returns volume, quote volume,
and trade count in its 12-field kline; ``GET /fapi/v1/exchangeInfo`` includes
``deliveryDate``. Official monthly archive ZIPs are used only to fill missing
REST days/hours and are checked against their official CHECKSUM sidecars.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import time
import zipfile
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np
import requests


ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = ROOT / "data/research/monthweek-12h-lifecycles-20261115"
REST_ROOT = "https://fapi.binance.com"
REST_ROOTS = ("https://www.binance.com", REST_ROOT)
KLINES_URL = f"{REST_ROOT}/fapi/v1/klines"
VISION_ROOT = "https://data.binance.vision/data/futures/um/monthly/klines"
KLINE_DOC_URL = "https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/market-data/rest-api/Kline-Candlestick-Data"

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
MIN_FLAT_MS = 72 * HOUR_MS
KLINE_WIDTH = 12
KLINE_COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_base_volume",
    "taker_buy_quote_volume", "ignore",
)
SCHEMA_VERSION = 1


def _utc_day(timestamp_ms: int) -> int:
    return timestamp_ms // DAY_MS * DAY_MS


def _utc_month(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).strftime("%Y-%m")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    with tmp.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    os.replace(tmp, path)


def _request(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    attempts: int = 4,
    timeout: tuple[int, int] = (5, 25),
) -> tuple[int, bytes]:
    """Bounded public GET; HTTP 404 is returned so receipts can preserve it."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            if response.status_code == 404:
                return 404, response.content
            if response.status_code in (418, 429, 500, 502, 503, 504):
                last_error = requests.HTTPError(
                    f"HTTP {response.status_code} for {response.url}", response=response
                )
                if attempt + 1 < attempts:
                    retry_after = float(response.headers.get("Retry-After", 2 ** attempt))
                    time.sleep(min(max(retry_after, 0.5), 12.0))
                    continue
                raise last_error
            if response.status_code >= 400:
                return response.status_code, response.content
            return response.status_code, response.content
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _rest_request(path: str, params: dict[str, Any] | None = None) -> tuple[int, bytes, str]:
    """Try the standard UM host, then Binance's equivalent public host."""
    errors = []
    for root in REST_ROOTS:
        try:
            status, body = _request(root + path, params, attempts=2, timeout=(4, 12))
            if status not in (403, 418, 429, 500, 502, 503, 504):
                return status, body, root + path
            errors.append(f"{root}: HTTP {status}")
        except requests.RequestException as exc:
            errors.append(f"{root}: {exc}")
    raise requests.RequestException("; ".join(errors))


def _month_bounds(month: str) -> tuple[int, int]:
    year, month_num = map(int, month.split("-"))
    first = int(datetime(year, month_num, 1, tzinfo=timezone.utc).timestamp() * 1000)
    days = monthrange(year, month_num)[1]
    return first, first + days * DAY_MS - 1


def _parse_kline_rows(rows: list[Any], interval: str) -> tuple[np.ndarray, list[str]]:
    expected_ms = DAY_MS if interval == "1d" else HOUR_MS
    parsed: list[list[float]] = []
    errors: list[str] = []
    for row_i, row in enumerate(rows):
        if len(row) < KLINE_WIDTH:
            errors.append(f"row_{row_i}: expected 12 columns, got {len(row)}")
            continue
        try:
            open_ms = int(row[0])
            close_ms = int(row[6])
            # Binance changed some archive timestamps to microseconds; normalize
            # both endpoints together to the millisecond contract used by raw.
            if open_ms >= 100_000_000_000_000:
                open_ms //= 1000
                close_ms //= 1000
            out = [
                float(open_ms), float(row[1]), float(row[2]), float(row[3]),
                float(row[4]), float(row[5]), float(close_ms), float(row[7]),
                float(row[8]), float(row[9]), float(row[10]), float(row[11]),
            ]
        except (TypeError, ValueError, OverflowError) as exc:
            errors.append(f"row_{row_i}: numeric parse failed: {exc}")
            continue
        if close_ms - open_ms != expected_ms - 1:
            errors.append(f"row_{row_i}: invalid {interval} close boundary")
            continue
        if (not np.isfinite(out).all() or min(out[1:5]) <= 0 or out[5] < 0
                or out[7] < 0 or out[8] < 0
                or out[2] < max(out[1], out[4])
                or out[3] > min(out[1], out[4]) or out[3] > out[2]):
            errors.append(f"row_{row_i}: invalid OHLCV/count values")
            continue
        parsed.append(out)
    if not parsed:
        return np.empty((0, KLINE_WIDTH), dtype=np.float64), errors
    result = np.asarray(parsed, dtype=np.float64)
    result = result[np.argsort(result[:, 0], kind="stable")]
    _, unique_ix = np.unique(result[:, 0], return_index=True)
    result = result[np.sort(unique_ix)]
    return result, errors


def _monthly_archive(symbol: str, interval: str, month: str) -> tuple[np.ndarray, dict[str, Any]]:
    escaped = quote(symbol, safe="")
    name = f"{escaped}-{interval}-{month}.zip"
    url = f"{VISION_ROOT}/{escaped}/{interval}/{quote(name, safe='')}"
    checksum_url = url + ".CHECKSUM"
    try:
        checksum_status, checksum_body = _request(checksum_url)
        if checksum_status == 404:
            return np.empty((0, KLINE_WIDTH)), {
                "month": month, "interval": interval, "status": "checksum_http_404",
                "url": url, "checksum_url": checksum_url,
            }
        if checksum_status != 200:
            return np.empty((0, KLINE_WIDTH)), {
                "month": month, "interval": interval,
                "status": f"checksum_http_{checksum_status}", "url": url,
                "checksum_url": checksum_url,
            }
        parts = checksum_body.decode("utf-8").strip().split()
        if len(parts) != 2 or parts[1] != name:
            return np.empty((0, KLINE_WIDTH)), {
                "month": month, "interval": interval, "status": "checksum_filename_mismatch",
                "url": url, "checksum_url": checksum_url,
            }
        status, body = _request(url)
        if status == 404:
            return np.empty((0, KLINE_WIDTH)), {
                "month": month, "interval": interval, "status": "archive_http_404",
                "url": url, "checksum_url": checksum_url,
            }
        if status != 200:
            return np.empty((0, KLINE_WIDTH)), {
                "month": month, "interval": interval,
                "status": f"archive_http_{status}", "url": url,
                "checksum_url": checksum_url,
            }
        actual = hashlib.sha256(body).hexdigest()
        if actual.lower() != parts[0].lower():
            return np.empty((0, KLINE_WIDTH)), {
                "month": month, "interval": interval, "status": "checksum_mismatch",
                "url": url, "checksum_url": checksum_url,
            }
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = [member for member in archive.namelist() if member.lower().endswith(".csv")]
            if len(members) != 1:
                return np.empty((0, KLINE_WIDTH)), {
                    "month": month, "interval": interval, "status": "csv_member_mismatch",
                    "url": url, "checksum_url": checksum_url, "members": members,
                }
            with archive.open(members[0]) as member:
                table = list(csv.reader(io.TextIOWrapper(member, encoding="utf-8-sig")))
        if table and table[0] and table[0][0].strip().lower() in {"open_time", "open time"}:
            table = table[1:]
        parsed, errors = _parse_kline_rows(table, interval)
        month_start, month_end = _month_bounds(month)
        parsed = parsed[(parsed[:, 0] >= month_start) & (parsed[:, 0] <= month_end)]
        return parsed, {
            "month": month, "interval": interval, "status": "ok",
            "url": url, "checksum_url": checksum_url,
            "rows": int(len(parsed)), "parse_errors": errors[:20],
        }
    except (requests.RequestException, UnicodeDecodeError, zipfile.BadZipFile,
            OSError, ValueError) as exc:
        return np.empty((0, KLINE_WIDTH)), {
            "month": month, "interval": interval, "status": "error",
            "url": url, "checksum_url": checksum_url, "error": str(exc),
        }


def _rest_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> tuple[np.ndarray, dict[str, Any]]:
    if end_ms < start_ms:
        return np.empty((0, KLINE_WIDTH)), {
            "interval": interval, "start_ms": start_ms, "end_ms": end_ms,
            "status": "empty_range", "rows": 0,
        }
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": int(start_ms),
        "endTime": int(end_ms),
        "limit": 1500,
    }
    try:
        status, body, klines_url = _rest_request("/fapi/v1/klines", params)
        if status != 200:
            return np.empty((0, KLINE_WIDTH)), {
                **params, "status": f"http_{status}", "rows": 0,
                "url": klines_url,
            }
        payload = json.loads(body)
        if not isinstance(payload, list):
            raise ValueError("REST klines response is not an array")
        parsed, errors = _parse_kline_rows(payload, interval)
        parsed = parsed[(parsed[:, 0] >= start_ms) & (parsed[:, 0] <= end_ms)]
        return parsed, {
            **params, "status": "ok", "rows": int(len(parsed)),
            "parse_errors": errors[:20], "url": klines_url,
        }
    except (requests.RequestException, ValueError, TypeError) as exc:
        return np.empty((0, KLINE_WIDTH)), {
            **params, "status": "error", "rows": 0,
            "error": str(exc), "url": KLINES_URL,
        }


def _merge_rows(*arrays: np.ndarray) -> np.ndarray:
    nonempty = [array for array in arrays if len(array)]
    if not nonempty:
        return np.empty((0, KLINE_WIDTH), dtype=np.float64)
    merged = np.concatenate(nonempty, axis=0)
    merged = merged[np.argsort(merged[:, 0], kind="stable")]
    _, unique_ix = np.unique(merged[:, 0], return_index=True)
    return merged[np.sort(unique_ix)]


def _cached_interval(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    required_opens: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Fetch and cache one candidate interval, falling back per missing month."""
    start_day, end_day = _utc_day(start_ms), _utc_day(end_ms)
    lo = datetime.fromtimestamp(start_day / 1000, timezone.utc).strftime("%Y%m%d")
    hi = datetime.fromtimestamp(end_day / 1000, timezone.utc).strftime("%Y%m%d")
    symbol_dir = CACHE_ROOT / symbol
    cache_data = symbol_dir / f"{interval}_{lo}_{hi}.npz"
    cache_receipt = symbol_dir / f"{interval}_{lo}_{hi}.json"
    if cache_data.is_file() and cache_receipt.is_file():
        old_receipt = json.loads(cache_receipt.read_text())
        if old_receipt.get("schema_version") == SCHEMA_VERSION and old_receipt.get("complete"):
            with np.load(cache_data, allow_pickle=False) as payload:
                result = payload["rows"]
            return result, old_receipt

    rows: list[np.ndarray] = []
    rest_receipts = []
    period_ms = DAY_MS if interval == "1d" else HOUR_MS
    # Binance documents max limit=1500. Split only if this evidence window is
    # longer than one response can represent.
    cursor = start_day if interval == "1d" else start_ms
    last_open = end_day if interval == "1d" else end_ms - (HOUR_MS - 1)
    while cursor <= last_open:
        chunk_last = min(last_open, cursor + (1500 - 1) * period_ms)
        chunk_end = chunk_last + period_ms - 1
        data, rec = _rest_klines(symbol, interval, cursor, chunk_end)
        rest_receipts.append(rec)
        if len(data):
            rows.append(data)
        cursor = chunk_last + period_ms

    combined = _merge_rows(*rows)
    expected = set(int(value) for value in required_opens)
    found = set(int(value) for value in combined[:, 0]) if len(combined) else set()
    missing_opens = sorted(expected - found)
    archive_receipts = []
    if missing_opens:
        month_opens: dict[str, list[int]] = {}
        for opening in missing_opens:
            month_opens.setdefault(_utc_month(opening), []).append(opening)
        supplements = []
        for month, opens in sorted(month_opens.items()):
            archive, rec = _monthly_archive(symbol, interval, month)
            rec["requested_opens"] = len(opens)
            archive_receipts.append(rec)
            if len(archive):
                allowed = np.asarray(opens, dtype=np.int64)
                supplements.append(archive[np.isin(archive[:, 0].astype(np.int64), allowed)])
        combined = _merge_rows(combined, *supplements)

    found = set(int(value) for value in combined[:, 0]) if len(combined) else set()
    unresolved = sorted(expected - found)
    failed = any(rec["status"] == "error" for rec in rest_receipts + archive_receipts)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "interval": interval,
        "source_docs": [KLINE_DOC_URL],
        "requested_ms": [int(start_ms), int(end_ms)],
        "requested_open_count": len(expected),
        "rest_requests": rest_receipts,
        "archive_requests": archive_receipts,
        "unresolved_opens_ms": unresolved,
        "complete": not failed,
        "cached_rows": int(len(combined)),
        "cache_data": str(cache_data),
    }
    _atomic_npz(cache_data, rows=combined)
    _atomic_json(cache_receipt, receipt)
    return combined, receipt


def _flat_runs(raw: np.ndarray) -> list[tuple[int, int]]:
    """Find contiguous H1 runs exactly equal to their previous close."""
    if len(raw) < 2:
        return []
    prev = raw[:-1, 4]
    current = raw[1:, 1:5]
    flat = (
        (np.diff(raw[:, 0]) == HOUR_MS)
        & (current[:, 0] == prev)
        & (current[:, 1] == prev)
        & (current[:, 2] == prev)
        & (current[:, 3] == prev)
    )
    indices = np.flatnonzero(flat) + 1
    if not len(indices):
        return []
    output: list[tuple[int, int]] = []
    start = prior = int(indices[0])
    for value in indices[1:]:
        value = int(value)
        if value != prior + 1:
            if (prior - start + 1) * HOUR_MS >= MIN_FLAT_MS:
                output.append((start, prior))
            start = value
        prior = value
    if (prior - start + 1) * HOUR_MS >= MIN_FLAT_MS:
        output.append((start, prior))
    return output


def _zero_trade(row: np.ndarray) -> bool:
    """Official Kline indexes 5/7/8 are base vol, quote vol, and trade count."""
    return float(row[5]) == 0.0 and float(row[7]) == 0.0 and int(row[8]) == 0


def _flat_row_matches_day(raw: np.ndarray, indices: np.ndarray, daily_row: np.ndarray) -> bool:
    if len(indices) == 0:
        return False
    day_price = float(daily_row[1])
    if not np.all(daily_row[1:5] == day_price):
        return False
    values = raw[indices, 1:5]
    return bool(np.all(values == day_price))


def _hour_flat_matches(raw: np.ndarray, index: int) -> bool:
    if index <= 0:
        return False
    return bool(
        raw[index, 1] == raw[index, 2] == raw[index, 3] == raw[index, 4]
        and raw[index, 1] == raw[index - 1, 4]
        and raw[index, 0] - raw[index - 1, 0] == HOUR_MS
    )


def clean_lifecycle(symbol: str, raw: np.ndarray, end_ms: int | None = None) -> np.ndarray:
    """Return H1 bars before ``end_ms`` with only verified lifecycle gaps removed.

    ``end_ms`` is an exclusive delivery cutoff from the caller's official
    contract roster. No raw timestamp at or after it is returned, and no
    REST/ZIP candle whose close time is at or after it is queried. The returned tape keeps original
    ``[open_ts_ms, O, H, L, C]`` rows, except bars at/after that cutoff and full days or edge hours verified as zero-trade within
    a >=72h flat run. Removed intervals are visible as timestamp discontinuities.
    
    The Binance official daily Kline schema's volume fields are indexes 5 and 7
    and trade count is index 8 (see :data:`KLINE_DOC_URL`).
    """
    bars = np.asarray(raw, dtype=np.float64)
    if bars.ndim != 2 or bars.shape[1] != 5:
        raise ValueError("raw must be an N x 5 float array: open_ts_ms, O, H, L, C")
    if not len(bars):
        return np.empty((0, 5), dtype=np.float64)
    if not np.isfinite(bars).all() or np.any(np.diff(bars[:, 0]) <= 0):
        raise ValueError("raw timestamps and OHLC must be finite and strictly increasing")
    if np.any(bars[:, 1:] <= 0):
        raise ValueError("raw OHLC values must be positive")
    if np.any(bars[:, 2] < np.maximum(bars[:, 1], bars[:, 4])) or np.any(
        bars[:, 3] > np.minimum(bars[:, 1], bars[:, 4])
    ) or np.any(bars[:, 3] > bars[:, 2]):
        raise ValueError("raw OHLC rows are inconsistent")
    timestamps_f = bars[:, 0]
    timestamps = timestamps_f.astype(np.int64)
    if not np.array_equal(timestamps_f, timestamps.astype(np.float64)):
        raise ValueError("raw open_ts_ms values must be integer milliseconds")

    input_rows = len(bars)
    if end_ms is not None:
        cutoff = int(end_ms)
        if cutoff != end_ms:
            raise ValueError("end_ms must be an integer millisecond timestamp")
        if cutoff < 0:
            raise ValueError("end_ms must be nonnegative")
        bars = bars[timestamps < cutoff]
        if not len(bars):
            return np.empty((0, 5), dtype=np.float64)
        timestamps = bars[:, 0].astype(np.int64)
        evidence_end_ms = cutoff - 1
    else:
        evidence_end_ms = int(timestamps[-1]) + HOUR_MS - 1

    original_rows = len(bars)
    runs = _flat_runs(bars)
    run_receipts = []
    daily_zero: dict[int, np.ndarray] = {}
    hourly_zero: dict[int, np.ndarray] = {}
    hourly_attempts = []

    for run_start_i, run_end_i in runs:
        start_ms = int(bars[run_start_i, 0])
        end_open_ms = int(bars[run_end_i, 0])
        candidate_days = np.arange(_utc_day(start_ms), _utc_day(end_open_ms) + DAY_MS, DAY_MS, dtype=np.int64)
        complete_days = candidate_days[candidate_days + DAY_MS - 1 <= evidence_end_ms]
        daily_rows = np.empty((0, KLINE_WIDTH), dtype=np.float64)
        daily_receipt = None
        if len(complete_days):
            daily_rows, daily_receipt = _cached_interval(
                str(symbol).upper(), "1d", int(complete_days[0]),
                int(complete_days[-1] + DAY_MS - 1), complete_days,
            )
            for row in daily_rows:
                day = int(row[0])
                if _zero_trade(row):
                    daily_zero[day] = row

        # H1 is used only for run-edge dates whose full UTC day is not confirmed
        # zero by a daily Kline. Entire zero-trade days need no hourly request.
        edge_days = {int(_utc_day(start_ms)), int(_utc_day(end_open_ms))}
        edge_indices: list[int] = []
        for day in edge_days:
            if day in daily_zero:
                continue
            idx = np.flatnonzero((bars[:, 0] >= day) & (bars[:, 0] < day + DAY_MS))
            idx = idx[(idx >= run_start_i) & (idx <= run_end_i)]
            for index in idx:
                if int(bars[index, 0]) + HOUR_MS - 1 <= evidence_end_ms:
                    edge_indices.append(int(index))
        edge_indices = sorted(set(edge_indices))
        h_receipts = []
        if edge_indices:
            edge_opens = np.asarray([int(bars[i, 0]) for i in edge_indices], dtype=np.int64)
            hrows, hreceipt = _cached_interval(
                str(symbol).upper(), "1h", int(edge_opens[0]),
                int(edge_opens[-1] + HOUR_MS - 1), edge_opens,
            )
            hourly_attempts.extend(hreceipt.get("rest_requests", []))
            h_receipts.append(hreceipt)
            for row in hrows:
                opening = int(row[0])
                if _zero_trade(row):
                    hourly_zero[opening] = row

        run_receipts.append({
            "start_ms": start_ms,
            "end_open_ms": end_open_ms,
            "flat_bars": int(run_end_i - run_start_i + 1),
            "classification": "tail" if run_end_i == len(bars) - 1 else "internal",
            "daily_receipt": daily_receipt,
            "hourly_receipts": h_receipts,
        })

    remove = np.zeros(len(bars), dtype=bool)
    daily_removed: list[int] = []
    daily_conflicts: list[int] = []
    for day, row in daily_zero.items():
        indices = np.flatnonzero((bars[:, 0] >= day) & (bars[:, 0] < day + DAY_MS))
        if _flat_row_matches_day(bars, indices, row):
            remove[indices] = True
            daily_removed.append(day)
        else:
            daily_conflicts.append(day)

    hourly_removed: list[int] = []
    timestamp_to_index = {int(ts): i for i, ts in enumerate(timestamps)}
    for opening, row in hourly_zero.items():
        index = timestamp_to_index.get(opening)
        if index is not None and not remove[index] and _hour_flat_matches(bars, index):
            remove[index] = True
            hourly_removed.append(opening)

    cleaned = bars[~remove]
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "symbol": str(symbol).upper(),
        "fetched_utc": datetime.now(timezone.utc).isoformat(),
        "input_rows_after_end_cut": original_rows,
        "rows_after_cleaning": int(len(cleaned)),
        "end_ms_exclusive": int(end_ms) if end_ms is not None else None,
        "rows_before_end_cut": input_rows,
        "removed_by_delivery_cutoff_rows": input_rows - original_rows,
        "evidence_end_ms": int(evidence_end_ms),
        "delivery_cutoff_source": "caller official contract roster" if end_ms is not None else None,
        "candidate_flat_runs": run_receipts,
        "removed_zero_trade_days_utc_ms": sorted(daily_removed),
        "daily_zero_raw_mismatch_days_utc_ms": sorted(daily_conflicts),
        "removed_zero_trade_hours_ms": sorted(hourly_removed),
        "candidate_daily_zero_count": len(daily_zero),
        "candidate_hourly_zero_count": len(hourly_zero),
        "output_timestamp_gaps": int(np.count_nonzero(np.diff(cleaned[:, 0]) != HOUR_MS)) if len(cleaned) > 1 else 0,
        "sources": {
            "rest_klines": KLINES_URL,
            "monthly_archive": VISION_ROOT,
            "kline_docs": KLINE_DOC_URL,
        },
    }
    _atomic_json(CACHE_ROOT / str(symbol).upper() / "clean_receipt.json", receipt)
    return cleaned


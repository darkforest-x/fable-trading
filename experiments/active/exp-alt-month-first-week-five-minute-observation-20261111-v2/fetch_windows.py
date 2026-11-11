#!/usr/bin/env python3
"""Fetch native OKX 5m windows around frozen first-week outer-rail H1 touches.

Each event is anchored to its frozen source H1 open. The requested window is
120 native 5m bars before that hour and 120 bars starting at that hour. Output
is descriptive source data only; this collector does not classify or trade.

The OKX public history-candles endpoint is unauthenticated. Its official API
guide documents the endpoint, 20 requests / 2 seconds per IP, pagination
semantics, 300-row maximum, and response fields:
https://www.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks-history
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import math
import os
import random
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = ROOT / "experiments/active/exp-alt-month-first-week-five-minute-observation-20261111-v2"
INPUT = ROOT / "experiments/active/exp-alt-month-first-week-entries-20261110-v1/events.csv.gz"
LOCAL_DIR = ROOT / "data/kline_fetched"
OUTPUT = ROOT / "data/research/month-first-week-okx-5m-first-touch-windows-v2"
CACHE_DIR = OUTPUT / "event_cache"
URL = "https://www.okx.com/api/v5/market/history-candles"
BAR_MS = 5 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
PRE_BARS = 120
POST_BARS = 120
WINDOW_BARS = PRE_BARS + POST_BARS
REQUEST_LIMIT = 260  # one page; OKX documents a maximum of 300
MAX_ATTEMPTS = 6
CACHE_SCHEMA_VERSION = 1

EVENT_EXTRA_FIELDS = [
    "inst_id", "touch_hour_ms", "touch_hour_utc",
    "window_start_ms", "window_start_utc",
    "window_end_exclusive_ms", "window_end_exclusive_utc",
    "requested_bars", "returned_bars", "contiguous_bars", "missing_bars",
    "confirmed_bars", "unconfirmed_bars", "confirm_unknown_bars",
    "status", "source", "api_code", "http_status", "attempts",
    "request_params", "local_file", "local_overlap_bars",
    "local_overlap_conflicts", "first_returned_utc", "last_returned_utc",
    "detail",
]
CANDLE_FIELDS = [
    "event_id", "symbol", "cohort", "inst_id", "bar_offset_from_touch_hour",
    "ts_ms", "ts_utc", "open_time", "raw_ts", "open", "high", "low",
    "close", "vol", "volCcy", "volCcyQuote", "confirm", "source",
]


def _utc_iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat()


def _parse_touch(value: str) -> int:
    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    ms = int(parsed.timestamp() * 1000)
    if ms % HOUR_MS:
        raise ValueError(f"touch H1 timestamp is not hour-aligned: {value}")
    return ms


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_events() -> tuple[list[dict[str, str]], str]:
    if not INPUT.exists():
        raise FileNotFoundError(INPUT)
    fingerprint = _sha256_file(INPUT)
    with gzip.open(INPUT, "rt", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"event_id", "symbol", "touch"}.issubset(reader.fieldnames):
            raise ValueError(f"unexpected event schema: {reader.fieldnames}")
        rows = list(reader)
        fields = list(reader.fieldnames)
    if len(rows) != 5182:
        raise ValueError(f"frozen input event count changed: {len(rows)} != 5182")
    ids = [row["event_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("event_id is not unique in frozen input")
    # Preserve the source field order for the event ledger.
    rows[0]["_source_fields"] = json.dumps(fields)
    return rows, fingerprint


class RequestLimiter:
    """Conservative shared per-process rolling IP-limit guard (18 / 2s)."""

    def __init__(self, max_per_2s: int = 18, min_interval: float = 0.11) -> None:
        self.max_per_2s = max_per_2s
        self.min_interval = min_interval
        self.lock = threading.Lock()
        self.starts: deque[float] = deque()
        self.last_start = 0.0

    def wait(self) -> None:
        with self.lock:
            while True:
                now = time.monotonic()
                while self.starts and now - self.starts[0] >= 2.0:
                    self.starts.popleft()
                gap = self.min_interval - (now - self.last_start)
                capacity_wait = 2.0 - (now - self.starts[0]) if len(self.starts) >= self.max_per_2s else 0.0
                wait_for = max(gap, capacity_wait)
                if wait_for <= 0:
                    self.last_start = now
                    self.starts.append(now)
                    return
                time.sleep(wait_for + 0.002)


LIMITER = RequestLimiter()
LOCAL_CACHE: dict[str, tuple[dict[int, list[str]], list[str], int]] = {}
LOCAL_LOCK = threading.Lock()
STATS_LOCK = threading.Lock()
STATS: dict[str, Any] = {
    "api_attempts": 0, "api_success_responses": 0, "api_51001": 0,
    "cache_hits": 0, "local_complete": 0, "api_events": 0,
}


def _cache_path(event_id: str) -> Path:
    token = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{token}.json.gz"


def _write_json_gzip_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    with tmp.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            compressed.write((json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8"))
    os.replace(tmp, path)


def _read_event_cache(event_id: str, input_fingerprint: str) -> dict[str, Any] | None:
    path = _cache_path(event_id)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            cached = json.load(stream)
        if (cached.get("schema_version") != CACHE_SCHEMA_VERSION
                or cached.get("event_id") != event_id
                or cached.get("input_sha256") != input_fingerprint):
            return None
        return cached
    except (OSError, EOFError, json.JSONDecodeError):
        return None


def _load_local(symbol: str) -> tuple[dict[int, list[str]], list[str], int]:
    with LOCAL_LOCK:
        if symbol in LOCAL_CACHE:
            return LOCAL_CACHE[symbol]
    pattern = f"okx_{symbol}_USDT_SWAP_5m_*.csv"
    files = sorted(LOCAL_DIR.glob(pattern)) if LOCAL_DIR.exists() else []
    rows: dict[int, list[str]] = {}
    conflicts = 0
    for path in files:
        try:
            with path.open("r", encoding="utf-8", newline="") as stream:
                for row in csv.DictReader(stream):
                    try:
                        ts_raw = str(row["ts"])
                        ts_ms = int(float(ts_raw))
                        if ts_ms % BAR_MS:
                            continue
                        normalized = [
                            ts_raw,
                            str(row["open"]), str(row["high"]),
                            str(row["low"]), str(row["close"]),
                            str(row.get("volume", "")), "", "", "",
                        ]
                    except (KeyError, TypeError, ValueError):
                        continue
                    old = rows.get(ts_ms)
                    if old is not None and old[1:5] != normalized[1:5]:
                        conflicts += 1
                    else:
                        rows[ts_ms] = normalized
        except OSError:
            continue
    result = (rows, [str(p.relative_to(ROOT)) for p in files], conflicts)
    with LOCAL_LOCK:
        LOCAL_CACHE[symbol] = result
    return result


def _local_bars(event: dict[str, str], touch_ms: int) -> tuple[dict[int, dict[str, str]], list[str], int]:
    by_ts, files, conflicts = _load_local(event["symbol"])
    start_ms = touch_ms - PRE_BARS * BAR_MS
    end_ms = touch_ms + POST_BARS * BAR_MS
    selected: dict[int, dict[str, str]] = {}
    for ts_ms in range(start_ms, end_ms, BAR_MS):
        raw = by_ts.get(ts_ms)
        if raw is not None:
            selected[ts_ms] = _candle_dict(raw, source="kline_fetched_local")
    return selected, files, conflicts


def _candle_dict(raw: list[Any], source: str) -> dict[str, str]:
    # OKX raw shape: [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]. Local files
    # have only ts/OHLC/volume, so missing ancillary fields remain empty.
    values = [str(v) if v is not None else "" for v in raw]
    values += [""] * max(0, 9 - len(values))
    ts_ms = int(float(values[0]))
    return {
        "raw_ts": values[0], "ts_ms": str(ts_ms), "ts_utc": _utc_iso(ts_ms),
        "open_time": _utc_iso(ts_ms), "open": values[1], "high": values[2],
        "low": values[3], "close": values[4], "vol": values[5],
        "volCcy": values[6], "volCcyQuote": values[7], "confirm": values[8],
        "source": source,
    }


def _api_bars(payload: dict[str, Any], start_ms: int, end_ms: int) -> tuple[dict[int, dict[str, str]], int]:
    selected: dict[int, dict[str, str]] = {}
    duplicates = 0
    for raw in payload.get("data", []):
        if not isinstance(raw, (list, tuple)) or len(raw) < 9:
            continue
        try:
            ts_ms = int(raw[0])
        except (TypeError, ValueError):
            continue
        if ts_ms < start_ms or ts_ms >= end_ms:
            continue
        if ts_ms in selected:
            duplicates += 1
            continue
        selected[ts_ms] = _candle_dict(list(raw[:9]), source="okx_history_candles")
    return selected, duplicates


def _expected_ms(touch_ms: int) -> list[int]:
    return list(range(touch_ms - PRE_BARS * BAR_MS,
                      touch_ms + POST_BARS * BAR_MS, BAR_MS))


def _row_for_event(event: dict[str, str], candle: dict[str, str], touch_ms: int) -> dict[str, str]:
    ts_ms = int(candle["ts_ms"])
    row = {field: event.get(field, "") for field in ("event_id", "symbol", "cohort")}
    row.update({
        "inst_id": f"{event['symbol']}-USDT-SWAP",
        "bar_offset_from_touch_hour": str((ts_ms - touch_ms) // BAR_MS),
    })
    row.update({key: candle.get(key, "") for key in CANDLE_FIELDS if key not in row})
    return row


def _coverage(bars: dict[int, dict[str, str]], expected: list[int]) -> dict[str, int | str]:
    present = [ts for ts in expected if ts in bars]
    missing = len(expected) - len(present)
    longest = current = 0
    previous = None
    for ts in present:
        current = current + 1 if previous is not None and ts - previous == BAR_MS else 1
        longest = max(longest, current)
        previous = ts
    confirmed = sum(bars[ts].get("confirm", "") == "1" for ts in present)
    unconfirmed = sum(bars[ts].get("confirm", "") == "0" for ts in present)
    unknown = len(present) - confirmed - unconfirmed
    return dict(returned_bars=len(present), contiguous_bars=longest,
                missing_bars=missing, confirmed_bars=confirmed,
                unconfirmed_bars=unconfirmed, confirm_unknown_bars=unknown,
                first_returned_utc=_utc_iso(present[0]) if present else "",
                last_returned_utc=_utc_iso(present[-1]) if present else "")


def _status_from_coverage(coverage: dict[str, Any], sources: set[str], api_code: str) -> str:
    count = int(coverage["returned_bars"])
    expected = WINDOW_BARS
    if count == expected and int(coverage["missing_bars"]) == 0:
        if int(coverage["unconfirmed_bars"]) > 0:
            return "complete_unconfirmed"
        if int(coverage["confirm_unknown_bars"]) > 0:
            return "complete_confirm_unknown" if len(sources) == 1 and "kline_fetched_local" in sources else "complete_mixed_confirm_unknown"
        return "complete_confirmed"
    if api_code == "51001":
        return "api_51001_partial_local" if count else "api_51001"
    if count:
        return "partial"
    if api_code == "0":
        return "empty_api"
    return "api_error"


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "fable-trading-research-fetch/1"})
    return session


def _retry_after(response: requests.Response | None) -> float | None:
    if response is None:
        return None
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _request_window(session: requests.Session, inst_id: str, end_ms: int) -> dict[str, Any]:
    params = {"instId": inst_id, "bar": "5m", "after": str(end_ms), "limit": str(REQUEST_LIMIT)}
    last_error = ""
    http_status = ""
    response_payload: dict[str, Any] | None = None
    attempts = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts = attempt
        LIMITER.wait()
        with STATS_LOCK:
            STATS["api_attempts"] += 1
        response: requests.Response | None = None
        try:
            response = session.get(URL, params=params, timeout=(10, 30))
            http_status = str(response.status_code)
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                if attempt == MAX_ATTEMPTS:
                    break
                delay = _retry_after(response)
                if delay is None:
                    delay = min(30.0, 1.5 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.35)
                time.sleep(delay)
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("response JSON root is not an object")
            code = str(payload.get("code", ""))
            if code == "50011":
                last_error = f"OKX rate-limit code 50011: {payload.get('msg', '')}"
                if attempt == MAX_ATTEMPTS:
                    response_payload = payload
                    break
                time.sleep(min(30.0, 2.0 * attempt) + random.uniform(0.0, 0.5))
                continue
            if code in ("0", "51001"):
                with STATS_LOCK:
                    STATS["api_success_responses"] += 1
                    if code == "51001":
                        STATS["api_51001"] += 1
                response_payload = payload
                break
            response_payload = payload
            last_error = f"OKX code {code}: {payload.get('msg', '')}"
            break
        except (requests.RequestException, ValueError) as error:
            last_error = f"{type(error).__name__}: {error}"
            if attempt == MAX_ATTEMPTS:
                break
            delay = _retry_after(response)
            if delay is None:
                delay = min(30.0, 1.5 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.35)
            time.sleep(delay)
    return {
        "payload": response_payload, "attempts": attempts,
        "http_status": http_status, "params": params,
        "error": last_error if response_payload is None or str(response_payload.get("code")) not in ("0", "51001") else "",
    }


def _read_result_cache(event: dict[str, str], input_fingerprint: str) -> dict[str, Any] | None:
    path = _cache_path(event["event_id"])
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            result = json.load(stream)
        if (result.get("schema_version") != CACHE_SCHEMA_VERSION
                or result.get("event_id") != event["event_id"]
                or result.get("input_sha256") != input_fingerprint):
            return None
        return result
    except (OSError, EOFError, json.JSONDecodeError):
        return None


def _build_result(event: dict[str, str], touch_ms: int, api_result: dict[str, Any] | None,
                  local: dict[int, dict[str, str]], local_files: list[str], local_conflicts: int,
                  api_code_override: str = "", source_note: str = "") -> dict[str, Any]:
    start_ms = touch_ms - PRE_BARS * BAR_MS
    end_ms = touch_ms + POST_BARS * BAR_MS
    expected = _expected_ms(touch_ms)
    local_window = {ts: row for ts, row in local.items() if start_ms <= ts < end_ms}
    if len(local_window) == WINDOW_BARS and all(ts in local_window for ts in expected):
        selected = dict(local_window)
        api_code = ""
        status = "complete_confirm_unknown"
        source = "kline_fetched_local"
        attempts = 0
        http_status = ""
        request = {}
        detail = "完整覆盖窗口来自data/kline_fetched；源文件未包含confirm列，因此confirm留空。"
        with STATS_LOCK:
            STATS["local_complete"] += 1
    else:
        selected = {}
        api_code = api_code_override
        attempts = 0
        http_status = ""
        request = {}
        payload = None
        if api_result is not None:
            payload = api_result.get("payload")
            attempts = int(api_result.get("attempts", 0))
            http_status = api_result.get("http_status", "")
            request = api_result.get("params", {})
            if payload is not None:
                api_code = str(payload.get("code", ""))
                api_rows, _ = _api_bars(payload, start_ms, end_ms)
                selected.update(api_rows)
            detail = api_result.get("error", "")
        else:
            detail = source_note
        api_keys = set(selected)
        overlap_conflicts = 0
        for ts_ms, row in local_window.items():
            if ts_ms in selected:
                if any(str(selected[ts_ms].get(field, "")) != str(row.get(field, ""))
                       for field in ("open", "high", "low", "close")):
                    # Compare decimal values, not formatting differences.
                    try:
                        from decimal import Decimal
                        if any(Decimal(selected[ts_ms].get(field, "0")) != Decimal(row.get(field, "0"))
                               for field in ("open", "high", "low", "close")):
                            overlap_conflicts += 1
                    except Exception:
                        overlap_conflicts += 1
            else:
                selected[ts_ms] = row
        coverage = _coverage(selected, expected)
        sources = {row["source"] for row in selected.values()}
        status = _status_from_coverage(coverage, sources, api_code)
        if status == "api_error" and api_result and api_result.get("payload") is None:
            status = "http_error"
        source = "+".join(sorted(sources)) if sources else ("okx_history_candles" if api_result else "none")
        if overlap_conflicts:
            detail = (detail + "; " if detail else "") + f"local/API OHLC conflicts={overlap_conflicts}; API rows retained on overlap"
        if not detail and coverage["missing_bars"]:
            detail = f"窗口缺{coverage['missing_bars']}根；未补K线或替代数据源"
        detail = (detail + "; " if detail and source_note else detail) + source_note
        api_result = dict(api_result or {})
        api_result["local_overlap_bars"] = len(set(local_window) & api_keys)
        api_result["local_overlap_conflicts"] = overlap_conflicts
    coverage = _coverage(selected, expected)
    if source == "kline_fetched_local":
        local_overlap_bars = WINDOW_BARS
        overlap_conflicts = local_conflicts
    else:
        local_overlap_bars = len(set(local_window) & set(selected))
        overlap_conflicts = int((api_result or {}).get("local_overlap_conflicts", 0))
    candles = [_row_for_event(event, selected[ts], touch_ms) for ts in expected if ts in selected]
    result = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "event_id": event["event_id"],
        "input_sha256": "",
        "record": {
            **{key: value for key, value in event.items() if key != "_source_fields"},
            "inst_id": f"{event['symbol']}-USDT-SWAP",
            "touch_hour_ms": touch_ms,
            "touch_hour_utc": _utc_iso(touch_ms),
            "window_start_ms": start_ms,
            "window_start_utc": _utc_iso(start_ms),
            "window_end_exclusive_ms": end_ms,
            "window_end_exclusive_utc": _utc_iso(end_ms),
            "requested_bars": WINDOW_BARS,
            **coverage,
            "status": status,
            "source": source,
            "api_code": api_code,
            "http_status": http_status,
            "attempts": attempts,
            "request_params": json.dumps(request, sort_keys=True, separators=(",", ":")),
            "local_file": "|".join(local_files),
            "local_overlap_bars": local_overlap_bars,
            "local_overlap_conflicts": overlap_conflicts,
            "detail": detail,
        },
        "candles": candles,
        "raw_response": (api_result or {}).get("payload"),
        "retryable": status in ("http_error", "api_error"),
    }
    return result


def _save_result(event: dict[str, str], fingerprint: str, result: dict[str, Any]) -> None:
    result["input_sha256"] = fingerprint
    _write_json_gzip_atomic(_cache_path(event["event_id"]), result)


def _cached_symbol_51001(events: list[dict[str, str]], fingerprint: str) -> str:
    for event in events:
        cached = _read_result_cache(event, fingerprint)
        if cached and cached.get("record", {}).get("status") == "api_51001":
            return event["event_id"]
    return ""


def _process_symbol(symbol: str, events: list[dict[str, str]], fingerprint: str,
                    limiter: RequestLimiter) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    session = _new_session()
    cached_51001_event = _cached_symbol_51001(events, fingerprint)
    for event in events:
        cached = _read_result_cache(event, fingerprint)
        if cached and not cached.get("retryable", False):
            with STATS_LOCK:
                STATS["cache_hits"] += 1
            output.append(cached)
            continue
        try:
            touch_ms = _parse_touch(event["touch"])
        except (TypeError, ValueError) as error:
            result = _build_result(event, 0, None, {}, [], 0, source_note=f"invalid_touch_time: {error}")
            result["record"]["status"] = "invalid_touch_time"
            _save_result(event, fingerprint, result)
            output.append(result)
            continue
        local, local_files, local_conflicts = _local_bars(event, touch_ms)
        if len(local) == WINDOW_BARS:
            result = _build_result(event, touch_ms, None, local, local_files, local_conflicts)
            _save_result(event, fingerprint, result)
            output.append(result)
            continue
        if cached_51001_event:
            result = _build_result(
                event, touch_ms, None, local, local_files, local_conflicts,
                api_code_override="51001",
                source_note=f"symbol skipped after {cached_51001_event} returned OKX 51001",
            )
            result["record"]["status"] = "api_51001_cached_symbol" if not local else "api_51001_partial_local"
            result["record"]["api_code"] = "51001"
            result["record"]["source"] = "symbol_status" if not local else result["record"]["source"]
            _save_result(event, fingerprint, result)
            output.append(result)
            continue
        with STATS_LOCK:
            STATS["api_events"] += 1
        api_result = _request_window(session, f"{symbol}-USDT-SWAP",
                                     touch_ms + POST_BARS * BAR_MS)
        if api_result.get("payload") and str(api_result["payload"].get("code")) == "51001":
            cached_51001_event = event["event_id"]
        result = _build_result(event, touch_ms, api_result, local, local_files, local_conflicts)
        if result["record"]["status"] == "api_51001":
            cached_51001_event = event["event_id"]
        _save_result(event, fingerprint, result)
        output.append(result)
    session.close()
    return output


def _write_gzip_csv(path: Path, fields: list[str], rows: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    count = 0
    with tmp.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: row.get(key, "") for key in fields})
                    count += 1
    os.replace(tmp, path)
    return count


def _export(events: list[dict[str, str]], source_fields: list[str], fingerprint: str) -> dict[str, int]:
    event_fields = source_fields + [field for field in EVENT_EXTRA_FIELDS if field not in source_fields]

    def event_rows():
        for event in events:
            result = _read_result_cache(event, fingerprint)
            if result:
                record = result["record"]
            else:
                record = {key: value for key, value in event.items() if key != "_source_fields"}
                record.update({"status": "not_processed", "requested_bars": WINDOW_BARS})
            yield record

    def candle_rows():
        for event in events:
            result = _read_result_cache(event, fingerprint)
            if not result:
                continue
            for row in result.get("candles", []):
                yield row

    n_events = _write_gzip_csv(OUTPUT / "events.csv.gz", event_fields, event_rows())
    n_candles = _write_gzip_csv(OUTPUT / "candles.csv.gz", CANDLE_FIELDS, candle_rows())
    return {"events_rows": n_events, "candle_rows": n_candles}


def _write_receipt(fingerprint: str, planned: int, completed: int, started: float,
                   final: bool, output_counts: dict[str, int] | None = None) -> None:
    receipt = {
        "experiment_id": "exp-alt-month-first-week-five-minute-observation-20261111-v2",
        "status": "complete" if final else "in_progress",
        "input": str(INPUT.relative_to(ROOT)), "input_sha256": fingerprint,
        "planned_events": planned, "processed_events": completed,
        "api_attempts": STATS["api_attempts"],
        "api_success_responses": STATS["api_success_responses"],
        "api_51001": STATS["api_51001"], "api_events": STATS["api_events"],
        "event_cache_hits": STATS["cache_hits"], "local_complete_events": STATS["local_complete"],
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "api_rate_guard": "18 requests per rolling 2 seconds, minimum 0.11 seconds between starts; 8 symbol workers",
        "source": {
            "provider": "OKX public API; existing native 5m local cache where complete",
            "endpoint": URL, "bar": "5m", "limit": REQUEST_LIMIT,
            "window": "[touch H1 open - 120*5m, touch H1 open + 120*5m)",
            "requested_bars": WINDOW_BARS,
            "raw_response_cache": str(CACHE_DIR.relative_to(ROOT)),
        },
        "outputs": output_counts or {},
    }
    path = OUTPUT / "receipt.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run(args: argparse.Namespace) -> None:
    events, fingerprint = read_events()
    source_fields = json.loads(events[0].pop("_source_fields"))
    for event in events[1:]:
        event.pop("_source_fields", None)
    if args.event_id:
        events = [event for event in events if event["event_id"] == args.event_id]
        if not events:
            raise ValueError(f"event_id not found: {args.event_id}")
    if args.symbol:
        events = [event for event in events if event["symbol"] == args.symbol]
    if args.max_events is not None:
        events = events[:args.max_events]
    if not events:
        raise ValueError("no events selected")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    groups: dict[str, list[dict[str, str]]] = {}
    for event in events:
        groups.setdefault(event["symbol"], []).append(event)
    for group in groups.values():
        group.sort(key=lambda event: (event.get("touch", ""), event["event_id"]))
    print(json.dumps({
        "planned_events": len(events), "symbols": len(groups),
        "input": str(INPUT.relative_to(ROOT)), "window_bars": WINDOW_BARS,
        "window_rule": "[touch H1 open - 120*5m, touch H1 open + 120*5m)",
        "rate_guard": "18 / rolling 2s, min interval 0.11s", "workers": args.workers,
    }, sort_keys=True), flush=True)

    completed = 0
    next_checkpoint = 100
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_process_symbol, symbol, symbol_events, fingerprint, LIMITER): symbol
            for symbol, symbol_events in groups.items()
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                rows = future.result()
            except Exception as error:
                print(json.dumps({"symbol_worker_error": symbol,
                                  "error": f"{type(error).__name__}: {error}"}), flush=True)
                continue
            completed += len(rows)
            if completed >= next_checkpoint or completed == len(events):
                _write_receipt(fingerprint, len(events), completed, started, False)
                print(json.dumps({
                    "progress_events": completed, "planned_events": len(events),
                    "api_attempts": STATS["api_attempts"], "api_events": STATS["api_events"],
                    "api_51001": STATS["api_51001"], "local_complete": STATS["local_complete"],
                    "cache_hits": STATS["cache_hits"], "elapsed_seconds": round(time.monotonic() - started, 1),
                }, sort_keys=True), flush=True)
                next_checkpoint = ((completed // 100) + 1) * 100

    counts = _export(events, source_fields, fingerprint)
    final_results = [_read_result_cache(event, fingerprint) for event in events]
    processed = sum(result is not None for result in final_results)
    statuses: dict[str, int] = {}
    for result in final_results:
        if result:
            status = str(result.get("record", {}).get("status", "unknown"))
            statuses[status] = statuses.get(status, 0) + 1
    _write_receipt(fingerprint, len(events), processed, started, processed == len(events), counts)
    print(json.dumps({
        "status": "complete" if processed == len(events) else "incomplete",
        "processed_events": processed, "planned_events": len(events),
        "statuses": statuses, "outputs": counts,
        "api_attempts": STATS["api_attempts"], "elapsed_seconds": round(time.monotonic() - started, 1),
        "output_dir": str(OUTPUT.relative_to(ROOT)),
    }, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--event-id")
    parser.add_argument("--symbol")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 16:
        raise ValueError("--workers must be between 1 and 16")
    if args.max_events is not None and args.max_events < 1:
        raise ValueError("--max-events must be positive")
    run(args)


if __name__ == "__main__":
    main()

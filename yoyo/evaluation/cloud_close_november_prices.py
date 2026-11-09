"""Continue the frozen Binance UM fixed-contract source with native 5m -> H1 OHLCV.

This is a market-data source builder only. It does not create signals, trades, or
returns. Local 5m observations are reused first; missing active-contract days
are fetched from Binance's public USD-M ``/fapi/v1/klines`` endpoint and cached
as raw responses. Every H1 row requires twelve exact, consecutive UTC 5m bars.
Missing candles are kept as gaps and never forward-filled.
"""
from __future__ import annotations

import argparse
import ast
import csv
import gzip
import json
import os
import time
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-cloud-close-november-prices-20261109-v1"
CONFIG_PATH = EXPERIMENT / "config.json"
ARCHIVE_EXP = ROOT / "experiments/active/exp-cloud-close-archive-complete-20261109-v1"
TRANSFER_WORK = ROOT / "experiments/active/exp-cloud-close-binance-transfer-20261109-v1/work"
ARCHIVE_COHORT = ROOT / "data/research/cloud_close_archive_cohort/symbols"
OUTPUT_ROOT = ROOT / "data/research/cloud_close_november_prices"
SERIES_ROOT = OUTPUT_ROOT / "series"
RAW_REST_ROOT = OUTPUT_ROOT / "raw_fapi"

# These bounds are explicit UTC bar-open boundaries. The final included H1 bar
# opens at 11:00Z and closes at the exclusive 12:00Z endpoint.
OVERLAP_START = pd.Timestamp("2026-09-22T00:00:00Z")
CONTINUATION_START = pd.Timestamp("2026-09-23T00:00:00Z")
PARITY_END = pd.Timestamp("2026-09-24T00:00:00Z")
END_EXCLUSIVE = pd.Timestamp("2026-11-08T12:00:00Z")
START_MS = int(OVERLAP_START.timestamp() * 1000)
CONTINUATION_START_MS = int(CONTINUATION_START.timestamp() * 1000)
END_EXCLUSIVE_MS = int(END_EXCLUSIVE.timestamp() * 1000)
PARITY_END_MS = int(PARITY_END.timestamp() * 1000)

BAR_MS = 5 * 60_000
HOUR_MS = 60 * 60_000
DAY_MS = 24 * HOUR_MS
ARCHIVE_15M_MS = 15 * 60_000
OHLCV = ["open", "high", "low", "close", "volume"]
PRICE_COLUMNS = ["open", "high", "low", "close"]
FAPI_URL = "https://fapi.binance.com/fapi/v1/klines"
FAPI_LIMIT = 1000
FAPI_REQUEST_WEIGHT = 2  # limit=288 falls in Binance's [100, 500) tier
HTTP_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 4
RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0)
USER_AGENT = "fable-trading-research/1.0"


class SourceError(RuntimeError):
    """A source cannot be treated as a validated market observation."""


class OfficialKlineUnavailable(SourceError):
    """The official endpoint confirms no usable current listing for a day."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ms(value: Any) -> int:
    if value is None or value == "":
        raise ValueError("missing millisecond timestamp")
    result = int(float(value))
    # Binance timestamps here are epoch milliseconds, not pandas nanoseconds.
    if result < 1_500_000_000_000 or result > 2_000_000_000_000:
        raise ValueError(f"timestamp is not a plausible 2020s epoch-ms value: {value!r}")
    return result


def _utc(ms: int) -> str:
    return pd.Timestamp(ms, unit="ms", tz="UTC").isoformat().replace("+00:00", "Z")


def _floor(value: int, unit: int) -> int:
    return (value // unit) * unit


def _ceil(value: int, unit: int) -> int:
    return ((value + unit - 1) // unit) * unit


def _delivery(value: Any) -> int | None:
    if value is None or str(value).strip() in {"", "OPEN_MAX", "4133404800000", "4133404800000.0"}:
        return None
    parsed = _ms(value)
    # Binance exchangeInfo's OPEN_MAX sentinel is not an actual delivery date.
    return None if parsed >= 4_000_000_000_000 else parsed


def _date_ms(day: str) -> int:
    return int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)


def _fixed_contract_periods() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load the frozen 632 + 29 + 19 symbol pool and split known relistings."""
    classification_path = ARCHIVE_EXP / "original_contract_classification.csv"
    supplemental_path = ARCHIVE_EXP / "supplemental_contract_evidence.csv"
    extra_audit_path = ARCHIVE_EXP / "contract_lifetime_evidence.csv.gz"
    if not all(p.is_file() for p in (classification_path, supplemental_path, extra_audit_path)):
        raise FileNotFoundError("Archive-complete frozen universe evidence is incomplete")

    periods: list[dict[str, Any]] = []
    members: dict[str, str] = {}
    with classification_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = row["symbol"].strip().upper()
            if not symbol or symbol in members:
                raise SourceError(f"duplicate/empty original symbol in frozen classification: {symbol!r}")
            members[symbol] = "original_632_exchangeInfo_snapshot_2026-08-28"
            old_onboard = _ms(row["archive_onboardDate_ms"])
            old_delivery = _delivery(row["archive_deliveryDate_ms"])
            current_onboard = _ms(row["current_onboardDate_ms"])
            current_delivery = _delivery(row["current_deliveryDate_ms"])
            if current_onboard == old_onboard:
                periods.append({
                    "symbol": symbol,
                    "contract_period_id": f"{symbol}__onboard_{old_onboard}",
                    "onboard_ms": old_onboard,
                    "delivery_ms": current_delivery,
                    "onboard_boundary_kind": "frozen_exchangeInfo_onboardDate",
                    "delivery_boundary_kind": "current_exchangeInfo_deliveryDate",
                    "period_source": "archive_snapshot_onboard_current_exchangeInfo_delivery",
                    "period_link_status": "same_onboard_date_same_contract_period",
                    "prior_period_id": "",
                    "same_symbol_relisting": False,
                    "membership_source": members[symbol],
                    "metadata_status": row["current_status"],
                })
            else:
                old_end = old_delivery
                # A changed official onboarding date denotes a distinct contract
                # lifecycle. Do not let an OPEN_MAX old record run through it.
                if old_end is None or old_end > current_onboard:
                    old_end = current_onboard
                old_id = f"{symbol}__onboard_{old_onboard}"
                new_id = f"{symbol}__onboard_{current_onboard}"
                periods.append({
                    "symbol": symbol,
                    "contract_period_id": old_id,
                    "onboard_ms": old_onboard,
                    "delivery_ms": old_end,
                    "onboard_boundary_kind": "frozen_exchangeInfo_onboardDate",
                    "delivery_boundary_kind": "archived_exchangeInfo_delivery_or_next_onboard_guard",
                    "period_source": "archive_snapshot_lifecycle_superseded_by_new_onboard",
                    "period_link_status": "closed_before_distinct_onboard_no_price_stitch",
                    "prior_period_id": "",
                    "same_symbol_relisting": False,
                    "membership_source": members[symbol],
                    "metadata_status": row["archive_status"],
                })
                periods.append({
                    "symbol": symbol,
                    "contract_period_id": new_id,
                    "onboard_ms": current_onboard,
                    "delivery_ms": current_delivery,
                    "onboard_boundary_kind": "current_exchangeInfo_onboardDate_distinct_period",
                    "delivery_boundary_kind": "current_exchangeInfo_deliveryDate",
                    "period_source": "current_exchangeInfo_distinct_relisted_contract",
                    "period_link_status": "same_symbol_relisting_separate_period_no_continuity_claim",
                    "prior_period_id": old_id,
                    "same_symbol_relisting": True,
                    "membership_source": members[symbol],
                    "metadata_status": row["current_status"],
                })

    with supplemental_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = row["symbol"].strip().upper()
            if not symbol or symbol in members:
                raise SourceError(f"supplemental symbol overlaps fixed pool or is empty: {symbol!r}")
            members[symbol] = "archive-complete_supplemental_19_exchangeInfo_evidence"
            onboard = int(pd.Timestamp(row["onboardDate_utc"]).timestamp() * 1000)
            delivery_text = row.get("deliveryDate_utc", "")
            delivery = None if delivery_text in {"", "OPEN_MAX"} else int(pd.Timestamp(delivery_text).timestamp() * 1000)
            periods.append({
                "symbol": symbol,
                "contract_period_id": f"{symbol}__onboard_{onboard}",
                "onboard_ms": onboard,
                "delivery_ms": delivery,
                "onboard_boundary_kind": "frozen_supplemental_exchangeInfo_onboardDate",
                "delivery_boundary_kind": "frozen_supplemental_exchangeInfo_deliveryDate",
                "period_source": "frozen_supplemental_exchangeInfo_and_archive_evidence",
                "period_link_status": "single_frozen_contract_period",
                "prior_period_id": "",
                "same_symbol_relisting": False,
                "membership_source": members[symbol],
                "metadata_status": row.get("status_asof_2026-11-06", "unknown"),
            })

    # The 29 archive-only additions are intentionally retained in coverage.
    # They are not blindly rebound to a current same-name contract: their
    # registered archive end predates this extension and no matching lifecycle
    # evidence exists in the frozen exchange snapshots.
    with gzip.open(extra_audit_path, "rt", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("row_type") != "extra29_archive_audit":
                continue
            symbol = row["symbol"].strip().upper()
            if not symbol or symbol in members:
                raise SourceError(f"extra archive symbol overlaps fixed pool or is empty: {symbol!r}")
            members[symbol] = "archive-complete_extra_29_historical_contract_archives"
            source_path = ARCHIVE_COHORT / symbol / "source.json"
            if not source_path.is_file():
                raise FileNotFoundError(f"missing frozen archive source metadata: {source_path}")
            source = json.loads(source_path.read_text(encoding="utf-8"))
            first = int(pd.Timestamp(source["first"]).timestamp() * 1000)
            last_open = int(pd.Timestamp(source["last"]).timestamp() * 1000)
            periods.append({
                "symbol": symbol,
                "contract_period_id": f"{symbol}__archive_period_{first}",
                "onboard_ms": first,
                "delivery_ms": last_open + ARCHIVE_15M_MS,
                "onboard_boundary_kind": "first_observed_15m_archive_not_official_onboard",
                "delivery_boundary_kind": "last_observed_15m_archive_plus_15m_not_official_delivery",
                "period_source": "registered_historical_archive_observation_only",
                "period_link_status": "historical_archive_period_not_joined_to_current_ticker",
                "prior_period_id": "",
                "same_symbol_relisting": False,
                "membership_source": members[symbol],
                "metadata_status": "not_present_in_frozen_exchangeInfo_snapshots",
            })

    if len(members) != 680 or len(periods) < 680:
        raise SourceError(f"fixed pool contract changed: symbols={len(members)}, periods={len(periods)}, expected 680 symbols")
    symbols_by_source = Counter(members.values())
    return sorted(periods, key=lambda r: (r["symbol"], r["onboard_ms"])), {
        "fixed_symbol_count": len(members),
        "fixed_symbol_counts_by_source": dict(symbols_by_source),
        "lifecycle_period_count": len(periods),
        "relisted_symbols": sorted({r["symbol"] for r in periods if r["same_symbol_relisting"]}),
        "fixed_symbols": sorted(members),
    }


def _period_active_window(period: dict[str, Any]) -> tuple[int, int] | None:
    start = max(START_MS, _ceil(int(period["onboard_ms"]), BAR_MS))
    end = min(END_EXCLUSIVE_MS, int(period["delivery_ms"]) if period["delivery_ms"] is not None else END_EXCLUSIVE_MS)
    if end <= start:
        return None
    return start, end


def _expected_5m(period: dict[str, Any], day_start_ms: int) -> np.ndarray:
    active = _period_active_window(period)
    if active is None:
        return np.empty(0, dtype=np.int64)
    left = max(active[0], day_start_ms)
    right = min(active[1], day_start_ms + DAY_MS)
    left = _ceil(left, BAR_MS)
    right = min(right, END_EXCLUSIVE_MS)
    if right <= left:
        return np.empty(0, dtype=np.int64)
    return np.arange(left, right, BAR_MS, dtype=np.int64)


def _candidate_local_sources(symbol: str) -> list[tuple[str, Path, int]]:
    """Use registered, OHLCV-complete files only; O/H/C-only caches are excluded."""
    candidates: list[tuple[str, Path, int]] = []
    roots = [
        ("forward_20260922", ROOT / "data/crypto/market/kline_binance_um5m_forward_20260922/series" / f"{symbol}.csv", 20),
        ("recent_20260923", ROOT / "data/crypto/market/research-spike_v128_recent_20260923/series" / f"{symbol}.csv.gz", 30),
        ("bulk_20260923_20261031", ROOT / "data/crypto/market/binance_um5m_20260923_20261031/series" / f"{symbol}.csv.gz", 40),
        ("october_registered_new_bars", ROOT / "experiments/active/exp-monday-limit-live-october-20261101-v1/symbols" / symbol / "new_bars.csv.gz", 35),
        ("november_targeted_through_20261104", ROOT / "data/crypto/market/breadth_rebound_targets_20261107" / f"{symbol}-november-through-04.csv.gz", 50),
    ]
    for source, path, priority in roots:
        if path.is_file():
            candidates.append((source, path, priority))
    rest_dir = ROOT / "data/crypto/market/binance_um5m_rest_fallback/series" / symbol
    if rest_dir.is_dir():
        for path in sorted(rest_dir.glob("*.csv")):
            candidates.append(("rest_fallback_cache", path, 60))
    return candidates


def _read_local_file(path: Path, source: str, start_ms: int, end_ms: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read only the requested UTC interval from a registered local 5m file."""
    try:
        # Header first so incomplete O/H/C-only fallback caches are visible and
        # excluded instead of being padded with guessed low/volume values.
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", newline="", encoding="utf-8-sig") as handle:
            header = next(csv.reader(handle), [])
        required = {"ts", *OHLCV}
        if not required.issubset(set(header)):
            return pd.DataFrame(columns=["ts", *OHLCV, "source", "priority"]), {
                "path": str(path), "source": source, "status": "excluded_incomplete_columns",
                "columns": header, "rows": 0,
            }
        reader = pd.read_csv(
            path, compression="infer", usecols=["ts", *OHLCV],
            dtype={"ts": "int64", **{name: "float64" for name in OHLCV}}, chunksize=100_000,
        )
        kept: list[pd.DataFrame] = []
        raw_rows = 0
        with reader:
            for chunk in reader:
                if chunk.empty:
                    continue
                chunk = chunk.sort_values("ts", kind="stable")
                first, last = int(chunk["ts"].iloc[0]), int(chunk["ts"].iloc[-1])
                if last < start_ms:
                    continue
                if first >= end_ms:
                    break
                part = chunk.loc[(chunk.ts >= start_ms) & (chunk.ts < end_ms)].copy()
                raw_rows += len(part)
                if len(part):
                    part["source"] = source
                    kept.append(part)
        frame = pd.concat(kept, ignore_index=True) if kept else pd.DataFrame(columns=["ts", *OHLCV, "source"])
        if len(frame):
            times = frame.ts.to_numpy(dtype=np.int64)
            if np.any(times % BAR_MS):
                raise SourceError(f"{path}: 5m opens are not UTC-grid aligned")
            values = frame[OHLCV].to_numpy(dtype=np.float64)
            if not np.isfinite(values).all() or (frame[PRICE_COLUMNS] <= 0).any().any() or (frame.volume < 0).any():
                raise SourceError(f"{path}: invalid/nonfinite OHLCV")
            if (frame.high < frame[["open", "low", "close"]].max(axis=1)).any() or (frame.low > frame[["open", "high", "close"]].min(axis=1)).any():
                raise SourceError(f"{path}: invalid OHLC range")
        return frame, {
            "path": str(path), "source": source, "status": "read" if len(frame) else "no_rows_in_requested_range",
            "rows": int(len(frame)), "raw_rows_in_requested_range": int(raw_rows),
            "first_ms": int(frame.ts.min()) if len(frame) else None,
            "last_ms": int(frame.ts.max()) if len(frame) else None,
        }
    except (OSError, ValueError, KeyError, pd.errors.ParserError, EOFError) as exc:
        if isinstance(exc, SourceError):
            raise
        return pd.DataFrame(columns=["ts", *OHLCV, "source", "priority"]), {
            "path": str(path), "source": source, "status": "read_error",
            "error": f"{type(exc).__name__}: {exc}"[:500], "rows": 0,
        }


def _local_5m(symbol: str, audit: list[dict[str, Any]]) -> pd.DataFrame:
    frames = []
    source_specs = _candidate_local_sources(symbol)
    for source, path, priority in source_specs:
        frame, receipt = _read_local_file(path, source, START_MS, END_EXCLUSIVE_MS)
        receipt["priority"] = priority
        audit.append(receipt)
        if len(frame):
            frame["priority"] = priority
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["ts", *OHLCV, "source", "priority"])
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["ts", "priority", "source"], kind="stable")
    conflicts = []
    duplicates = 0
    conflict_examples: list[dict[str, Any]] = []
    for ts, group in combined.groupby("ts", sort=False):
        if len(group) > 1:
            duplicates += int(len(group) - 1)
            values = group[OHLCV].to_numpy(dtype=np.float64)
            if not np.allclose(values, values[0], rtol=1e-12, atol=1e-12):
                conflicts.append(int(ts))
                if len(conflict_examples) < 8:
                    conflict_examples.append({
                        "open_time_ms": int(ts), "open_time_utc": _utc(int(ts)),
                        "sources": [str(x) for x in group.source],
                        "values_ohlcv": group[OHLCV].to_numpy(dtype=np.float64).tolist(),
                    })
    # Higher-priority local archive wins. Conflicts are persisted to the audit;
    # they are never hidden by silently dropping the duplicate lineage.
    deduped = combined.drop_duplicates("ts", keep="last").sort_values("ts", kind="stable").reset_index(drop=True)
    deduped.attrs["local_conflict_times_ms"] = conflicts
    audit.append({"symbol": symbol, "source": "local_merge", "status": "merged",
                  "rows_before_deduplication": int(len(combined)), "duplicate_rows_removed": duplicates,
                  "conflicting_timestamp_count": len(conflicts), "conflict_examples": conflict_examples,
                  "winner_rule": "highest registered local source priority; original conflicting source values retained here"})
    return deduped


_last_request_start = 0.0


def _http_json(url: str, min_interval_seconds: float, attempt_budget: int = MAX_ATTEMPTS) -> tuple[bytes, int]:
    global _last_request_start
    errors: list[str] = []
    tls_failed = False
    allowed_attempts = min(MAX_ATTEMPTS, int(attempt_budget))
    if allowed_attempts < 1:
        raise SourceError("FAPI request budget is exhausted before starting a request")
    for attempt in range(allowed_attempts):
        delay = min_interval_seconds - (time.monotonic() - _last_request_start)
        if delay > 0:
            time.sleep(delay)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        _last_request_start = time.monotonic()
        try:
            if tls_failed:
                # The host's system TLS client can recover after Python's older
                # SSL transport closes early. This consumes one existing retry.
                run = subprocess.run(['curl','--silent','--show-error','--max-time',str(HTTP_TIMEOUT_SECONDS),
                                      '--user-agent',USER_AGENT,'--write-out','\n%{http_code}',url],
                                     capture_output=True,check=False)
                if run.returncode:
                    raise OSError(run.stderr.decode(errors='replace').strip())
                payload,code = run.stdout.rsplit(b'\n',1)
                status = int(code)
                if status==200:
                    return payload,attempt+1
                raise urllib.error.HTTPError(url,status,'Public kline transport fallback',{},None)
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                payload = response.read()
                return payload, attempt + 1
        except urllib.error.HTTPError as exc:
            errors.append(f"HTTP {exc.code}: {exc.reason}")
            # Invalid/delisted symbols are recorded as a source gap; transient
            # rate-limit and server responses are retried.
            if exc.code not in (408, 425, 429) and exc.code < 500:
                raise OfficialKlineUnavailable(f"Binance public kline request failed: {errors[-1]} {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if "ssl" in str(exc).lower() or "tls" in str(exc).lower() or "eof" in str(exc).lower():
                tls_failed = True
        if attempt + 1 < allowed_attempts:
            time.sleep(RETRY_DELAYS_SECONDS[min(attempt, len(RETRY_DELAYS_SECONDS) - 1)])
    raise SourceError(f"Binance public kline request exhausted retries: {'; '.join(errors)} {url}")


def _parse_fapi_payload(payload: bytes, symbol: str, day_start_ms: int) -> pd.DataFrame:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SourceError(f"{symbol} {_utc(day_start_ms)}: invalid JSON response") from exc
    if isinstance(parsed, dict):
        raise SourceError(f"{symbol} {_utc(day_start_ms)}: API error payload {parsed}")
    if not isinstance(parsed, list):
        raise SourceError(f"{symbol} {_utc(day_start_ms)}: response is not an array")
    rows = []
    for record in parsed:
        if not isinstance(record, list) or len(record) < 6:
            raise SourceError(f"{symbol} {_utc(day_start_ms)}: malformed kline record")
        rows.append((_ms(record[0]), *(float(x) for x in record[1:6])))
    frame = pd.DataFrame(rows, columns=["ts", *OHLCV])
    if not frame.empty:
        if frame.ts.duplicated().any():
            raise SourceError(f"{symbol} {_utc(day_start_ms)}: API response contains duplicate 5m opens")
        frame = frame.sort_values("ts", kind="stable")
        times = frame.ts.to_numpy(dtype=np.int64)
        if len(times) != len(np.unique(times)) or np.any(times % BAR_MS) or np.any(times < day_start_ms) or np.any(times >= day_start_ms + DAY_MS):
            raise SourceError(f"{symbol} {_utc(day_start_ms)}: response clocks are duplicate, off-grid, or outside requested day")
        values = frame[OHLCV].to_numpy(dtype=np.float64)
        if not np.isfinite(values).all() or (frame[PRICE_COLUMNS] <= 0).any().any() or (frame.volume < 0).any():
            raise SourceError(f"{symbol} {_utc(day_start_ms)}: invalid OHLCV values")
        if (frame.high < frame[["open", "low", "close"]].max(axis=1)).any() or (frame.low > frame[["open", "high", "close"]].min(axis=1)).any():
            raise SourceError(f"{symbol} {_utc(day_start_ms)}: invalid OHLC range")
        frame["source"] = "binance_fapi_klines_5m"
        frame["priority"] = 100
    else:
        frame = pd.DataFrame(columns=["ts", *OHLCV, "source", "priority"])
    return frame.reset_index(drop=True)


def _fetch_fapi_day(symbol: str, day_start_ms: int, *, cache: bool, min_interval_seconds: float,
                    attempt_budget: int = MAX_ATTEMPTS) -> tuple[pd.DataFrame, dict[str, Any]]:
    date = pd.Timestamp(day_start_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
    cache_path = RAW_REST_ROOT / symbol / f"{date}.json.gz"
    params = urllib.parse.urlencode({
        "symbol": symbol, "interval": "5m", "startTime": day_start_ms,
        "endTime": day_start_ms + DAY_MS - 1, "limit": FAPI_LIMIT,
    })
    url = f"{FAPI_URL}?{params}"
    if cache and cache_path.is_file():
        payload = gzip.decompress(cache_path.read_bytes())
        frame = _parse_fapi_payload(payload, symbol, day_start_ms)
        return frame, {
            "date": date, "symbol": symbol, "status": "validated_raw_cache",
            "url": url, "raw_path": str(cache_path), "raw_bytes": cache_path.stat().st_size,
            "requests": 0, "request_weight": 0,
        }
    payload, attempts = _http_json(url, min_interval_seconds, attempt_budget)
    frame = _parse_fapi_payload(payload, symbol, day_start_ms)
    raw_path = ""
    if cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.part")
        temp.write_bytes(gzip.compress(payload, mtime=0))
        os.replace(temp, cache_path)
        raw_path = str(cache_path)
    return frame, {
        "date": date, "symbol": symbol, "status": "downloaded" if len(frame) else "empty_response",
        "url": url, "raw_path": raw_path, "raw_bytes": len(payload),
        "requests": attempts, "request_weight": attempts * FAPI_REQUEST_WEIGHT,
    }


def _combine_day(local: pd.DataFrame, fetched: pd.DataFrame, expected: np.ndarray) -> tuple[pd.DataFrame, dict[str, Any]]:
    pieces = []
    if len(local):
        pieces.append(local.loc[local.ts.isin(expected)])
    if len(fetched):
        pieces.append(fetched.loc[fetched.ts.isin(expected)])
    duplicate_count = 0
    conflicting_count = 0
    conflict_examples: list[dict[str, Any]] = []
    if not pieces:
        combined = pd.DataFrame(columns=["ts", *OHLCV, "source", "priority"])
    else:
        combined = pd.concat(pieces, ignore_index=True)
        combined = combined.sort_values(["ts", "priority", "source"], kind="stable")
        for ts, group in combined.groupby("ts", sort=False):
            if len(group) > 1:
                duplicate_count += int(len(group) - 1)
                values = group[OHLCV].to_numpy(dtype=np.float64)
                if not np.allclose(values, values[0], rtol=1e-12, atol=1e-12):
                    conflicting_count += 1
                    if len(conflict_examples) < 4:
                        conflict_examples.append({"open_time_ms": int(ts), "open_time_utc": _utc(int(ts)),
                                                  "sources": [str(x) for x in group.source],
                                                  "values_ohlcv": values.tolist()})
        combined = combined.drop_duplicates("ts", keep="last")
        combined = combined.sort_values("ts", kind="stable").reset_index(drop=True)
    observed = combined.ts.to_numpy(dtype=np.int64) if len(combined) else np.empty(0, dtype=np.int64)
    missing = np.setdiff1d(expected, observed, assume_unique=True)
    extras = np.setdiff1d(observed, expected, assume_unique=True)
    return combined, {"expected_5m": int(len(expected)), "observed_5m": int(len(observed)),
                      "missing_5m": int(len(missing)), "extra_5m_after_period_filter": int(len(extras)),
                      "missing_sample_utc": [_utc(int(x)) for x in missing[:4]],
                      "duplicate_rows_resolved": duplicate_count,
                      "conflicting_timestamps_resolved": conflicting_count,
                      "conflict_examples": conflict_examples,
                      "winner_rule": "highest source priority (FAPI first for matching timestamps)"}


def _aggregate_h1(frame: pd.DataFrame, period: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    if frame.empty:
        return pd.DataFrame(columns=["open_time_ms", "open_time_utc", "close_time_ms", "close_time_utc", *OHLCV,
                                     "close_volume_sum_5m", "n_5m", "source_mix"]), {
            "expected_h1": 0, "complete_h1": 0, "incomplete_h1": 0, "zero_volume_5m": 0,
        }
    active = _period_active_window(period)
    assert active is not None
    first_hour = max(_ceil(active[0], HOUR_MS), START_MS)
    # Last bar is allowed only when the full H1 candle lies before delivery/end.
    end = min(active[1], END_EXCLUSIVE_MS)
    last_hour_exclusive = _floor(end, HOUR_MS)
    expected_h1_times = np.arange(first_hour, last_hour_exclusive, HOUR_MS, dtype=np.int64)
    by_ts = frame.sort_values("ts", kind="stable").set_index("ts", drop=False)
    rows: list[dict[str, Any]] = []
    incomplete = 0
    missing_examples: list[str] = []
    for hour_ms in expected_h1_times:
        expected = np.arange(hour_ms, hour_ms + HOUR_MS, BAR_MS, dtype=np.int64)
        group = by_ts.loc[by_ts.index.intersection(expected)]
        times = group.ts.to_numpy(dtype=np.int64)
        if len(times) != 12 or not np.array_equal(times, expected):
            incomplete += 1
            if len(missing_examples) < 8:
                missing = np.setdiff1d(expected, times)
                missing_examples.append(_utc(int(missing[0] if len(missing) else hour_ms)))
            continue
        prices = group[OHLCV].to_numpy(dtype=np.float64)
        row = {
            "symbol": period["symbol"],
            "contract_period_id": period["contract_period_id"],
            "open_time_ms": int(hour_ms),
            "open_time_utc": _utc(int(hour_ms)),
            "close_time_ms": int(hour_ms + HOUR_MS - 1),
            "close_time_utc": _utc(int(hour_ms + HOUR_MS - 1)),
            "open": float(prices[0, 0]),
            "high": float(np.max(prices[:, 1])),
            "low": float(np.min(prices[:, 2])),
            "close": float(prices[-1, 3]),
            "volume": float(np.sum(prices[:, 4], dtype=np.float64)),
            # Frozen scoring expects this seven-day turnover proxy. It is
            # deliberately named as a proxy, not Binance quote-asset volume.
            "close_volume_sum_5m": float(np.sum(prices[:, 3] * prices[:, 4], dtype=np.float64)),
            "n_5m": 12,
            "source_mix": "+".join(sorted(set(str(x) for x in group.source))),
        }
        if row["high"] < max(row[c] for c in ("open", "low", "close")) or row["low"] > min(row[c] for c in ("open", "high", "close")) or row["volume"] < 0:
            raise SourceError(f"invalid H1 aggregate for {period['contract_period_id']} {_utc(int(hour_ms))}")
        rows.append(row)
    out = pd.DataFrame(rows)
    return out, {
        "expected_h1": int(len(expected_h1_times)), "complete_h1": int(len(out)),
        "incomplete_h1": int(incomplete), "incomplete_hour_samples_utc": missing_examples,
        "zero_volume_5m": int((frame.volume == 0).sum()),
    }


def _quote_parity(period: dict[str, Any], h1: pd.DataFrame) -> dict[str, Any]:
    path = TRANSFER_WORK / period["symbol"] / "quotes.npz"
    if not path.is_file() or h1.empty:
        return {"parity_source": str(path), "parity_status": "no_frozen_quote_or_no_h1", "checked_bars": 0, "mismatched_bars": 0}
    with np.load(path, allow_pickle=False) as quotes:
        clock = np.asarray(quotes["clock"], dtype=np.int64)
        prices = np.asarray(quotes["prices"], dtype=np.float64)
    if clock.ndim != 1 or prices.shape != (len(clock), 4) or np.any(np.diff(clock) <= 0):
        raise SourceError(f"invalid frozen quote file {path}")
    if np.any(clock < 1_500_000_000_000) or np.any(clock > 2_000_000_000_000):
        raise SourceError(f"frozen quote clock is not epoch-ms: {path}")
    bars = h1.loc[(h1.open_time_ms >= START_MS) & (h1.open_time_ms < PARITY_END_MS)]
    if bars.empty:
        return {"parity_source": str(path), "parity_status": "no_overlap_h1", "checked_bars": 0, "mismatched_bars": 0}
    old = {int(t): p for t, p in zip(clock, prices)}
    checked = 0
    mismatches: list[dict[str, Any]] = []
    for row in bars.itertuples(index=False):
        previous = old.get(int(row.open_time_ms))
        if previous is None:
            continue
        current = np.asarray([row.open, row.high, row.low, row.close], dtype=np.float64)
        checked += 1
        if not np.allclose(current, previous, rtol=1e-12, atol=1e-12):
            if len(mismatches) < 8:
                mismatches.append({"open_time_utc": row.open_time_utc, "new_ohlc": current.tolist(), "frozen_ohlc": previous.tolist()})
    return {"parity_source": str(path), "parity_status": "checked" if checked else "no_common_h1_open",
            "checked_bars": checked, "mismatched_bars": len(mismatches), "mismatch_examples": mismatches}


def _write_csv_gz_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.part")
    frame.to_csv(temp, index=False, compression="gzip", float_format="%.15g")
    os.replace(temp, path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.part")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _local_nonfixed_symbols(fixed_symbols: set[str]) -> dict[str, Any]:
    discovered: set[str] = set()
    bulk = ROOT / "data/crypto/market/binance_um5m_20260923_20261031/series"
    recent = ROOT / "data/crypto/market/research-spike_v128_recent_20260923/series"
    forward = ROOT / "data/crypto/market/kline_binance_um5m_forward_20260922/series"
    for root in (bulk, recent, forward):
        if root.is_dir():
            for path in root.iterdir():
                name = path.name.split(".", 1)[0]
                if name.endswith("USDT"):
                    discovered.add(name)
    target = ROOT / "data/crypto/market/breadth_rebound_targets_20261107"
    if target.is_dir():
        for path in target.glob("*-november-through-04.csv.gz"):
            discovered.add(path.name.removesuffix("-november-through-04.csv.gz"))
    outside = sorted(discovered - fixed_symbols)
    return {"local_source_symbol_count": len(discovered), "fixed_pool_outside_local_source_count": len(fixed_symbols - discovered),
            "locally_observed_nonfixed_symbol_count": len(outside), "nonfixed_symbols": outside}


def _build_period(period: dict[str, Any], *, config: dict[str, Any], local: pd.DataFrame,
                  source_audit: list[dict[str, Any]], request_audit: list[dict[str, Any]],
                  request_state: dict[str, Any], fetch: bool) -> dict[str, Any]:
    active = _period_active_window(period)
    base = dict(period)
    base.update({
        "period_start_utc": _utc(int(period["onboard_ms"])),
        "period_end_exclusive_utc": _utc(int(period["delivery_ms"])) if period["delivery_ms"] is not None else "OPEN_MAX",
        "collection_start_utc": _utc(active[0]) if active else None,
        "collection_end_exclusive_utc": _utc(active[1]) if active else None,
        "status": "inactive_outside_requested_window",
    })
    if active is None:
        base.update({"expected_5m": 0, "observed_5m": 0, "missing_5m": 0, "complete_h1": 0, "incomplete_h1": 0,
                     "series_path": "", "parity_status": "not_applicable",
                     "overlap_parity_status": "not_applicable", "overlap_parity_checked_bars": 0,
                     "overlap_parity_mismatched_bars": 0, "overlap_parity_examples": []})
        return base

    lo, hi = active
    period_local = local.loc[(local.ts >= lo) & (local.ts < hi)].copy() if not local.empty else local.copy()
    day_starts = range(_floor(lo, DAY_MS), _ceil(hi, DAY_MS), DAY_MS)
    fetched_frames = []
    day_audit: list[dict[str, Any]] = []
    missing_expected_total = 0
    expected_total = 0
    for day_start in day_starts:
        expected = _expected_5m(period, day_start)
        if not len(expected):
            continue
        expected_total += len(expected)
        local_day = period_local.loc[period_local.ts.isin(expected)] if not period_local.empty else period_local
        local_times = local_day.ts.to_numpy(dtype=np.int64) if not local_day.empty else np.empty(0, dtype=np.int64)
        need = not np.array_equal(local_times, expected)
        fetched = pd.DataFrame(columns=["ts", *OHLCV, "source", "priority"])
        fetch_receipt = None
        if need and fetch:
            if request_state["requests"] >= int(config["request_policy"]["max_requests"]):
                raise SourceError(f"request budget exhausted at {request_state['requests']} calls")
            try:
                fetched, fetch_receipt = _fetch_fapi_day(
                    period["symbol"], day_start, cache=bool(config["request_policy"]["cache_raw_responses"]),
                    min_interval_seconds=float(config["request_policy"]["minimum_seconds_between_requests"]),
                    attempt_budget=int(config["request_policy"]["max_requests"]) - request_state["requests"],
                )
            except OfficialKlineUnavailable as exc:
                fetched = pd.DataFrame(columns=["ts", *OHLCV, "source", "priority"])
                fetch_receipt = {"date": pd.Timestamp(day_start, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
                                 "symbol": period["symbol"], "status": "official_endpoint_unavailable",
                                 "url": "", "raw_path": "", "requests": 1,
                                 "request_weight": FAPI_REQUEST_WEIGHT, "error": str(exc)[:500]}
            request_state["requests"] += int(fetch_receipt["requests"])
            request_state["request_weight"] += int(fetch_receipt["request_weight"])
            request_audit.append(fetch_receipt)
            if len(fetched):
                fetched_frames.append(fetched)
        combined_day, day_summary = _combine_day(local_day, fetched, expected)
        missing_expected_total += day_summary["missing_5m"]
        day_audit.append({"day": pd.Timestamp(day_start, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
                          **day_summary, "fetched": bool(fetch_receipt),
                          "fetch_status": fetch_receipt["status"] if fetch_receipt else ("not_run" if need else "not_needed"),
                          "observed_source_mix": sorted(set(str(x) for x in combined_day.source)) if len(combined_day) else [],
                          "source_presence_for_gap": sorted(set(str(x) for x in local_day.source)) if len(local_day) else []})
        if len(combined_day):
            fetched_frames.append(combined_day)

    if fetched_frames:
        all_bars = pd.concat([period_local, *fetched_frames], ignore_index=True)
        all_bars = all_bars.sort_values(["ts", "priority", "source"], kind="stable").drop_duplicates("ts", keep="last")
        all_bars = all_bars.loc[(all_bars.ts >= lo) & (all_bars.ts < hi)].sort_values("ts", kind="stable").reset_index(drop=True)
    else:
        all_bars = period_local.sort_values("ts", kind="stable").reset_index(drop=True)

    expected_5m_all = np.concatenate([_expected_5m(period, day) for day in day_starts]) if expected_total else np.empty(0, dtype=np.int64)
    observed_times = all_bars.ts.to_numpy(dtype=np.int64) if len(all_bars) else np.empty(0, dtype=np.int64)
    missing_times = np.setdiff1d(expected_5m_all, observed_times, assume_unique=True)
    h1, h1_audit = _aggregate_h1(all_bars, period)
    parity = _quote_parity(period, h1)
    if len(h1) and parity["mismatched_bars"]:
        status = "overlap_parity_mismatch"
    elif len(missing_times):
        status = "active_with_explicit_missing_5m_or_h1_gaps"
    elif not len(h1):
        status = "active_window_no_complete_h1"
    else:
        status = "complete_source_window"

    period_key = str(period["contract_period_id"]).replace("/", "_")
    output_path = SERIES_ROOT / f"{period_key}.csv.gz"
    if fetch:
        if len(h1):
            _write_csv_gz_atomic(output_path, h1)
        elif output_path.exists():
            output_path.unlink()
    local_conflicts = local.attrs.get("local_conflict_times_ms", [])
    base.update({
        "status": status,
        "expected_5m": int(expected_total),
        "observed_5m": int(len(all_bars)),
        "missing_5m": int(len(missing_times)),
        "missing_5m_samples_utc": [_utc(int(x)) for x in missing_times[:8]],
        "zero_volume_5m": int((all_bars.volume == 0).sum()) if len(all_bars) else 0,
        "local_source_conflict_timestamps_in_symbol_window": int(sum(lo <= x < hi for x in local_conflicts)),
        "complete_h1": int(len(h1)),
        "incomplete_h1": int(h1_audit["incomplete_h1"]),
        "incomplete_h1_samples_utc": h1_audit["incomplete_hour_samples_utc"],
        "first_h1_open_utc": str(h1.open_time_utc.iloc[0]) if len(h1) else None,
        "last_h1_open_utc": str(h1.open_time_utc.iloc[-1]) if len(h1) else None,
        "overlap_parity_status": parity["parity_status"],
        "overlap_parity_checked_bars": int(parity["checked_bars"]),
        "overlap_parity_mismatched_bars": int(parity["mismatched_bars"]),
        "overlap_parity_examples": parity.get("mismatch_examples", []),
        "source_path": str(output_path) if len(h1) else "",
        "day_audit": day_audit,
    })
    return base


def collect(*, fetch: bool, symbols: set[str] | None = None) -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    periods, universe = _fixed_contract_periods()
    fixed_symbols = set(universe["fixed_symbols"])
    if symbols is not None:
        unknown = symbols - fixed_symbols
        if unknown:
            raise ValueError(f"smoke symbols outside frozen pool: {sorted(unknown)}")
        periods = [p for p in periods if p["symbol"] in symbols]

    output_rows: list[dict[str, Any]] = []
    source_audit: list[dict[str, Any]] = []
    request_audit: list[dict[str, Any]] = []
    request_state = {"requests": 0, "request_weight": 0}
    for symbol in sorted({p["symbol"] for p in periods}):
        local = _local_5m(symbol, source_audit)
        for period in (p for p in periods if p["symbol"] == symbol):
            output_rows.append(_build_period(period, config=config, local=local, source_audit=source_audit,
                                             request_audit=request_audit, request_state=request_state, fetch=fetch))

    if fetch and symbols is None:
        active_rows = [r for r in output_rows if r["collection_start_utc"]]
        mismatches = sum(int(r.get("overlap_parity_mismatched_bars", 0)) for r in output_rows)
        fixed_symbols = {r["symbol"] for r in output_rows}
        nonfixed = _local_nonfixed_symbols(fixed_symbols)
        coverage = pd.DataFrame(output_rows)
        _write_csv_gz_atomic(OUTPUT_ROOT / "coverage.csv.gz", coverage)
        # Detailed path/source receipts stay in data; the experiment receipt is
        # intentionally compact and references them by exact relative paths.
        _write_json_atomic(OUTPUT_ROOT / "source_audit.json", {
            "generated_at_utc": _utc_now(),
            "local_source_rows": source_audit,
            "fapi_day_requests": request_audit,
            "source_policy": config.get("source_policy", config.get("sources", {})),
        })
        manifest = {
            "experiment_id": config["id"], "generated_at_utc": _utc_now(),
            "source_scope": "data source only; no signal generation, strategy run, or PnL calculation",
            "requested_range": config["range"],
            "universe": {k: v for k, v in universe.items() if k != "fixed_symbols"},
            "period_rows": len(output_rows), "active_period_rows": len(active_rows),
            "period_status_counts": dict(Counter(r["status"] for r in output_rows)),
            "fixed_symbols_with_any_h1": len({r["symbol"] for r in output_rows if r["complete_h1"]}),
            "h1_rows": int(sum(r["complete_h1"] for r in output_rows)),
            "h1_gap_periods": sum(int(r["incomplete_h1"] > 0) for r in output_rows),
            "overlap_parity_checked_bars": int(sum(r["overlap_parity_checked_bars"] for r in output_rows)),
            "overlap_parity_mismatched_bars": int(mismatches),
            "request_count": request_state["requests"], "request_weight": request_state["request_weight"],
            "coverage_path": str(OUTPUT_ROOT / "coverage.csv.gz"),
            "source_audit_path": str(OUTPUT_ROOT / "source_audit.json"),
            "series_dir": str(SERIES_ROOT), "raw_rest_cache_dir": str(RAW_REST_ROOT),
            "nonfixed_local_source_summary": nonfixed,
        }
        _write_json_atomic(OUTPUT_ROOT / "manifest.json", manifest)
        EXPERIMENT.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(EXPERIMENT / "manifest.json", {
            "experiment_id": config["id"], "data_manifest": str(OUTPUT_ROOT / "manifest.json"),
            "coverage": str(OUTPUT_ROOT / "coverage.csv.gz"), "generated_at_utc": manifest["generated_at_utc"],
            "fixed_symbols": universe["fixed_symbol_count"], "lifecycle_periods": len(output_rows),
            "source_only": True,
        })
        return manifest
    return {"universe": universe, "coverage": output_rows, "source_audit": source_audit,
            "requests": request_audit, "request_state": request_state}


def finalize_existing() -> dict[str, Any]:
    """Rebuild global receipts from a complete coverage checkpoint and cached inputs.

    This path is intentionally network-free. It is for recovery after all period
    rows/series were written but the final manifest transaction failed.
    """
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    periods, universe = _fixed_contract_periods()
    period_by_id = {str(p["contract_period_id"]): p for p in periods}
    checkpoint = OUTPUT_ROOT / "coverage.csv.gz"
    if not checkpoint.is_file():
        raise FileNotFoundError(f"No coverage checkpoint to finalize: {checkpoint}")
    coverage = pd.read_csv(checkpoint, dtype={"symbol": str, "contract_period_id": str}).fillna("")
    if coverage.contract_period_id.duplicated().any() or set(coverage.contract_period_id) != set(period_by_id):
        raise SourceError("Coverage checkpoint period IDs do not match the frozen lifecycle set")

    local_audit: list[dict[str, Any]] = []
    local_presence: dict[tuple[str, str], set[str]] = {}
    local_observed: dict[tuple[str, str], set[str]] = {}
    local_conflicts: Counter[str] = Counter()
    request_audit: list[dict[str, Any]] = []
    expected_raw: set[str] = set()
    rebuilt_rows = []
    for row in coverage.to_dict("records"):
        period_id = str(row["contract_period_id"])
        period = period_by_id[period_id]
        days_value = row.get("day_audit", "")
        days = ast.literal_eval(days_value) if isinstance(days_value, str) and days_value else []
        active = bool(str(row.get("collection_start_utc", "")))
        if active:
            if not days:
                raise SourceError(f"Active coverage checkpoint lacks day audit: {period_id}")
            for name, field in (("expected_5m", "expected_5m"), ("observed_5m", "observed_5m"),
                                ("missing_5m", "missing_5m")):
                if int(row[name]) != sum(int(d[field]) for d in days):
                    raise SourceError(f"Coverage/day audit total mismatch for {period_id}: {name}")
            if int(row["missing_5m"]) != 0 or int(row["incomplete_h1"]) != 0:
                raise SourceError(f"Checkpoint is not a complete source window: {period_id}")
            for day in days:
                date = str(day["day"])
                for source in day.get("source_presence_for_gap", []):
                    local_presence.setdefault((period["symbol"], str(source)), set()).add(date)
                for source in day.get("observed_source_mix", []):
                    if str(source) != "binance_fapi_klines_5m":
                        local_observed.setdefault((period["symbol"], str(source)), set()).add(date)
                fetched = bool(day.get("fetched", False))
                status = str(day.get("fetch_status", ""))
                if not fetched:
                    if status != "not_needed":
                        raise SourceError(f"Unresolved fetch state in checkpoint: {period_id} {day.get('day')} {status}")
                    continue
                raw_path = RAW_REST_ROOT / period["symbol"] / f"{date}.json.gz"
                url = f"{FAPI_URL}?{urllib.parse.urlencode({'symbol': period['symbol'], 'interval': '5m', 'startTime': _date_ms(date), 'endTime': _date_ms(date) + DAY_MS - 1, 'limit': FAPI_LIMIT})}"
                if status == "validated_raw_cache":
                    if not raw_path.is_file():
                        raise SourceError(f"Validated raw cache missing for checkpoint day: {period_id} {date}")
                    expected_raw.add(str(raw_path.relative_to(RAW_REST_ROOT)))
                    request_audit.append({"date": date, "symbol": period["symbol"], "status": status,
                                          "url": url, "raw_path": str(raw_path), "raw_bytes": raw_path.stat().st_size,
                                          "requests": 0, "request_weight": 0})
                else:
                    # The coverage checkpoint does not preserve retry counts or
                    # raw response payloads for network statuses. Do not invent them.
                    raise SourceError(f"Cannot exactly reconstruct request receipt {status} for {period_id} {date}")

        source_path = SERIES_ROOT / f"{period_id.replace('/', '_')}.csv.gz"
        if int(row.get("complete_h1", 0) or 0):
            if not source_path.is_file():
                raise SourceError(f"Coverage checkpoint series file is missing: {source_path}")
            h1 = pd.read_csv(source_path, float_precision="round_trip")
            if len(h1) != int(row["complete_h1"]):
                raise SourceError(f"Coverage/H1 row count mismatch: {period_id}")
            required = {"open_time_ms", "close_time_ms", *PRICE_COLUMNS, "volume", "n_5m"}
            if not required.issubset(h1.columns) or h1.open_time_ms.duplicated().any() or not h1.open_time_ms.is_monotonic_increasing:
                raise SourceError(f"Invalid H1 series schema/order: {period_id}")
            values = h1[[*PRICE_COLUMNS, "volume"]].to_numpy(dtype=np.float64)
            if not np.isfinite(values).all() or (h1[PRICE_COLUMNS] <= 0).any().any() or (h1.volume < 0).any():
                raise SourceError(f"Invalid H1 price/volume values: {period_id}")
            if ((h1.high < h1[["open", "low", "close"]].max(axis=1)) |
                    (h1.low > h1[["open", "high", "close"]].min(axis=1)) | h1.n_5m.ne(12)).any():
                raise SourceError(f"Invalid H1 OHLC geometry or aggregation count: {period_id}")
            if len(h1) > 1 and not h1.open_time_ms.diff().dropna().eq(HOUR_MS).all():
                raise SourceError(f"Unexpected H1 gap in series: {period_id}")
            parity = _quote_parity(period, h1)
            if (parity["checked_bars"] != int(row["overlap_parity_checked_bars"]) or
                    parity["mismatched_bars"] != int(row["overlap_parity_mismatched_bars"])):
                raise SourceError(f"Coverage/frozen overlap parity mismatch: {period_id}")
        elif source_path.exists():
            raise SourceError(f"Series exists for a period reported with no complete H1 bars: {period_id}")
        local_conflicts[str(row["symbol"])] += int(row.get("local_source_conflict_timestamps_in_symbol_window", 0) or 0)
        rebuilt_rows.append(row)

    for symbol in sorted(universe["fixed_symbols"]):
        specs = _candidate_local_sources(symbol)
        paths = {source: (str(path), priority) for source, path, priority in specs}
        source_names = {source for sym, source in local_presence if sym == symbol}
        source_names |= {source for sym, source in local_observed if sym == symbol}
        source_names |= set(paths)
        for source in sorted(source_names):
            presence_days = sorted(local_presence.get((symbol, source), set()))
            observed_days = sorted(local_observed.get((symbol, source), set()))
            path, priority = paths.get(source, ("", None))
            if path:
                status = "present_and_used_in_checkpoint" if presence_days or observed_days else "present_no_checkpoint_rows"
            else:
                status = "used_in_checkpoint_but_source_path_unavailable_now"
            local_audit.append({"symbol": symbol, "source": source, "path": path, "priority": priority,
                                "status": status, "checkpoint_source_presence_days": len(presence_days),
                                "checkpoint_observed_days": len(observed_days),
                                "source_presence_first_day": presence_days[0] if presence_days else None,
                                "source_presence_last_day": presence_days[-1] if presence_days else None,
                                "observed_first_day": observed_days[0] if observed_days else None,
                                "observed_last_day": observed_days[-1] if observed_days else None,
                                "rows_in_range": None})
        local_audit.append({"symbol": symbol, "source": "local_merge_checkpoint", "path": "",
                            "status": "reconstructed_from_daily_coverage", "priority": None,
                            "conflicting_timestamps": int(local_conflicts[symbol])})

    actual_raw = {str(p.relative_to(RAW_REST_ROOT)) for p in RAW_REST_ROOT.glob("*/*.json.gz")}
    missing_raw = expected_raw - actual_raw
    if missing_raw:
        raise SourceError(f"Referenced raw cache files are missing: {sorted(missing_raw)[:5]}")
    mismatches = sum(int(r["overlap_parity_mismatched_bars"]) for r in rebuilt_rows)
    active_rows = [r for r in rebuilt_rows if str(r.get("collection_start_utc", ""))]
    nonfixed = _local_nonfixed_symbols(set(universe["fixed_symbols"]))
    source_audit_path = OUTPUT_ROOT / "source_audit.json"
    manifest = {
        "experiment_id": config["id"], "generated_at_utc": _utc_now(),
        "source_scope": "data source only; no signal generation, strategy run, or PnL calculation",
        "receipt_recovered_from_checkpoint": str(checkpoint),
        "receipt_note": "The failed collector had already validated all referenced API day payloads from disk cache; this finalizer issued no network requests. Historical attempt counts from before this recovery are unavailable.",
        "request_count_scope": "finalization invocation only; prior collection attempts were not preserved in the failed manifest",
        "requested_range": config["range"],
        "universe": {k: v for k, v in universe.items() if k != "fixed_symbols"},
        "period_rows": len(rebuilt_rows), "active_period_rows": len(active_rows),
        "period_status_counts": dict(Counter(str(r["status"]) for r in rebuilt_rows)),
        "fixed_symbols_with_any_h1": len({r["symbol"] for r in rebuilt_rows if int(r.get("complete_h1", 0) or 0)}),
        "h1_rows": int(sum(int(r.get("complete_h1", 0) or 0) for r in rebuilt_rows)),
        "h1_gap_periods": sum(int(int(r.get("incomplete_h1", 0) or 0) > 0) for r in rebuilt_rows),
        "overlap_parity_checked_bars": int(sum(int(r["overlap_parity_checked_bars"]) for r in rebuilt_rows)),
        "overlap_parity_mismatched_bars": int(mismatches),
        "request_count": 0, "request_weight": 0,
        "coverage_path": str(checkpoint), "source_audit_path": str(source_audit_path),
        "series_dir": str(SERIES_ROOT), "raw_rest_cache_dir": str(RAW_REST_ROOT),
        "nonfixed_local_source_summary": nonfixed,
    }
    _write_json_atomic(source_audit_path, {
        "generated_at_utc": manifest["generated_at_utc"],
        "receipt_recovered_from_checkpoint": str(checkpoint),
        "local_source_rows": local_audit, "fapi_day_requests": request_audit,
        "source_policy": config.get("source_policy", config.get("sources", {})),
        "local_lineage_reconstruction": "Source paths and per-day presence/use come from the checkpoint day_audit; per-file row counts were not re-read.",
        "raw_cache_files_referenced": len(expected_raw), "raw_cache_files_present": len(actual_raw),
        "raw_cache_files_unreferenced": len(actual_raw - expected_raw),
        "network_requests_during_finalization": 0,
    })
    _write_json_atomic(OUTPUT_ROOT / "manifest.json", manifest)
    EXPERIMENT.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(EXPERIMENT / "manifest.json", {
        "experiment_id": config["id"], "data_manifest": str(OUTPUT_ROOT / "manifest.json"),
        "coverage": str(checkpoint), "generated_at_utc": manifest["generated_at_utc"],
        "fixed_symbols": universe["fixed_symbol_count"], "lifecycle_periods": len(rebuilt_rows),
        "source_only": True,
    })
    return manifest


def _aggregate_fixture() -> None:
    hour = START_MS
    ts = np.arange(hour, hour + HOUR_MS, BAR_MS, dtype=np.int64)
    frame = pd.DataFrame({"ts": ts, "open": np.arange(1, 13, dtype=float),
                          "high": np.arange(2, 14, dtype=float), "low": np.arange(0, 12, dtype=float),
                          "close": np.arange(1.5, 13.5, dtype=float), "volume": np.ones(12),
                          "source": ["fixture"] * 12})
    period = {"symbol": "FIXUSDT", "contract_period_id": "FIXUSDT__onboard_fixture",
              "onboard_ms": hour, "delivery_ms": hour + HOUR_MS}
    full, audit = _aggregate_h1(frame, period)
    assert len(full) == 1 and full.iloc[0].open == 1 and full.iloc[0].close == 12.5
    assert full.iloc[0].high == 13 and full.iloc[0].low == 0 and full.iloc[0].volume == 12 and full.iloc[0].n_5m == 12
    assert full.iloc[0].close_volume_sum_5m == 84
    assert full.iloc[0].open_time_ms == hour and full.iloc[0].close_time_ms == hour + HOUR_MS - 1
    gap = frame.loc[frame.ts != ts[6]]
    partial, partial_audit = _aggregate_h1(gap, period)
    assert partial.empty and partial_audit["incomplete_h1"] == 1
    assert END_EXCLUSIVE_MS - HOUR_MS == int(pd.Timestamp("2026-11-08T11:00:00Z").timestamp() * 1000)


def mini_check() -> dict[str, Any]:
    _aggregate_fixture()
    periods, universe = _fixed_contract_periods()
    assert universe["fixed_symbol_count"] == 680
    relisted = [p for p in periods if p["symbol"] == "MDTUSDT"]
    assert len(relisted) == 2 and relisted[0]["contract_period_id"] != relisted[1]["contract_period_id"]
    assert relisted[1]["period_link_status"] == "same_symbol_relisting_separate_period_no_continuity_claim"
    local_audit: list[dict[str, Any]] = []
    ada = _local_5m("ADAUSDT", local_audit)
    if ada.empty:
        raise SourceError("mini-check could not read real ADAUSDT local 5m OHLCV")
    if not {"ts", *OHLCV, "source"}.issubset(ada.columns):
        raise AssertionError("local OHLCV schema did not survive source read")
    ada_period = next(p for p in periods if p["symbol"] == "ADAUSDT")
    ada_h1, ada_h1_audit = _aggregate_h1(ada, ada_period)
    if ada_h1.empty or not ada_h1.n_5m.eq(12).all() or not ada_h1.open_time_ms.between(START_MS, END_EXCLUSIVE_MS - HOUR_MS).all():
        raise SourceError("mini-check failed to aggregate real local ADA 5m rows into bounded full H1 bars")
    if (ada_h1.volume < 0).any() or (ada_h1.close_volume_sum_5m < 0).any():
        raise SourceError("mini-check produced invalid real ADA volume/turnover proxy")
    return {"ok": True, "fixed_symbols": universe["fixed_symbol_count"], "lifecycle_periods": universe["lifecycle_period_count"],
            "relisted_symbols": universe["relisted_symbols"], "real_symbol": "ADAUSDT",
            "real_local_5m_rows_in_range": int(len(ada)), "real_local_source_kinds": sorted(set(ada.source)),
            "real_complete_h1_bars": int(len(ada_h1)), "real_incomplete_h1_bars": int(ada_h1_audit["incomplete_h1"]),
            "real_h1_columns": list(ada_h1.columns),
            "local_conflict_timestamps": len(ada.attrs.get("local_conflict_times_ms", [])),
            "output_clock_unit": "epoch milliseconds", "network_requests": 0, "strategy_or_pnl": False}


def smoke_network(symbol: str, day: str, overlap_day: str) -> dict[str, Any]:
    """Three public kline calls: one 5m day, its independent 1h check, and overlap."""
    day_ms = _date_ms(day)
    five, receipt_5m = _fetch_fapi_day(symbol, day_ms, cache=False, min_interval_seconds=0.25)
    expected = np.arange(day_ms, day_ms + DAY_MS, BAR_MS, dtype=np.int64)
    if not np.array_equal(five.ts.to_numpy(dtype=np.int64), expected):
        raise SourceError(f"smoke 5m day did not return all 288 exact bars: {symbol} {day}")
    grouped = []
    for hour in range(day_ms, day_ms + DAY_MS, HOUR_MS):
        group = five.loc[(five.ts >= hour) & (five.ts < hour + HOUR_MS)]
        if len(group) != 12 or not np.array_equal(group.ts.to_numpy(dtype=np.int64), np.arange(hour, hour + HOUR_MS, BAR_MS)):
            raise SourceError(f"smoke 5m aggregation incomplete: {symbol} {_utc(hour)}")
        v = group[OHLCV].to_numpy(dtype=np.float64)
        grouped.append([hour, v[0, 0], v[:, 1].max(), v[:, 2].min(), v[-1, 3], v[:, 4].sum()])
    params = urllib.parse.urlencode({"symbol": symbol, "interval": "1h", "startTime": day_ms,
                                     "endTime": day_ms + DAY_MS - 1, "limit": 100})
    payload, tries = _http_json(f"{FAPI_URL}?{params}", 0.25)
    reference = json.loads(payload)
    if not isinstance(reference, list) or len(reference) != 24:
        raise SourceError(f"smoke independent 1h endpoint did not return 24 bars for {symbol} {day}")
    mismatch = 0
    for actual, expected_row in zip(grouped, reference):
        expected_values = np.asarray([float(expected_row[1]), float(expected_row[2]), float(expected_row[3]),
                                      float(expected_row[4]), float(expected_row[5])])
        if int(actual[0]) != _ms(expected_row[0]) or not np.allclose(np.asarray(actual[1:]), expected_values, rtol=1e-10, atol=1e-12):
            mismatch += 1
    if mismatch:
        raise SourceError(f"5m -> 1h OHLCV aggregation differs from independent native 1h feed in {mismatch} bars")

    overlap_ms = _date_ms(overlap_day)
    overlap, overlap_receipt = _fetch_fapi_day(symbol, overlap_ms, cache=False, min_interval_seconds=0.25)
    quote_path = TRANSFER_WORK / symbol / "quotes.npz"
    parity = {"checked": 0, "mismatched": 0}
    if quote_path.is_file():
        with np.load(quote_path, allow_pickle=False) as quote:
            old_clock = np.asarray(quote["clock"], dtype=np.int64)
            old_prices = np.asarray(quote["prices"], dtype=np.float64)
        hourly = []
        for hour in range(overlap_ms, overlap_ms + DAY_MS, HOUR_MS):
            group = overlap.loc[(overlap.ts >= hour) & (overlap.ts < hour + HOUR_MS)]
            if len(group) == 12 and np.array_equal(group.ts.to_numpy(dtype=np.int64), np.arange(hour, hour + HOUR_MS, BAR_MS)):
                v = group[OHLCV].to_numpy(dtype=np.float64)
                hourly.append((hour, np.asarray([v[0, 0], v[:, 1].max(), v[:, 2].min(), v[-1, 3]], dtype=np.float64)))
        old = {int(t): p for t, p in zip(old_clock, old_prices)}
        for timestamp, values in hourly:
            if timestamp in old:
                parity["checked"] += 1
                parity["mismatched"] += int(not np.allclose(values, old[timestamp], rtol=1e-10, atol=1e-12))
    return {"ok": True, "symbol": symbol, "target_5m_day": day, "target_5m_rows": len(five),
            "target_1h_rows_from_12x5m": len(grouped), "independent_1h_rows": len(reference),
            "target_5m_request": receipt_5m, "overlap_day": overlap_day, "overlap_5m_rows": len(overlap),
            "frozen_overlap_parity": parity, "overlap_request": overlap_receipt,
            "network_requests": int(receipt_5m["requests"]) + int(tries) + int(overlap_receipt["requests"]),
            "network_request_weight": int(receipt_5m["request_weight"]) + 1 + int(overlap_receipt["request_weight"]),
            "no_data_written": True, "strategy_or_pnl": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mini-check", action="store_true", help="run synthetic and local-source checks without network")
    mode.add_argument("--smoke-network", action="store_true", help="validate a real FAPI daily response and 5m->1h aggregation")
    mode.add_argument("--preflight", action="store_true", help="inspect frozen 680-symbol pool and local inputs without network")
    mode.add_argument("--collect", action="store_true", help="build full fixed-pool H1 OHLCV continuation; may use public FAPI")
    mode.add_argument("--finalize-existing", action="store_true", help="validate a complete coverage checkpoint and rebuild receipts without network")
    parser.add_argument("--symbol", default="ADAUSDT", help="fixed-pool symbol for network smoke")
    parser.add_argument("--day", default="2026-10-28", help="UTC day for 5m -> 1h network smoke")
    parser.add_argument("--overlap-day", default="2026-09-22", help="UTC day for frozen H1 overlap smoke")
    args = parser.parse_args(argv)
    if args.mini_check:
        result = mini_check()
    elif args.smoke_network:
        result = smoke_network(args.symbol, args.day, args.overlap_day)
    elif args.collect:
        result = collect(fetch=True)
    elif args.finalize_existing:
        result = finalize_existing()
    else:
        periods, universe = _fixed_contract_periods()
        result = {"ok": True, "universe": {k: v for k, v in universe.items() if k != "fixed_symbols"},
                  "lifecycle_samples": periods[:5],
                  "local_series_root": str(ROOT / "data/crypto/market/binance_um5m_20260923_20261031/series"),
                  "range": {"overlap_start_utc": OVERLAP_START.isoformat(),
                            "continuation_start_utc": CONTINUATION_START.isoformat(),
                            "end_exclusive_utc": END_EXCLUSIVE.isoformat()},
                    "network_requests": 0, "strategy_or_pnl": False}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

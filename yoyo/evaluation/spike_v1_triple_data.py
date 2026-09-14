"""Freeze a causal Binance USD-M universe for the SPIKE V1 triple-exit study.

The study's asset selection is fixed from actual August 2023 quote-asset
turnover, before its 2023-09-01 evaluation window.  It deliberately uses the
public ``data.binance.vision`` monthly archive, rather than a current exchange
catalog or the legacy V1 ``fapi`` cache: current catalogs omit delisted
contracts and the old 6,253-row ledger is an outcome ledger, not a universe.

``rank-august-2023`` enumerates the public archive's symbol directories,
consumes exactly one 15m month per historical USDT symbol, and records every
archive/checksum identity.  Existing checksum-verified local ZIPs may be
reused only after their SHA-256 is matched to the official checksum.  A missing
month or an internal bar gap is quarantined; a continuous partial listing month
is ranked from its actual observed turnover and visibly labelled.  This tool
ranks turnover only; it never loads a trade ledger or computes a return.

Future history acquisition must consume the resulting frozen-universe receipt;
it must not rerun this ranking or choose assets from later market information.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from yoyo.data.binance_um_archives import (
    ARCHIVE_BASE,
    KLINE_COLUMNS,
    REQUEST_HEADERS,
    BinanceArchiveError,
    archive_urls,
    parse_checksum,
    parse_month_zip,
    sha256_file,
)


ARCHIVE_BUCKET_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
S3_PREFIX = "data/futures/um/monthly/klines/"
RANK_MONTH = "2023-08"
RANK_INTERVAL = "15m"
EXPECTED_MONTH_ROWS = 31 * 24 * 4
USDT_PERPETUAL_SYMBOL = re.compile(r"^[A-Z0-9]+USDT$")
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
MONTH_START = "2023-08-01T00:00:00+00:00"
MONTH_END = "2023-08-31T23:45:00+00:00"
HISTORY_START = "2023-07-01T00:00:00+00:00"
HISTORY_END_INCLUSIVE = "2026-08-31T23:59:59+00:00"
INTERVALS = {15: "15m", 60: "1h", 240: "4h"}
OFFICIAL_TICK_RECORDS = {
    "MATICUSDT": {
        "tick_size": "0.0001",
        "effective_at": "2021-06-01T03:30:00+00:00",
        "url": "https://www.binance.com/en/support/announcement/detail/10e4069265794d1db0edf4a88a6be849",
    }
}


class TripleDataError(RuntimeError):
    """Raised when the causal-universe data contract cannot be proven."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _request_bytes(url: str, *, retries: int = 4, timeout: int = 45) -> bytes:
    """Get a public archive object with bounded retry, never from ``fapi``."""

    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise TripleDataError(f"HTTP {exc.code}: {url}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt + 1 == retries:
                raise TripleDataError(f"request failed: {url}") from exc
            time.sleep(2**attempt)
    raise AssertionError("unreachable retry state")


def _s3_list_page(*, continuation_token: str | None) -> tuple[list[str], str | None]:
    """List one public bucket page of kline symbol prefixes."""

    query: dict[str, str] = {
        "list-type": "2",
        "prefix": S3_PREFIX,
        "delimiter": "/",
        "max-keys": "1000",
    }
    if continuation_token:
        query["continuation-token"] = continuation_token
    payload = _request_bytes(f"{ARCHIVE_BUCKET_URL}?{urllib.parse.urlencode(query)}")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise TripleDataError("Binance archive S3 listing is not XML") from exc
    prefixes = [element.text or "" for element in root.findall(f"{S3_NS}CommonPrefixes/{S3_NS}Prefix")]
    next_token = root.findtext(f"{S3_NS}NextContinuationToken")
    return prefixes, next_token


def list_historical_usdt_symbols() -> list[str]:
    """Return all archive-directory USDT symbols, including delisted contracts."""

    prefixes: list[str] = []
    token: str | None = None
    while True:
        page, token = _s3_list_page(continuation_token=token)
        prefixes.extend(page)
        if token is None:
            break
    symbols: list[str] = []
    for prefix in prefixes:
        if not prefix.startswith(S3_PREFIX) or not prefix.endswith("/"):
            raise TripleDataError(f"unexpected S3 prefix: {prefix}")
        symbol = prefix[len(S3_PREFIX) : -1]
        if USDT_PERPETUAL_SYMBOL.fullmatch(symbol):
            symbols.append(symbol)
    if len(symbols) != len(set(symbols)):
        raise TripleDataError("public archive listing returned duplicate symbols")
    return sorted(symbols)


def _existing_cache_path(cache_dir: Path, symbol: str) -> Path:
    return cache_dir / "downloads" / symbol / f"{symbol}-{RANK_INTERVAL}-{RANK_MONTH}.zip"


def _load_rank_month(
    *, symbol: str, cache_dir: Path, output_dir: Path
) -> tuple[bytes, dict[str, Any]]:
    """Resolve one ranked-month ZIP against its official checksum.

    A local cache is a transport optimization only.  Its bytes are accepted
    only if the official checksum endpoint and the local SHA-256 agree.
    Newly fetched ZIPs are stored below this experiment's own data directory.
    """

    archive_url, checksum_url = archive_urls(symbol, RANK_MONTH, RANK_INTERVAL)
    filename = f"{symbol}-{RANK_INTERVAL}-{RANK_MONTH}.zip"
    checksum_payload = _request_bytes(checksum_url)
    expected_sha256 = parse_checksum(checksum_payload, expected_filename=filename)
    cached_path = _existing_cache_path(cache_dir, symbol)
    if cached_path.exists():
        actual_sha256 = sha256_file(cached_path)
        if actual_sha256 == expected_sha256:
            return cached_path.read_bytes(), {
                "archive_url": archive_url,
                "checksum_url": checksum_url,
                "checksum_sha256": hashlib.sha256(checksum_payload).hexdigest(),
                "expected_zip_sha256": expected_sha256,
                "zip_path": str(cached_path),
                "zip_source": "existing_cache_verified_against_official_checksum",
            }
    destination = output_dir / "downloads" / "ranking_aug2023" / symbol / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = _request_bytes(archive_url)
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise TripleDataError(
            f"download checksum mismatch for {symbol} {RANK_MONTH}: {actual_sha256}"
        )
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(payload)
    os.replace(temporary, destination)
    return payload, {
        "archive_url": archive_url,
        "checksum_url": checksum_url,
        "checksum_sha256": hashlib.sha256(checksum_payload).hexdigest(),
        "expected_zip_sha256": expected_sha256,
        "zip_path": str(destination),
        "zip_source": "downloaded_from_official_archive",
        "prior_cache_sha256": sha256_file(cached_path) if cached_path.exists() else None,
    }


def _month_coverage(audit: dict[str, Any]) -> str:
    """Classify a continuous August span without excluding a new listing."""

    if audit["non_bar_gaps"] != 0:
        raise TripleDataError(f"internal 15m gap: {audit}")
    starts_at_month_open = audit["first_time"] == MONTH_START
    ends_at_month_close = audit["last_time"] == MONTH_END
    if starts_at_month_open and ends_at_month_close and audit["rows"] == EXPECTED_MONTH_ROWS:
        return "complete_month"
    if not starts_at_month_open and ends_at_month_close:
        return "partial_listing_month"
    if starts_at_month_open and not ends_at_month_close:
        return "partial_delisting_month"
    return "partial_listing_and_delisting_month"


def _quote_turnover(payload: bytes, *, symbol: str, expected_sha256: str) -> tuple[Decimal, dict[str, Any]]:
    """Validate and sum native column 7 ``quote_volume`` for August 2023."""

    _ohlcv, audit = parse_month_zip(
        payload,
        symbol=symbol,
        month=RANK_MONTH,
        interval=RANK_INTERVAL,
        expected_sha256=expected_sha256,
    )
    audit["month_coverage"] = _month_coverage(audit)
    expected_member = f"{symbol}-{RANK_INTERVAL}-{RANK_MONTH}.csv"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [name for name in archive.namelist() if name.endswith(".csv")]
        if members != [expected_member]:
            raise TripleDataError(f"archive member drift for {symbol}: {members}")
        raw = archive.read(expected_member)
    frame = pd.read_csv(io.BytesIO(raw), header=None, dtype=str)
    if str(frame.iloc[0, 0]).strip().lower() in {"open_time", "open time"}:
        frame = frame.iloc[1:].reset_index(drop=True)
    if frame.shape != (audit["rows"], len(KLINE_COLUMNS)):
        raise TripleDataError(f"unexpected native schema for {symbol}: {frame.shape}")
    quote_values = frame.iloc[:, 7].astype(str)
    try:
        turnover = sum((Decimal(value) for value in quote_values), Decimal("0"))
    except (InvalidOperation, ValueError) as exc:
        raise TripleDataError(f"non-numeric quote_volume for {symbol}") from exc
    if not turnover.is_finite() or turnover <= 0:
        raise TripleDataError(f"non-positive August quote turnover for {symbol}: {turnover}")
    return turnover, audit


def _tick_metadata(cache_dir: Path) -> tuple[dict[str, str], dict[str, Any]]:
    """Read actual tick sizes from the existing immutable exchange-info snapshot.

    The snapshot is evidence of one retrieved exchange metadata state, not an
    attempted reconstruction of historical tick changes.  Absent symbols remain
    explicitly unknown; price decimal precision is never a tick-size proxy.
    """

    path = cache_dir / "exchange_info.json"
    if not path.exists():
        return {}, {"status": "missing", "path": str(path), "sha256": None}
    payload = path.read_bytes()
    try:
        symbols = json.loads(payload).get("symbols", [])
    except json.JSONDecodeError as exc:
        raise TripleDataError(f"invalid cached exchange metadata: {path}") from exc
    ticks: dict[str, str] = {}
    for row in symbols:
        price_filter = next(
            (item for item in row.get("filters", []) if item.get("filterType") == "PRICE_FILTER"),
            None,
        )
        tick = None if price_filter is None else price_filter.get("tickSize")
        if isinstance(tick, str) and Decimal(tick) > 0:
            ticks[str(row.get("symbol"))] = tick
    return ticks, {
        "status": "loaded",
        "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "symbols_with_price_filter": len(ticks),
        "source": "existing cached exchange-info snapshot; no fapi request made",
        "limitation": "one metadata snapshot only; no time-varying tick reconstruction",
    }


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _read_config(experiment_dir: Path) -> tuple[dict[str, Any], str]:
    path = experiment_dir / "config.json"
    if not path.exists():
        raise TripleDataError(f"frozen experiment config is missing: {path}")
    payload = path.read_bytes()
    try:
        config = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TripleDataError(f"invalid frozen experiment config: {path}") from exc
    if config.get("ranking_month") != RANK_MONTH or config.get("universe_size") != 20:
        raise TripleDataError("experiment config does not match the August-2023 fixed-universe contract")
    if config.get("timeframes_minutes") != [15, 60, 240]:
        raise TripleDataError("experiment config does not require native 15m/1h/4h archives")
    if config.get("start") != "2023-09-01T00:00:00Z" or config.get("end_exclusive") != "2026-09-01T00:00:00Z":
        raise TripleDataError("experiment evaluation interval has drifted")
    return config, hashlib.sha256(payload).hexdigest()


def freeze_top20(*, output_dir: Path, cache_dir: Path) -> dict[str, Any]:
    """Content-address the ranked cohort before any three-year downloads occur."""

    experiment_dir = output_dir.parent
    config, config_sha256 = _read_config(experiment_dir)
    ranked_path = output_dir / "ranked_top_universe.csv"
    receipt_path = output_dir / "ranking_receipt.json"
    if not ranked_path.exists() or not receipt_path.exists():
        raise TripleDataError("ranked top20 and ranking receipt must exist before freezing")
    rows = list(pd.read_csv(ranked_path, dtype=str).to_dict("records"))
    if len(rows) != int(config["universe_size"]):
        raise TripleDataError(f"expected {config['universe_size']} ranked assets, found {len(rows)}")
    if [int(row["rank"]) for row in rows] != list(range(1, len(rows) + 1)):
        raise TripleDataError("ranked universe is not a contiguous deterministic top-N")
    if len({row["symbol"] for row in rows}) != len(rows):
        raise TripleDataError("ranked universe contains duplicate symbols")
    cached_ticks, cached_tick_receipt = _tick_metadata(cache_dir)
    tick_sources: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row["symbol"]
        tick_size = row.get("tick_size")
        if isinstance(tick_size, str) and tick_size.strip():
            continue
        record = OFFICIAL_TICK_RECORDS.get(symbol)
        if record is None:
            row["tick_status"] = "missing_not_inferred"
            continue
        try:
            payload = _request_bytes(str(record["url"]))
        except TripleDataError as exc:
            row["tick_status"] = "official_source_unavailable_not_inferred"
            row["tick_source_error"] = str(exc)
            continue
        source_path = output_dir / "tick_sources" / f"{symbol}.html"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = source_path.with_suffix(".html.part")
        temporary.write_bytes(payload)
        os.replace(temporary, source_path)
        row["tick_size"] = str(record["tick_size"])
        row["tick_status"] = "official_historical_announcement_fixed_across_study"
        row["tick_effective_at"] = str(record["effective_at"])
        row["tick_source_url"] = str(record["url"])
        row["tick_source_sha256"] = hashlib.sha256(payload).hexdigest()
        tick_sources[symbol] = {
            "path": str(source_path),
            "url": str(record["url"]),
            "sha256": row["tick_source_sha256"],
            "effective_at": row["tick_effective_at"],
        }
    frozen = {
        "schema": "spike-v1-triple-frozen-top20-v1",
        "frozen_at": _utc_now(),
        "selection": "ranked August-2023 native quote_volume; no later data or returns used",
        "ranked_csv": str(ranked_path),
        "ranked_csv_sha256": sha256_file(ranked_path),
        "ranking_receipt": str(receipt_path),
        "ranking_receipt_sha256": sha256_file(receipt_path),
        "config": str(experiment_dir / "config.json"),
        "config_sha256": config_sha256,
        "history_request": {
            "start": HISTORY_START,
            "end_inclusive": HISTORY_END_INCLUSIVE,
            "intervals": list(INTERVALS.values()),
            "native_intervals_required": True,
            "minimum_continuous_feature_bars": 341,
        },
        "tick_metadata_snapshot": cached_tick_receipt,
        "external_tick_sources": tick_sources,
        "unresolved_tick_symbols": [row["symbol"] for row in rows if row.get("tick_status") == "missing_not_inferred"],
        "assets": rows,
    }
    frozen["frozen_identity_sha256"] = _sha256_json(
        {key: value for key, value in frozen.items() if key not in {"frozen_at", "frozen_identity_sha256"}}
    )
    _write_json(output_dir / "frozen_top20.json", frozen)
    return frozen


def _history_cache_paths(output_dir: Path, cache_dir: Path, symbol: str, interval: str, month: str) -> list[Path]:
    """Return existing ZIP locations in preference order without trusting either."""

    filename = f"{symbol}-{interval}-{month}.zip"
    paths = [
        output_dir / "downloads" / "ranking_aug2023" / symbol / filename,
        cache_dir / "downloads" / symbol / filename,
        output_dir / "downloads" / "history" / interval / symbol / filename,
    ]
    return [path for index, path in enumerate(paths) if path not in paths[:index]]


def _history_month(
    *, output_dir: Path, cache_dir: Path, symbol: str, interval: str, month: str
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Resolve and validate one native timeframe ZIP, retaining missing months."""

    archive_url, checksum_url = archive_urls(symbol, month, interval)
    filename = f"{symbol}-{interval}-{month}.zip"
    try:
        checksum_payload = _request_bytes(checksum_url)
    except TripleDataError as exc:
        if "HTTP 404" in str(exc):
            return None, {"month": month, "status": "missing", "reason": "checksum_404", "checksum_url": checksum_url, "archive_url": archive_url}
        raise
    expected_sha256 = parse_checksum(checksum_payload, expected_filename=filename)
    payload: bytes | None = None
    provenance: dict[str, Any] = {}
    for path in _history_cache_paths(output_dir, cache_dir, symbol, interval, month):
        if path.exists() and sha256_file(path) == expected_sha256:
            payload = path.read_bytes()
            provenance = {"zip_path": str(path), "zip_source": "existing_checksum_verified_cache"}
            break
    if payload is None:
        try:
            payload = _request_bytes(archive_url)
        except TripleDataError as exc:
            if "HTTP 404" in str(exc):
                return None, {"month": month, "status": "missing", "reason": "zip_404", "checksum_url": checksum_url, "archive_url": archive_url}
            raise
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise TripleDataError(f"download checksum mismatch for {symbol} {interval} {month}")
        destination = output_dir / "downloads" / "history" / interval / symbol / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".zip.part")
        temporary.write_bytes(payload)
        os.replace(temporary, destination)
        provenance = {"zip_path": str(destination), "zip_source": "downloaded_from_official_archive"}
    frame, audit = parse_month_zip(
        payload, symbol=symbol, month=month, interval=interval, expected_sha256=expected_sha256
    )
    audit.update(
        {
            "status": "complete" if audit["non_bar_gaps"] == 0 else "gapped",
            "archive_url": archive_url,
            "checksum_url": checksum_url,
            "checksum_sha256": hashlib.sha256(checksum_payload).hexdigest(),
            "expected_zip_sha256": expected_sha256,
            **provenance,
        }
    )
    return frame, audit


def _continuous_segments(frame: pd.DataFrame, interval: str) -> list[dict[str, Any]]:
    """Describe all continuous native-bar segments; missing time is never filled."""

    if frame.empty:
        return []
    delta = pd.Timedelta(minutes={"15m": 15, "1h": 60, "4h": 240}[interval])
    boundaries = frame["open_time"].diff().fillna(delta) != delta
    groups = boundaries.cumsum()
    segments: list[dict[str, Any]] = []
    for _, part in frame.groupby(groups, sort=False):
        segments.append(
            {
                "first_time": part["open_time"].iloc[0].isoformat(),
                "last_time": part["open_time"].iloc[-1].isoformat(),
                "bars": int(len(part)),
                "feature_ready_at_341_bars": part["open_time"].iloc[340].isoformat() if len(part) >= 341 else None,
                "feature_segment_usable": bool(len(part) >= 341),
            }
        )
    return segments


def _acquire_symbol_interval(
    *, output_dir: Path, cache_dir: Path, symbol: str, interval: str, months: list[str]
) -> dict[str, Any]:
    """Acquire one frozen asset/timeframe and publish source identity atomically."""

    audit_path = output_dir / "history_audits" / f"{symbol}_{interval}.json"
    if audit_path.exists():
        prior = json.loads(audit_path.read_text(encoding="utf-8"))
        output_path = Path(str(prior.get("normalized_path", "")))
        if output_path.exists() and sha256_file(output_path) == prior.get("normalized_sha256"):
            return prior
    frames: list[pd.DataFrame] = []
    monthly: list[dict[str, Any]] = []
    for month in months:
        frame, audit = _history_month(
            output_dir=output_dir, cache_dir=cache_dir, symbol=symbol, interval=interval, month=month
        )
        monthly.append(audit)
        if frame is not None:
            frames.append(frame)
    result: dict[str, Any] = {
        "symbol": symbol,
        "interval": interval,
        "months_requested": months,
        "months_complete": [row["month"] for row in monthly if row["status"] == "complete"],
        "months_gapped": [row["month"] for row in monthly if row["status"] == "gapped"],
        "months_missing": [row["month"] for row in monthly if row["status"] == "missing"],
        "month_audits": monthly,
        "normalized_path": None,
        "normalized_sha256": None,
    }
    result["source_sha256"] = _sha256_json(monthly)
    if frames:
        combined = pd.concat(frames, ignore_index=True).sort_values("open_time", kind="mergesort")
        if combined["open_time"].duplicated().any():
            raise TripleDataError(f"duplicate native bars across months for {symbol} {interval}")
        normalized_path = output_dir / "normalized" / f"{symbol}_{interval}.csv.gz"
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = normalized_path.with_suffix(".csv.gz.part")
        combined.to_csv(temporary, index=False, compression="gzip")
        os.replace(temporary, normalized_path)
        segments = _continuous_segments(combined, interval)
        result.update(
            {
                "status": "complete" if not result["months_missing"] and not result["months_gapped"] and len(segments) == 1 else "coverage_limited",
                "rows": int(len(combined)),
                "first_time": combined["open_time"].iloc[0].isoformat(),
                "last_time": combined["open_time"].iloc[-1].isoformat(),
                "continuous_segments": segments,
                "normalized_path": str(normalized_path),
                "normalized_sha256": sha256_file(normalized_path),
            }
        )
    else:
        result["status"] = "no_data"
    _write_json(audit_path, result)
    return result


def _stream_manifest(*, output_dir: Path, frozen: Mapping[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Publish direct runner inputs without hiding missing ticks or bar gaps."""

    assets = {str(row["symbol"]): row for row in frozen["assets"]}
    minutes_by_interval = {interval: minutes for minutes, interval in INTERVALS.items()}
    streams: list[dict[str, Any]] = []
    for result in sorted(results, key=lambda row: (row["symbol"], row["interval"])):
        universe_row = assets[str(result["symbol"])]
        tick = universe_row.get("tick_size")
        streams.append(
            {
                "symbol": result["symbol"],
                "asset": str(result["symbol"])[:-4],
                "minutes": minutes_by_interval[result["interval"]],
                "tick_size": tick if isinstance(tick, str) and tick.strip() else None,
                "tick_status": universe_row.get("tick_status"),
                "normalized_path": result.get("normalized_path"),
                "normalized_sha256": result.get("normalized_sha256"),
                "source_sha256": result["source_sha256"],
                "coverage_status": result["status"],
                "months_missing": result["months_missing"],
                "months_gapped": result["months_gapped"],
                "continuous_segments": result.get("continuous_segments", []),
            }
        )
    manifest = {
        "schema": "spike-v1-triple-stream-manifest-v1",
        "generated_at": _utc_now(),
        "frozen_top20_sha256": sha256_file(output_dir / "frozen_top20.json"),
        "streams": streams,
        "runner_contract": "A stream with missing tick_size, coverage gaps, or no >=341-bar segment is disclosed as unavailable for its affected replay segment; no bar is filled or substituted.",
    }
    manifest["manifest_sha256"] = _sha256_json(
        {key: value for key, value in manifest.items() if key not in {"generated_at", "manifest_sha256"}}
    )
    _write_json(output_dir / "stream_manifest.json", manifest)
    return manifest


def acquire_frozen_history(*, output_dir: Path, cache_dir: Path, workers: int) -> dict[str, Any]:
    """Download only the frozen cohort's native bars, with resumable coverage receipts."""

    if not 1 <= workers <= 4:
        raise TripleDataError("workers must be in [1, 4] to keep archive acquisition conservative")
    frozen_path = output_dir / "frozen_top20.json"
    if not frozen_path.exists():
        raise TripleDataError("run --freeze-top20 before --acquire-frozen-history")
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if frozen.get("history_request", {}).get("intervals") != list(INTERVALS.values()):
        raise TripleDataError("frozen history contract has drifted")
    months = [str(period) for period in pd.period_range("2023-07", "2026-08", freq="M")]
    jobs = [(row["symbol"], interval) for row in frozen["assets"] for interval in INTERVALS.values()]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _acquire_symbol_interval,
                output_dir=output_dir,
                cache_dir=cache_dir,
                symbol=symbol,
                interval=interval,
                months=months,
            ): (symbol, interval)
            for symbol, interval in jobs
        }
        for number, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            print(
                f"native archive {number:02d}/{len(jobs):02d} {result['symbol']:<14} {result['interval']:<3} "
                f"{result['status']:<16} rows={int(result.get('rows', 0)):>7}",
                flush=True,
            )
    summary = {
        "schema": "spike-v1-triple-native-history-v1",
        "generated_at": _utc_now(),
        "provider": "Binance official data.binance.vision USD-M monthly klines",
        "frozen_top20_path": str(frozen_path),
        "frozen_top20_sha256": sha256_file(frozen_path),
        "history_start": HISTORY_START,
        "history_end_inclusive": HISTORY_END_INCLUSIVE,
        "months_requested": months,
        "native_intervals": list(INTERVALS.values()),
        "workers": workers,
        "results": sorted(results, key=lambda row: (row["symbol"], row["interval"])),
    }
    stream_manifest = _stream_manifest(output_dir=output_dir, frozen=frozen, results=results)
    summary["stream_manifest_path"] = str(output_dir / "stream_manifest.json")
    summary["stream_manifest_sha256"] = stream_manifest["manifest_sha256"]
    summary["source_identity_sha256"] = _sha256_json(
        {key: value for key, value in summary.items() if key not in {"generated_at", "source_identity_sha256"}}
    )
    _write_json(output_dir / "native_history_summary.json", summary)
    return summary


def _rank_one(symbol: str, *, cache_dir: Path, output_dir: Path) -> dict[str, Any]:
    try:
        payload, provenance = _load_rank_month(
            symbol=symbol, cache_dir=cache_dir, output_dir=output_dir
        )
        turnover, audit = _quote_turnover(
            payload, symbol=symbol, expected_sha256=str(provenance["expected_zip_sha256"])
        )
        return {
            "symbol": symbol,
            "status": "ranked",
            "august_2023_quote_volume": str(turnover),
            **provenance,
            **audit,
        }
    except Exception as exc:  # Per-symbol quarantine is the required behavior.
        return {"symbol": symbol, "status": "quarantined", "reason": f"{type(exc).__name__}: {exc}"}


def rank_august_2023(*, output_dir: Path, cache_dir: Path, top_n: int, workers: int) -> dict[str, Any]:
    """Produce an auditable fixed top-N universe from historic August turnover."""

    if not 1 <= top_n <= 100:
        raise TripleDataError("top_n must be in [1, 100]")
    if not 1 <= workers <= 4:
        raise TripleDataError("workers must be in [1, 4] to keep archive acquisition conservative")
    candidates = list_historical_usdt_symbols()
    _write_json(
        output_dir / "s3_historical_symbol_listing.json",
        {
            "generated_at": _utc_now(),
            "source": ARCHIVE_BUCKET_URL,
            "prefix": S3_PREFIX,
            "selection": "directory-name matches ^[A-Z0-9]+USDT$; no current exchange catalog",
            "symbols": candidates,
        },
    )
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_rank_one, symbol, cache_dir=cache_dir, output_dir=output_dir): symbol
            for symbol in candidates
        }
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: str(row["symbol"]))
    ranked = sorted(
        (row for row in rows if row["status"] == "ranked"),
        key=lambda row: (-Decimal(str(row["august_2023_quote_volume"])), str(row["symbol"])),
    )
    top = ranked[:top_n]
    ticks, tick_receipt = _tick_metadata(cache_dir)
    ranking_rows = [
        {
            "rank": index,
            "symbol": row["symbol"],
            "august_2023_quote_volume": row["august_2023_quote_volume"],
            "august_2023_month_coverage": row["month_coverage"],
            "zip_source": row["zip_source"],
            "expected_zip_sha256": row["expected_zip_sha256"],
            "csv_sha256": row["csv_sha256"],
            "tick_size": ticks.get(str(row["symbol"])),
            "tick_status": "cached_metadata" if str(row["symbol"]) in ticks else "missing_not_inferred",
        }
        for index, row in enumerate(top, 1)
    ]
    pd.DataFrame(ranking_rows).to_csv(output_dir / "ranked_top_universe.csv", index=False)
    receipt = {
        "schema": "spike-v1-triple-data-ranking-v1",
        "generated_at": _utc_now(),
        "purpose": "fixed asset universe only; no returns, signals, or exit outcomes read",
        "rank_month": RANK_MONTH,
        "rank_interval": RANK_INTERVAL,
        "rank_metric": "sum of native Binance kline column 7 quote_volume over its actual continuous August span",
        "rank_metric_not_used": ["close_times_base_volume proxy", "current turnover", "full-period return"],
        "candidate_universe": "all public data.binance.vision monthly-kline symbol directories matching USDT contract name; historical archive coverage, not a claim of every contract Binance ever listed",
        "listing_source": str(output_dir / "s3_historical_symbol_listing.json"),
        "candidate_count": len(candidates),
        "ranked_complete_count": len(ranked),
        "quarantined_count": len(rows) - len(ranked),
        "top_n": top_n,
        "top_universe": ranking_rows,
        "tick_metadata": tick_receipt,
        "cache_dir": str(cache_dir),
        "acquisition_limit": {"workers": workers, "recommended_nice": 10},
        "next_contract": "A parent-approved freeze must content-address ranked_top_universe.csv before three-year archive acquisition. Do not rerun ranking or select monthly universes.",
    }
    _write_json(output_dir / "ranking_receipt.json", receipt)
    _write_json(
        output_dir / "ranking_coverage.json",
        {"schema": receipt["schema"], "rank_month": RANK_MONTH, "rows": rows},
    )
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rank-august-2023", action="store_true", help="rank historical archive candidates")
    parser.add_argument("--freeze-top20", action="store_true", help="content-address the ranked top20 before history download")
    parser.add_argument("--acquire-frozen-history", action="store_true", help="download only the frozen top20 native histories")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("experiments/active/exp-spike-v1-triple-exit-20260914-v1/data"),
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("data/kline_preholdout_binance_um15m")
    )
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    selected = int(args.rank_august_2023) + int(args.freeze_top20) + int(args.acquire_frozen_history)
    if selected != 1:
        raise TripleDataError("select exactly one of --rank-august-2023, --freeze-top20, or --acquire-frozen-history")
    output_dir = args.output_dir.resolve()
    cache_dir = args.cache_dir.resolve()
    if args.rank_august_2023:
        receipt = rank_august_2023(
            output_dir=output_dir, cache_dir=cache_dir, top_n=args.top_n, workers=args.workers
        )
        print(json.dumps(receipt["top_universe"], indent=2), flush=True)
    elif args.freeze_top20:
        frozen = freeze_top20(output_dir=output_dir, cache_dir=cache_dir)
        print(json.dumps(frozen["assets"], indent=2), flush=True)
    else:
        summary = acquire_frozen_history(output_dir=output_dir, cache_dir=cache_dir, workers=args.workers)
        print(json.dumps({"source_identity_sha256": summary["source_identity_sha256"]}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BinanceArchiveError, TripleDataError) as exc:
        print(f"spike-v1 triple data failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

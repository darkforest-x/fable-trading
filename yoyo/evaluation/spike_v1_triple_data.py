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
or partial month is quarantined in the coverage receipt and cannot enter the
ranked universe.  This tool ranks turnover only; it never loads a trade ledger
or computes a return.

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
from typing import Any, Iterable

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


def _quote_turnover(payload: bytes, *, symbol: str, expected_sha256: str) -> tuple[Decimal, dict[str, Any]]:
    """Validate and sum native column 7 ``quote_volume`` for August 2023."""

    _ohlcv, audit = parse_month_zip(
        payload,
        symbol=symbol,
        month=RANK_MONTH,
        interval=RANK_INTERVAL,
        expected_sha256=expected_sha256,
    )
    expected_first = "2023-08-01T00:00:00+00:00"
    expected_last = "2023-08-31T23:45:00+00:00"
    if (
        audit["rows"] != EXPECTED_MONTH_ROWS
        or audit["first_time"] != expected_first
        or audit["last_time"] != expected_last
        or audit["non_bar_gaps"] != 0
    ):
        raise TripleDataError(
            f"partial or gapped {RANK_MONTH} archive for {symbol}: {audit}"
        )
    expected_member = f"{symbol}-{RANK_INTERVAL}-{RANK_MONTH}.csv"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [name for name in archive.namelist() if name.endswith(".csv")]
        if members != [expected_member]:
            raise TripleDataError(f"archive member drift for {symbol}: {members}")
        raw = archive.read(expected_member)
    frame = pd.read_csv(io.BytesIO(raw), header=None, dtype=str)
    if str(frame.iloc[0, 0]).strip().lower() in {"open_time", "open time"}:
        frame = frame.iloc[1:].reset_index(drop=True)
    if frame.shape != (EXPECTED_MONTH_ROWS, len(KLINE_COLUMNS)):
        raise TripleDataError(f"unexpected native schema for {symbol}: {frame.shape}")
    quote_values = frame.iloc[:, 7].astype(str)
    try:
        turnover = sum((Decimal(value) for value in quote_values), Decimal("0"))
    except (InvalidOperation, ValueError) as exc:
        raise TripleDataError(f"non-numeric quote_volume for {symbol}") from exc
    if not turnover.is_finite() or turnover <= 0:
        raise TripleDataError(f"non-positive August quote turnover for {symbol}: {turnover}")
    return turnover, audit


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
    ranking_rows = [
        {
            "rank": index,
            "symbol": row["symbol"],
            "august_2023_quote_volume": row["august_2023_quote_volume"],
            "zip_source": row["zip_source"],
            "expected_zip_sha256": row["expected_zip_sha256"],
            "csv_sha256": row["csv_sha256"],
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
        "rank_metric": "sum of native Binance kline column 7 quote_volume",
        "rank_metric_not_used": ["close_times_base_volume proxy", "current turnover", "full-period return"],
        "candidate_universe": "all public data.binance.vision monthly-kline symbol directories matching USDT contract name; historical archive coverage, not a claim of every contract Binance ever listed",
        "listing_source": str(output_dir / "s3_historical_symbol_listing.json"),
        "candidate_count": len(candidates),
        "ranked_complete_count": len(ranked),
        "quarantined_count": len(rows) - len(ranked),
        "top_n": top_n,
        "top_universe": ranking_rows,
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
    if not args.rank_august_2023:
        raise TripleDataError("select --rank-august-2023")
    receipt = rank_august_2023(
        output_dir=args.output_dir.resolve(),
        cache_dir=args.cache_dir.resolve(),
        top_n=args.top_n,
        workers=args.workers,
    )
    print(json.dumps(receipt["top_universe"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BinanceArchiveError, TripleDataError) as exc:
        print(f"spike-v1 triple data failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

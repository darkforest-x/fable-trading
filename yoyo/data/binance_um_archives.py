"""Fetch checksum-verified Binance USD-M monthly kline archives before holdout.

The bar interval is a parameter, defaulting to 15m so every existing call site
keeps its exact behaviour. Only the interval string and the bar duration it
implies vary; the checksum, epoch-unit and gap validation are identical for
every interval, and the close-boundary check is derived from the interval
rather than from a literal 899_999 ms.

The source is Binance's public ``data.binance.vision`` archive.  Only USDT
perpetual contracts whose exchange-info ``onboardDate`` predates the frozen
archive ceiling are admitted.  The latest requested archive is 2026-04, so no
OHLCV row at or after the repository holdout (2026-05-04) is downloaded or
materialized.

This module is a dataset input utility, not a trading-data writer.  It writes
to its own immutable research directory and never touches the VPS-owned live
K-line cache.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
ARCHIVE_BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
    )
}
KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
)


class BinanceArchiveError(RuntimeError):
    """Raised when a source, checksum, time, or schema contract drifts."""


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def archive_months(start: object, end_inclusive: object) -> list[str]:
    """Return inclusive UTC ``YYYY-MM`` tokens in chronological order."""

    start_period = pd.Period(_utc(start).tz_localize(None), freq="M")
    end_period = pd.Period(_utc(end_inclusive).tz_localize(None), freq="M")
    if end_period < start_period:
        return []
    return [str(value) for value in pd.period_range(start_period, end_period, freq="M")]


def interval_delta(interval: str) -> pd.Timedelta:
    """Bar duration implied by a Binance interval string."""
    unit = interval[-1]
    value = int(interval[:-1])
    if unit == "m":
        return pd.Timedelta(minutes=value)
    if unit == "h":
        return pd.Timedelta(hours=value)
    if unit == "d":
        return pd.Timedelta(days=value)
    raise BinanceArchiveError(f"unsupported interval: {interval}")


def archive_urls(symbol: str, month: str, interval: str = "15m") -> tuple[str, str]:
    filename = f"{symbol}-{interval}-{month}.zip"
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    encoded_filename = urllib.parse.quote(filename, safe="")
    url = f"{ARCHIVE_BASE}/{encoded_symbol}/{interval}/{encoded_filename}"
    return url, f"{url}.CHECKSUM"


def parse_checksum(payload: bytes, *, expected_filename: str) -> str:
    try:
        parts = payload.decode("utf-8").strip().split()
    except UnicodeDecodeError as exc:
        raise BinanceArchiveError("checksum is not UTF-8 text") from exc
    if len(parts) != 2 or parts[1] != expected_filename:
        raise BinanceArchiveError(
            f"checksum filename drift: expected {expected_filename}, got {parts}"
        )
    digest = parts[0].lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise BinanceArchiveError("checksum digest is not SHA-256")
    return digest


def _normalise_epoch(values: pd.Series, *, label: str) -> tuple[pd.Series, str]:
    numeric = pd.to_numeric(values, errors="raise").astype("int64")
    median = int(numeric.median())
    if median >= 100_000_000_000_000:
        return (numeric // 1_000).astype("int64"), "microseconds"
    if median >= 100_000_000_000:
        return numeric, "milliseconds"
    raise BinanceArchiveError(f"{label} epoch unit is unsupported")


def parse_month_zip(
    payload: bytes,
    *,
    symbol: str,
    month: str,
    expected_sha256: str,
    interval: str = "15m",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse one official USD-M ZIP and validate every OHLCV row."""

    actual_sha = sha256_bytes(payload)
    if actual_sha != expected_sha256:
        raise BinanceArchiveError(
            f"archive checksum mismatch for {symbol} {month}: {actual_sha}"
        )
    expected_filename = f"{symbol}-{interval}-{month}.csv"
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [name for name in archive.namelist() if name.endswith(".csv")]
            if members != [expected_filename]:
                raise BinanceArchiveError(
                    f"archive member drift for {symbol} {month}: {members}"
                )
            csv_payload = archive.read(members[0])
    except zipfile.BadZipFile as exc:
        raise BinanceArchiveError(f"bad ZIP for {symbol} {month}") from exc

    frame = pd.read_csv(io.BytesIO(csv_payload), header=None, dtype=str)
    if frame.empty:
        raise BinanceArchiveError(f"empty archive for {symbol} {month}")
    first = str(frame.iloc[0, 0]).strip().lower()
    if first in {"open_time", "open time"}:
        frame = frame.iloc[1:].reset_index(drop=True)
    if frame.shape[1] != len(KLINE_COLUMNS):
        raise BinanceArchiveError(
            f"kline column drift for {symbol} {month}: {frame.shape[1]}"
        )
    frame.columns = list(KLINE_COLUMNS)
    open_ms, open_unit = _normalise_epoch(frame["open_time"], label="open_time")
    close_ms, close_unit = _normalise_epoch(frame["close_time"], label="close_time")
    if open_unit != close_unit:
        raise BinanceArchiveError(f"mixed epoch units for {symbol} {month}")
    expected_span = int(interval_delta(interval).total_seconds() * 1000) - 1
    if not bool(((close_ms - open_ms) == expected_span).all()):
        raise BinanceArchiveError(f"non-{interval} close boundary for {symbol} {month}")

    numeric_columns = ("open", "high", "low", "close", "volume")
    output = pd.DataFrame({"ts": open_ms})
    for column in numeric_columns:
        output[column] = pd.to_numeric(frame[column], errors="raise").astype(float)
    output["open_time"] = pd.to_datetime(output["ts"], unit="ms", utc=True)
    if not np.isfinite(output[list(numeric_columns)].to_numpy(dtype=float)).all():
        raise BinanceArchiveError(f"non-finite OHLCV for {symbol} {month}")
    if bool((output[["open", "high", "low", "close"]] <= 0.0).any().any()):
        raise BinanceArchiveError(f"non-positive OHLC for {symbol} {month}")
    if bool((output["volume"] < 0.0).any()):
        raise BinanceArchiveError(f"negative volume for {symbol} {month}")
    if bool((output["high"] < output[["open", "close"]].max(axis=1)).any()):
        raise BinanceArchiveError(f"high below candle body for {symbol} {month}")
    if bool((output["low"] > output[["open", "close"]].min(axis=1)).any()):
        raise BinanceArchiveError(f"low above candle body for {symbol} {month}")
    if output["open_time"].duplicated().any() or not output["open_time"].is_monotonic_increasing:
        raise BinanceArchiveError(f"duplicate or descending bars for {symbol} {month}")

    period = pd.Period(month, freq="M")
    start = pd.Timestamp(period.start_time, tz="UTC")
    end = pd.Timestamp((period + 1).start_time, tz="UTC")
    if output["open_time"].min() < start or output["open_time"].max() >= end:
        raise BinanceArchiveError(f"row outside named month for {symbol} {month}")
    return output, {
        "symbol": symbol,
        "month": month,
        "rows": int(len(output)),
        "epoch_unit": open_unit,
        "zip_sha256": actual_sha,
        "csv_sha256": sha256_bytes(csv_payload),
        "first_time": output["open_time"].iloc[0].isoformat(),
        "last_time": output["open_time"].iloc[-1].isoformat(),
        "non_bar_gaps": int(
            (output["open_time"].diff().dropna() != interval_delta(interval)).sum()
        ),
    }


def admitted_symbols(exchange_info: Mapping[str, Any], *, before: object) -> list[dict[str, Any]]:
    """Select the complete pre-ceiling USDT perpetual universe deterministically."""

    boundary_ms = int(_utc(before).value // 1_000_000)
    selected: list[dict[str, Any]] = []
    for row in exchange_info.get("symbols", []):
        if str(row.get("quoteAsset")) != "USDT":
            continue
        if str(row.get("contractType")) != "PERPETUAL":
            continue
        onboard_ms = int(row.get("onboardDate", boundary_ms))
        if onboard_ms >= boundary_ms:
            continue
        selected.append(
            {
                "symbol": str(row["symbol"]),
                "pair": str(row.get("pair", row["symbol"])),
                "status": str(row.get("status", "UNKNOWN")),
                "onboard_time": pd.to_datetime(onboard_ms, unit="ms", utc=True).isoformat(),
            }
        )
    selected.sort(key=lambda value: value["symbol"])
    if len({row["symbol"] for row in selected}) != len(selected):
        raise BinanceArchiveError("exchange info contains duplicate admitted symbols")
    return selected


def _request_bytes(url: str, *, retries: int = 5, timeout: int = 45) -> bytes | None:
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt + 1 == retries:
                raise BinanceArchiveError(f"HTTP {exc.code}: {url}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt + 1 == retries:
                raise BinanceArchiveError(f"request failed: {url}") from exc
        time.sleep(2**attempt)
    raise AssertionError("unreachable retry state")


def _download_month(
    *, symbol: str, month: str, download_dir: Path, interval: str = "15m"
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    zip_url, checksum_url = archive_urls(symbol, month, interval)
    filename = f"{symbol}-{interval}-{month}.zip"
    symbol_dir = download_dir / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)
    zip_path = symbol_dir / filename
    checksum_payload = _request_bytes(checksum_url)
    if checksum_payload is None:
        return None, {"month": month, "status": "missing", "reason": "checksum_404"}
    expected_sha = parse_checksum(checksum_payload, expected_filename=filename)
    if zip_path.exists() and sha256_file(zip_path) == expected_sha:
        payload = zip_path.read_bytes()
        download_status = "cached"
    else:
        payload = _request_bytes(zip_url)
        if payload is None:
            return None, {"month": month, "status": "missing", "reason": "zip_404"}
        if sha256_bytes(payload) != expected_sha:
            raise BinanceArchiveError(f"download checksum mismatch: {symbol} {month}")
        temporary = zip_path.with_suffix(".zip.part")
        temporary.write_bytes(payload)
        os.replace(temporary, zip_path)
        download_status = "downloaded"
    frame, audit = parse_month_zip(
        payload,
        symbol=symbol,
        month=month,
        expected_sha256=expected_sha,
    )
    audit.update(
        {
            "status": "complete",
            "download_status": download_status,
            "zip_path": str(zip_path),
            "checksum_url": checksum_url,
            "archive_url": zip_url,
        }
    )
    return frame, audit


def fetch_symbol(
    row: Mapping[str, Any],
    *,
    output_dir: Path,
    archive_start: object,
    archive_end_inclusive: object,
    archive_max_exclusive: object,
    interval: str = "15m",
) -> dict[str, Any]:
    """Fetch one symbol, aggregate its complete monthly bars, and publish atomically."""

    symbol = str(row["symbol"])
    audit_path = output_dir / "audits" / f"{symbol}.json"
    if audit_path.exists():
        prior = json.loads(audit_path.read_text(encoding="utf-8"))
        output_path = Path(str(prior.get("output_path", "")))
        if (
            prior.get("status") == "complete"
            and output_path.exists()
            and sha256_file(output_path) == str(prior.get("output_sha256"))
        ):
            return prior

    onboard = _utc(row["onboard_time"])
    first_month = max(_utc(archive_start), onboard.floor("D")).strftime("%Y-%m")
    months = archive_months(f"{first_month}-01", archive_end_inclusive)
    frames: list[pd.DataFrame] = []
    month_audits: list[dict[str, Any]] = []
    for month in months:
        frame, audit = _download_month(
            symbol=symbol,
            month=month,
            download_dir=output_dir / "downloads",
        )
        month_audits.append(audit)
        if frame is not None:
            frames.append(frame)

    result: dict[str, Any] = {
        "symbol": symbol,
        "pair": str(row["pair"]),
        "exchange_status": str(row["status"]),
        "onboard_time": onboard.isoformat(),
        "months_requested": months,
        "months_complete": [a["month"] for a in month_audits if a["status"] == "complete"],
        "months_missing": [a["month"] for a in month_audits if a["status"] == "missing"],
        "month_audits": month_audits,
        "status": "no_data" if not frames else "complete",
        "holdout_ohlcv_rows_materialized": 0,
        "output_path": None,
    }
    if frames:
        combined = (
            pd.concat(frames, ignore_index=True)
            .sort_values("open_time", kind="mergesort")
            .drop_duplicates("open_time", keep="first")
            .reset_index(drop=True)
        )
        boundary = _utc(archive_max_exclusive)
        if combined["open_time"].max() >= boundary:
            raise BinanceArchiveError(f"archive ceiling crossed for {symbol}")
        output_path = output_dir / "series" / f"binance_um_{symbol}_{interval}_{len(combined)}.csv"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".csv.part")
        combined.to_csv(temporary, index=False)
        os.replace(temporary, output_path)
        result.update(
            {
                "output_path": str(output_path),
                "output_sha256": sha256_file(output_path),
                "rows": int(len(combined)),
                "first_time": combined["open_time"].iloc[0].isoformat(),
                "last_time": combined["open_time"].iloc[-1].isoformat(),
                "non_bar_gaps": int(
                    (combined["open_time"].diff().dropna() != interval_delta(interval)).sum()
                ),
            }
        )
    write_json(audit_path, result)
    return result


def fetch_universe(
    *,
    output_dir: Path,
    archive_start: object,
    archive_end_inclusive: object,
    archive_max_exclusive: object,
    holdout_start: object,
    workers: int = 24,
    interval: str = "15m",
) -> dict[str, Any]:
    """Fetch the admitted universe with symbol-level resumability."""

    output_dir = output_dir.resolve()
    archive_ceiling = _utc(archive_max_exclusive)
    if archive_ceiling > _utc(holdout_start):
        raise BinanceArchiveError("archive ceiling may not cross repository holdout")
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "exchange_info.json"
    if snapshot_path.exists():
        snapshot_bytes = snapshot_path.read_bytes()
    else:
        payload = _request_bytes(EXCHANGE_INFO_URL)
        if payload is None:
            raise BinanceArchiveError("exchange-info endpoint returned 404")
        json.loads(payload)
        temporary = snapshot_path.with_suffix(".json.part")
        temporary.write_bytes(payload)
        os.replace(temporary, snapshot_path)
        snapshot_bytes = payload
    exchange_info = json.loads(snapshot_bytes)
    symbols = admitted_symbols(exchange_info, before=archive_ceiling)
    write_json(output_dir / "admitted_symbols.json", symbols)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=int(workers)) as pool:
        futures = {
            pool.submit(
                fetch_symbol,
                row,
                output_dir=output_dir,
                archive_start=archive_start,
                archive_end_inclusive=archive_end_inclusive,
                archive_max_exclusive=archive_ceiling,
                interval=interval,
            ): str(row["symbol"])
            for row in symbols
        }
        for index, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            result = future.result()
            results.append(result)
            print(
                f"binance archive {index:03d}/{len(futures):03d} {symbol:<18} "
                f"{result['status']:<8} rows={int(result.get('rows', 0)):>7}",
                flush=True,
            )

    complete = [row for row in results if row["status"] == "complete"]
    summary = {
        "schema_version": 1,
        "provider": "Binance official data.binance.vision USD-M monthly klines",
        "exchange_info_url": EXCHANGE_INFO_URL,
        "exchange_info_path": str(snapshot_path),
        "exchange_info_sha256": sha256_bytes(snapshot_bytes),
        "archive_start": _utc(archive_start).isoformat(),
        "archive_end_inclusive": _utc(archive_end_inclusive).isoformat(),
        "archive_max_exclusive": archive_ceiling.isoformat(),
        "holdout_start": _utc(holdout_start).isoformat(),
        "symbols_admitted": len(symbols),
        "symbols_complete": len(complete),
        "symbols_no_data": len(results) - len(complete),
        "rows": sum(int(row.get("rows", 0)) for row in complete),
        "months_complete": sum(len(row["months_complete"]) for row in results),
        "months_missing": sum(len(row["months_missing"]) for row in results),
        "holdout_ohlcv_rows_materialized": 0,
        "results": sorted(results, key=lambda value: str(value["symbol"])),
    }
    write_json(output_dir / "archive_fetch_summary.json", summary)
    return summary

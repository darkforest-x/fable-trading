"""Recover the 2022-02-26..2022-02-28 Binance UM 5m source gap.

This module only reads the frozen transfer-study quote cache and official
Binance UM public market data. Downloaded archives and the normalized repair
cache live under ``data/research/cloud_close_binance_feb2022_daily``. The
returned frame matches ``signal_center_score.five_minute``: UTC nanosecond
bar-open index and float ``open/high/low/close/volume`` columns.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import functools
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "experiments/active/exp-cloud-close-binance-transfer-20261109-v1/work"
DATA_DIR = ROOT / "data/research/cloud_close_binance_feb2022_daily"
START = pd.Timestamp("2022-02-26T00:00:00Z")
END = pd.Timestamp("2022-03-01T00:00:00Z")
START_MS = START.value // 1_000_000
END_MS = END.value // 1_000_000
DAY_MS = 86_400_000
BAR_MS = 300_000
HOUR_MS = 3_600_000
EXPECTED_COHORT = 112
EXPECTED_COMPLETE = 69
EXPECTED_AFFECTED = 43
EXPECTED_BARS = 864
OHLCV = ["open", "high", "low", "close", "volume"]
PRICE_COLUMNS = ["open", "high", "low", "close"]
MAX_ATTEMPTS = 3
HTTP_TIMEOUT_SECONDS = 30
RETRY_DELAYS_SECONDS = (0.5, 1.0)
VISION_ROOT = "https://data.binance.vision/data/futures/um/daily/klines"
FAPI_ROOT = "https://fapi.binance.com/fapi/v1/klines"
USER_AGENT = "fable-trading-research/1.0"


class OfficialSourceUnavailable(RuntimeError):
    """Raised only after official Vision and fapi sources fail validation."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _quote_files() -> list[Path]:
    files = sorted(WORK.glob("*/quotes.npz"))
    if len(files) != 632:
        raise ValueError(f"Expected 632 frozen Binance quote files, found {len(files)} in {WORK}")
    tickers = [p.parent.name for p in files]
    if len(tickers) != len(set(tickers)) or any(not s.endswith("USDT") for s in tickers):
        raise ValueError("Frozen quote cache ticker identities are not unique USDT contracts")
    return files


def _load_clock(path: Path) -> np.ndarray:
    with np.load(path, allow_pickle=False) as archive:
        if "clock" not in archive.files:
            raise ValueError(f"{path}: missing frozen clock")
        clock = np.asarray(archive["clock"], dtype=np.int64)
    if clock.ndim != 1 or (len(clock) > 1 and np.any(np.diff(clock) <= 0)):
        raise ValueError(f"{path}: clock must be a strictly increasing int64 vector")
    return clock


@functools.lru_cache(maxsize=1)
def affected_tickers() -> list[str]:
    """Return the 43 frozen contracts missing exactly the three target days.

    Candidates must have cached hourly bars on both sides of the gap. Counts
    are checked against the frozen 112-contract cohort so new or partial gaps
    cannot silently change the repair universe.
    """
    scoped: list[tuple[str, int]] = []
    for path in _quote_files():
        clock = _load_clock(path)
        before = bool(np.any(clock < START_MS))
        after = bool(np.any(clock >= END_MS))
        if before and after:
            count = int(np.count_nonzero((clock >= START_MS) & (clock < END_MS)))
            scoped.append((path.parent.name, count))

    if len(scoped) != EXPECTED_COHORT:
        raise ValueError(f"Expected {EXPECTED_COHORT} contracts with quotes before and after gap, found {len(scoped)}")
    counts: dict[int, int] = {}
    for _, count in scoped:
        counts[count] = counts.get(count, 0) + 1
    expected = {0: EXPECTED_AFFECTED, 72: EXPECTED_COMPLETE}
    if counts != expected:
        raise ValueError(f"Frozen gap census changed: observed {counts}, expected {expected}")
    result = sorted(ticker for ticker, count in scoped if count == 0)
    if len(result) != EXPECTED_AFFECTED:
        raise ValueError(f"Expected {EXPECTED_AFFECTED} affected tickers, found {len(result)}")
    return result


def _expected_opens(day_start_ms: int) -> np.ndarray:
    return np.arange(day_start_ms, day_start_ms + DAY_MS, BAR_MS, dtype=np.int64)


def _validate_frame(frame: pd.DataFrame, expected_opens: np.ndarray, label: str) -> pd.DataFrame:
    if list(frame.columns) != OHLCV:
        raise ValueError(f"{label}: expected columns {OHLCV}, got {list(frame.columns)}")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError(f"{label}: expected timezone-aware DatetimeIndex")
    frame = frame.copy()
    frame.index = frame.index.tz_convert("UTC").as_unit("ns")
    observed = frame.index.asi8 // 1_000_000
    if len(frame) != len(expected_opens) or not np.array_equal(observed, expected_opens):
        missing = np.setdiff1d(expected_opens, observed)
        extra = np.setdiff1d(observed, expected_opens)
        raise ValueError(f"{label}: expected {len(expected_opens)} exact bar opens; got {len(frame)}, missing={len(missing)}, extra={len(extra)}")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError(f"{label}: bar opens are duplicated or out of order")
    values = frame[OHLCV].to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError(f"{label}: OHLCV contains a non-finite value")
    if (frame[PRICE_COLUMNS] <= 0).any().any() or (frame.volume < 0).any():
        raise ValueError(f"{label}: non-positive price or negative volume")
    if (frame.high < frame[PRICE_COLUMNS].max(axis=1)).any():
        raise ValueError(f"{label}: high is below one of open/high/low/close")
    if (frame.low > frame[PRICE_COLUMNS].min(axis=1)).any():
        raise ValueError(f"{label}: low is above one of open/high/low/close")
    return frame


def _read_daily_zip(payload: bytes, ticker: str, day_start_ms: int, label: str) -> pd.DataFrame:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"{label}: expected one CSV member, found {members}")
            raw = archive.read(members[0]).decode("utf-8-sig")
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise ValueError(f"{label}: not a readable Binance daily ZIP") from exc

    rows: list[tuple[int, float, float, float, float, float]] = []
    reader = csv.reader(io.StringIO(raw, newline=""))
    for row_number, row in enumerate(reader, 1):
        if not row:
            continue
        try:
            ts = int(row[0])
            if len(row) < 6:
                raise ValueError("fewer than six kline fields")
            values = tuple(float(value) for value in row[1:6])
        except (ValueError, IndexError) as exc:
            if row_number == 1 and row and row[0].strip().lower() in {"open_time", "opentime"}:
                continue
            raise ValueError(f"{label}: malformed kline CSV row {row_number}") from exc
        rows.append((ts, *values))

    if not rows:
        raise ValueError(f"{label}: daily ZIP contains no klines for {ticker}")
    data = np.asarray(rows, dtype=np.float64)
    opens = data[:, 0].astype(np.int64)
    frame = pd.DataFrame(data[:, 1:], columns=OHLCV,
                         index=pd.DatetimeIndex(pd.to_datetime(opens, unit="ms", utc=True)).as_unit("ns"))
    frame = _validate_frame(frame, _expected_opens(day_start_ms), label)
    return frame


def _local_zip_roots() -> list[Path]:
    roots = [DATA_DIR]
    extra = os.environ.get("CLOUD_CLOSE_BINANCE_DAILY_ZIP_ROOTS", "")
    for value in extra.split(os.pathsep):
        value = value.strip()
        if value:
            candidate = Path(value).expanduser()
            roots.append(candidate if candidate.is_absolute() else ROOT / candidate)
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve(strict=False))
        if key not in seen:
            unique.append(root)
            seen.add(key)
    return unique


def _daily_name(ticker: str, day_start_ms: int) -> str:
    day = pd.Timestamp(day_start_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
    return f"{ticker}-5m-{day}.zip"


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    try:
        with temp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _http_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, None)
        return response.read()


def _fetch_daily_vision(ticker: str, day_start_ms: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    filename = _daily_name(ticker, day_start_ms)
    errors: list[str] = []
    for root in _local_zip_roots():
        local_path = root / filename
        if not local_path.is_file():
            continue
        try:
            payload = local_path.read_bytes()
            frame = _read_daily_zip(payload, ticker, day_start_ms, f"local:{local_path}")
            return frame, dict(source="local_official_vision_zip", url=None, raw_file=str(local_path),
                               raw_bytes=len(payload), retrieved_at_utc=None)
        except Exception as exc:
            errors.append(f"local {local_path}: {type(exc).__name__}: {exc}")

    url = f"{VISION_ROOT}/{ticker}/5m/{filename}"
    for attempt in range(MAX_ATTEMPTS):
        try:
            payload = _http_bytes(url)
            frame = _read_daily_zip(payload, ticker, day_start_ms, url)
            target = DATA_DIR / filename
            _atomic_write(target, payload)
            return frame, dict(source="binance_vision_daily_zip", url=url, raw_file=str(target),
                               raw_bytes=len(payload), retrieved_at_utc=_utc_now())
        except urllib.error.HTTPError as exc:
            errors.append(f"HTTP {exc.code}: {url}")
            # Missing archives are permanent; retry only rate limits and server errors.
            if exc.code not in (408, 425, 429) and exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        except ValueError as exc:
            errors.append(f"invalid archive: {exc}")
            break
        if attempt + 1 < MAX_ATTEMPTS:
            time.sleep(RETRY_DELAYS_SECONDS[min(attempt, len(RETRY_DELAYS_SECONDS) - 1)])
    raise OfficialSourceUnavailable(f"Binance Vision daily archive unavailable for {ticker} {filename}: {'; '.join(errors)}")


def _fapi_day(ticker: str, day_start_ms: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    day_end_ms = day_start_ms + DAY_MS - 1
    params = urllib.parse.urlencode({"symbol": ticker, "interval": "5m", "startTime": day_start_ms,
                                     "endTime": day_end_ms, "limit": 1000})
    url = f"{FAPI_ROOT}?{params}"
    errors: list[str] = []
    payload: bytes | None = None
    parsed: Any = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            payload = _http_bytes(url)
            parsed = json.loads(payload)
            break
        except urllib.error.HTTPError as exc:
            errors.append(f"HTTP {exc.code}: {url}")
            if exc.code not in (408, 425, 429) and exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        if attempt + 1 < MAX_ATTEMPTS:
            time.sleep(RETRY_DELAYS_SECONDS[min(attempt, len(RETRY_DELAYS_SECONDS) - 1)])
    if payload is None or not isinstance(parsed, list):
        raise OfficialSourceUnavailable(f"Binance fapi unavailable for {ticker} {pd.Timestamp(day_start_ms, unit='ms', tz='UTC').date()}: {'; '.join(errors)}")

    try:
        rows = []
        for record in parsed:
            if not isinstance(record, list) or len(record) < 6:
                raise ValueError("fapi response row has fewer than six fields")
            rows.append((int(record[0]), *(float(value) for value in record[1:6])))
        if not rows:
            raise ValueError("fapi response contains no klines")
        array = np.asarray(rows, dtype=np.float64)
        opens = array[:, 0].astype(np.int64)
        frame = pd.DataFrame(array[:, 1:], columns=OHLCV,
                             index=pd.DatetimeIndex(pd.to_datetime(opens, unit="ms", utc=True)).as_unit("ns"))
        frame = _validate_frame(frame, _expected_opens(day_start_ms), f"fapi:{ticker}:{day_start_ms}")
    except (TypeError, ValueError, OverflowError) as exc:
        raise OfficialSourceUnavailable(f"Binance fapi data failed exact-day validation for {ticker}: {exc}") from exc

    date = pd.Timestamp(day_start_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
    target = DATA_DIR / f"{ticker}-5m-{date}-fapi.json"
    _atomic_write(target, payload)
    return frame, dict(source="binance_um_fapi_klines", url=url, raw_file=str(target),
                       raw_bytes=len(payload), retrieved_at_utc=_utc_now())


def _verify_repaired_window(ticker: str, frame: pd.DataFrame) -> dict[str, Any]:
    path = WORK / ticker / "quotes.npz"
    with np.load(path, allow_pickle=False) as archive:
        clock = np.asarray(archive["clock"], dtype=np.int64)
    old_window_count = int(np.count_nonzero((clock >= START_MS) & (clock < END_MS)))
    if old_window_count != 0:
        raise ValueError(f"{ticker}: frozen affected quote cache unexpectedly has {old_window_count} old hourly bars in repair window")
    before = clock[clock < START_MS]
    after = clock[clock >= END_MS]
    if not len(before) or not len(after):
        raise ValueError(f"{ticker}: frozen quote cache lacks prices on one side of the repair window")

    grouped = frame.resample("1h", origin="epoch", label="left", closed="left")
    hourly = grouped.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                         close=("close", "last"), n=("close", "count"))
    hourly = hourly[hourly.n.eq(12)].drop(columns="n")
    observed_clock = hourly.index.as_unit("ms").asi8
    observed_prices = hourly[PRICE_COLUMNS].to_numpy(dtype=np.float64)
    expected_clock = np.arange(START_MS, END_MS, HOUR_MS, dtype=np.int64)
    if not np.array_equal(observed_clock, expected_clock):
        raise ValueError(f"{ticker}: repaired 5m bars do not aggregate to the exact 72 UTC hourly opens")
    if observed_prices.shape != (72, 4) or not np.isfinite(observed_prices).all():
        raise ValueError(f"{ticker}: aggregate hourly OHLC values are incomplete or non-finite")
    if np.any(observed_prices[:, 1] < observed_prices[:, [0, 2, 3]].max(axis=1)):
        raise ValueError(f"{ticker}: aggregate hourly high is below open/low/close")
    if np.any(observed_prices[:, 2] > observed_prices[:, [0, 1, 3]].min(axis=1)):
        raise ValueError(f"{ticker}: aggregate hourly low is above open/high/close")
    return dict(frozen_original_hourly_bars_in_window=old_window_count,
                frozen_previous_hourly_open_ms=int(before[-1]), frozen_next_hourly_open_ms=int(after[0]),
                repaired_complete_hourly_bars=int(len(observed_clock)), hourly_ohlc_values_validated=int(observed_prices.size),
                hourly_first_open_utc=hourly.index[0].isoformat(), hourly_last_open_utc=hourly.index[-1].isoformat(),
                aggregation="5m open=first, high=max, low=min, close=last; exactly 12 bars per UTC hour; no old-window OHLC comparison")


def _canonical_path(ticker: str) -> Path:
    return DATA_DIR / f"{ticker}-5m-2022-02-26_to_2022-03-01.csv.gz"


def _source_receipt_path(ticker: str) -> Path:
    return DATA_DIR / f"{ticker}-repair-receipt.json"


def _reconstruct_source_records(ticker: str) -> list[dict[str, Any]]:
    """Recover per-day provenance from saved official raw payloads on resume."""
    result: list[dict[str, Any]] = []
    for day_start_ms in range(START_MS, END_MS, DAY_MS):
        date = pd.Timestamp(day_start_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
        zip_path = DATA_DIR / _daily_name(ticker, day_start_ms)
        if zip_path.is_file():
            payload = zip_path.read_bytes()
            _read_daily_zip(payload, ticker, day_start_ms, f"resume:{zip_path}")
            result.append(dict(day=date, source="binance_vision_daily_zip_cached",
                               url=f"{VISION_ROOT}/{ticker}/5m/{_daily_name(ticker, day_start_ms)}",
                               raw_file=str(zip_path), raw_bytes=len(payload), retrieved_at_utc=None))
            continue
        fapi_path = DATA_DIR / f"{ticker}-5m-{date}-fapi.json"
        if fapi_path.is_file():
            payload = fapi_path.read_bytes()
            try:
                parsed = json.loads(payload)
                rows = [(int(row[0]), *(float(value) for value in row[1:6])) for row in parsed]
                array = np.asarray(rows, dtype=np.float64)
                opens = array[:, 0].astype(np.int64)
                cached = pd.DataFrame(array[:, 1:], columns=OHLCV,
                                      index=pd.DatetimeIndex(pd.to_datetime(opens, unit="ms", utc=True)).as_unit("ns"))
                _validate_frame(cached, _expected_opens(day_start_ms), f"resume:fapi:{fapi_path}")
            except (TypeError, ValueError, IndexError, json.JSONDecodeError) as exc:
                raise ValueError(f"Cannot reconstruct provenance from {fapi_path}: {exc}") from exc
            params = urllib.parse.urlencode({"symbol": ticker, "interval": "5m", "startTime": day_start_ms,
                                             "endTime": day_start_ms + DAY_MS - 1, "limit": 1000})
            result.append(dict(day=date, source="binance_um_fapi_klines_cached", url=f"{FAPI_ROOT}?{params}",
                               raw_file=str(fapi_path), raw_bytes=len(payload), retrieved_at_utc=None))
            continue
        raise ValueError(f"{ticker} {date}: canonical cache has no per-ticker receipt or saved official raw source")
    return result


def _read_canonical(ticker: str) -> pd.DataFrame | None:
    path = _canonical_path(ticker)
    if not path.is_file():
        return None
    frame = pd.read_csv(path, compression="gzip", float_precision="round_trip")
    if "ts" not in frame.columns:
        raise ValueError(f"{path}: missing ts field")
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("ts"), unit="ms", utc=True)).as_unit("ns")
    frame = frame[OHLCV]
    return _validate_frame(frame, np.arange(START_MS, END_MS, BAR_MS, dtype=np.int64), str(path))


def patch_bars(ticker: str) -> pd.DataFrame:
    """Return validated official 5m OHLCV for the three affected UTC days.

    Raises ``ValueError`` for a ticker outside the frozen 43-contract repair
    set, and ``OfficialSourceUnavailable`` only after both official Binance
    data sources fail to provide one exact 288-row day.
    """
    if ticker not in set(affected_tickers()):
        raise ValueError(f"{ticker} is not in the frozen affected ticker set")
    cached = _read_canonical(ticker)
    if cached is not None:
        check = _verify_repaired_window(ticker, cached)
        receipt_path = _source_receipt_path(ticker)
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            records = receipt.get("days")
            if not isinstance(records, list) or len(records) != 3:
                raise ValueError(f"{ticker}: malformed per-ticker source receipt {receipt_path}")
        else:
            records = _reconstruct_source_records(ticker)
        cached.attrs.update(ticker=ticker, source_records=records, hourly_check=check)
        return cached

    frames: list[pd.DataFrame] = []
    source_records: list[dict[str, Any]] = []
    for day_start_ms in range(START_MS, END_MS, DAY_MS):
        try:
            day_frame, record = _fetch_daily_vision(ticker, day_start_ms)
        except OfficialSourceUnavailable as vision_error:
            try:
                day_frame, record = _fapi_day(ticker, day_start_ms)
                record["vision_error"] = str(vision_error)
            except OfficialSourceUnavailable as fapi_error:
                raise OfficialSourceUnavailable(
                    f"No validated official Binance source for {ticker} on "
                    f"{pd.Timestamp(day_start_ms, unit='ms', tz='UTC').date()}; "
                    f"vision={vision_error}; fapi={fapi_error}"
                ) from fapi_error
        frames.append(day_frame)
        source_records.append(dict(day=pd.Timestamp(day_start_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d"), **record))

    frame = pd.concat(frames).sort_index()
    frame = _validate_frame(frame, np.arange(START_MS, END_MS, BAR_MS, dtype=np.int64), f"{ticker}:combined official repair")
    hourly_check = _verify_repaired_window(ticker, frame)
    frame.attrs.update(ticker=ticker, source_records=source_records, hourly_check=hourly_check)
    return frame


def _save_canonical(ticker: str, frame: pd.DataFrame) -> dict[str, Any]:
    path = _canonical_path(ticker)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    try:
        export = frame.copy()
        export.insert(0, "ts", export.index.as_unit("ms").asi8)
        export.to_csv(temp, index=False, compression="gzip", float_format="%.17g")
        with temp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    saved = dict(path=str(path), bytes=path.stat().st_size)
    per_ticker = dict(ticker=ticker, generated_at_utc=_utc_now(), canonical=saved,
                      rows=len(frame), hourly_check=frame.attrs.get("hourly_check"),
                      days=frame.attrs.get("source_records"))
    if not isinstance(per_ticker["days"], list) or len(per_ticker["days"]) != 3:
        raise ValueError(f"{ticker}: refusing to save canonical data without three source records")
    _atomic_write(_source_receipt_path(ticker), (json.dumps(per_ticker, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
    saved["source_receipt"] = str(_source_receipt_path(ticker))
    saved["source_receipt_bytes"] = _source_receipt_path(ticker).stat().st_size
    return saved


def _local_archive_count(tickers: list[str]) -> int:
    return sum(1 for ticker in tickers for day in range(START_MS, END_MS, DAY_MS)
               if any((root / _daily_name(ticker, day)).is_file() for root in _local_zip_roots()))


def _preflight() -> dict[str, Any]:
    tickers = affected_tickers()
    return dict(mode="preflight_no_download", quote_files=632, contracts_with_before_after=EXPECTED_COHORT,
                complete_72_hour_gap_bars=EXPECTED_COMPLETE, affected_tickers=len(tickers),
                targeted_local_daily_zip_files=_local_archive_count(tickers),
                window_start_utc=START.isoformat(), window_end_exclusive_utc=END.isoformat(),
                tickers=tickers)


def _fetch_one(ticker: str) -> dict[str, Any]:
    frame = patch_bars(ticker)
    check = dict(frame.attrs.get("hourly_check", {}))
    records = frame.attrs.get("source_records", [])
    if len(records) != 3:
        raise ValueError(f"{ticker}: cannot make a three-day provenance receipt")
    canonical = _save_canonical(ticker, frame)
    return dict(rows=len(frame), first_bar_utc=frame.index[0].isoformat(), last_bar_utc=frame.index[-1].isoformat(),
                canonical=canonical, hourly_check=check, days=records)


def _fetch_all(workers: int) -> dict[str, Any]:
    if not 1 <= workers <= 4:
        raise ValueError("--workers must be between 1 and 4")
    tickers = affected_tickers()
    results: dict[str, dict[str, Any]] = {}
    errors: list[tuple[str, BaseException]] = []
    if workers == 1:
        for index, ticker in enumerate(tickers, 1):
            print(f"Repairing {ticker} ({index}/{len(tickers)})", flush=True)
            try:
                results[ticker] = _fetch_one(ticker)
            except OfficialSourceUnavailable as exc:
                raise
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_fetch_one, ticker): ticker for ticker in tickers}
            for future in concurrent.futures.as_completed(futures):
                ticker = futures[future]
                try:
                    results[ticker] = future.result()
                except BaseException as exc:
                    errors.append((ticker, exc))
                    for pending in futures:
                        pending.cancel()
        if errors:
            ticker, exc = errors[0]
            if isinstance(exc, OfficialSourceUnavailable):
                raise exc
            raise RuntimeError(f"Repair stopped on {ticker}: {type(exc).__name__}: {exc}") from exc
    receipt = dict(generated_at_utc=_utc_now(), source_scope="Binance USD-M perpetuals, official daily Vision ZIP with same-product fapi klines fallback",
                   window_start_utc=START.isoformat(), window_end_exclusive_utc=END.isoformat(),
                   expected_tickers=EXPECTED_AFFECTED, completed_tickers=len(results),
                   validation=dict(rows_per_ticker=EXPECTED_BARS, repaired_hourly_bars_per_ticker=72,
                                   price_columns=PRICE_COLUMNS,
                                   frozen_gap_policy="affected quote caches must have 0 original hourly bars in-window and a price record before and after",
                                   hourly_policy="validate 12 complete official 5m bars per UTC hour and aggregate OHLC from those same official bars; no old-window OHLC comparison",
                                   content_hashes="not computed"), tickers=results)
    if len(results) != EXPECTED_AFFECTED:
        raise ValueError(f"Refusing incomplete receipt: {len(results)} / {EXPECTED_AFFECTED} tickers completed")
    receipt_path = DATA_DIR / "fetch_receipt.json"
    _atomic_write(receipt_path, (json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="census the frozen gap and local exact-name ZIP cache; no network or writes")
    mode.add_argument("--fetch", action="store_true", help="fetch and validate all 43 affected ticker windows")
    parser.add_argument("--workers", type=int, default=1, help="fetch concurrency, from 1 through 4; default 1")
    args = parser.parse_args(argv)
    try:
        result = _preflight() if args.preflight else _fetch_all(args.workers)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

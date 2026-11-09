"""Build an auditable Binance Spot 1h panel for frozen 12H V1.7 events.

This is a geometry/input collector only. It does not trade, score returns, or
substitute UM prices. It fetches exact-name Spot USDT monthly klines from the
official Binance public archive, checks the complete New York Monday-to-key
hourly path, and tests the event's closed UTC 12h OHLC against that Spot week's
own Monday extension. Missing pairs, months, or hourly observations stay
unknown. The cached ZIPs and receipts live under data/research/ so interrupted
runs can resume without changing the frozen candidate set.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from decimal import Decimal
import gzip
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "experiments/active/exp-cloud-close-spot-reclaim-20261109-v1/source_config.json"
NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
HOUR_MS = 3_600_000
TWELVE_HOURS_MS = 12 * HOUR_MS
DAY_MS = 24 * HOUR_MS
S3_ENDPOINT = "https://s3.ap-northeast-1.amazonaws.com/data.binance.vision/"
ARCHIVE_BASE = "https://data.binance.vision/data/spot/monthly/klines"
S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
MONTH_RE = re.compile(r"^(.+)-1h-(\d{4})-(\d{2})\.zip$")

PANEL_COLUMNS = [
    "event_id", "symbol", "spot_symbol", "side", "entry_ms", "parent_week",
    "parent_week_source", "parent_week_check", "parent_signal_source_status",
    "week_start_utc", "monday_end_utc", "parent_week_expiry_utc",
    "key_open_utc", "key_close_utc", "covered", "spot_status", "spot_reclaim",
    "spot_confirms", "spot_h1_expected", "spot_h1_observed", "spot_h1_missing",
    "spot_monday_h1_observed", "spot_key_h1_observed", "spot_zero_trade_hours",
    "spot_monday_zero_trade_hours", "spot_key_zero_trade_hours", "spot_monday_high",
    "spot_monday_low", "spot_key_high", "spot_key_low", "spot_key_close",
    "spot_lower_1618", "spot_upper_1618", "required_months", "missing_months",
    "archive_source", "timestamp_units", "missing_h1_examples", "unknown_reason",
]


def _load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _now() -> str:
    return dt.datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_json_gz(path: Path, value: dict | list) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as handle:
        handle.write(payload)
    _atomic_bytes(path, buffer.getvalue())


def _read_json_gz(path: Path) -> dict | list:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _write_csv_gz(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with gzip.open(temporary, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})
    os.replace(temporary, path)


def _request(url: str, *, retries: int = 4, timeout: int = 45) -> tuple[int, bytes]:
    last_error: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(url, headers={"User-Agent": "fable-trading-research/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return 404, b""
            last_error = error
        except (TimeoutError, OSError, urllib.error.URLError) as error:
            last_error = error
        if attempt + 1 < retries:
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"GET failed after {retries} attempts: {url}: {last_error}")


def _s3_page(prefix: str, *, delimiter: str | None = None,
             continuation_token: str | None = None) -> ET.Element:
    params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
    if delimiter is not None:
        params["delimiter"] = delimiter
    if continuation_token:
        params["continuation-token"] = continuation_token
    url = S3_ENDPOINT + "?" + urllib.parse.urlencode(params)
    status, body = _request(url)
    if status != 200:
        raise RuntimeError(f"S3 list returned HTTP {status}: {url}")
    return ET.fromstring(body)


def _pair_index_path(cache_root: Path) -> Path:
    return cache_root / "index" / "spot_pair_prefixes.json.gz"


def spot_pair_index(cache_root: Path, *, refresh: bool = False) -> dict:
    """Read/cache the official monthly Spot pair prefix index; no candles fetched."""
    path = _pair_index_path(cache_root)
    if path.is_file() and not refresh:
        value = _read_json_gz(path)
        if value.get("schema") == 1 and isinstance(value.get("pairs"), list):
            return value
    prefixes: set[str] = set()
    token = None
    pages = 0
    while True:
        root = _s3_page("data/spot/monthly/klines/", delimiter="/", continuation_token=token)
        pages += 1
        for node in root.findall("s3:CommonPrefixes/s3:Prefix", S3_NS):
            prefix = node.text or ""
            pair = prefix.rstrip("/").split("/")[-1]
            if pair:
                prefixes.add(pair)
        truncated = root.findtext("s3:IsTruncated", default="false", namespaces=S3_NS) == "true"
        token = root.findtext("s3:NextContinuationToken", namespaces=S3_NS)
        if not truncated:
            break
        if not token:
            raise RuntimeError("S3 pair index is truncated without a continuation token")
    value = {
        "schema": 1,
        "fetched_at_utc": _now(),
        "source": S3_ENDPOINT + "?list-type=2&prefix=data/spot/monthly/klines/&delimiter=/",
        "pages": pages,
        "pair_prefixes": len(prefixes),
        "pairs": sorted(prefixes),
    }
    _write_json_gz(path, value)
    return value


def _monthly_index_path(cache_root: Path, spot_symbol: str) -> Path:
    return cache_root / "index" / "1h" / f"{spot_symbol}.json.gz"


def spot_month_index(cache_root: Path, spot_symbol: str, *, refresh: bool = False) -> dict:
    """List official 1h monthly ZIP keys for one exact Spot pair."""
    path = _monthly_index_path(cache_root, spot_symbol)
    if path.is_file() and not refresh:
        value = _read_json_gz(path)
        if value.get("schema") == 1 and value.get("spot_symbol") == spot_symbol:
            return value
    prefix = f"data/spot/monthly/klines/{spot_symbol}/1h/"
    token = None
    pages = 0
    months: set[str] = set()
    while True:
        root = _s3_page(prefix, continuation_token=token)
        pages += 1
        for node in root.findall("s3:Contents/s3:Key", S3_NS):
            key = (node.text or "").rsplit("/", 1)[-1]
            match = MONTH_RE.fullmatch(key)
            if match and match.group(1) == spot_symbol:
                year, month = int(match.group(2)), int(match.group(3))
                if 1 <= month <= 12:
                    months.add(f"{year:04d}-{month:02d}")
        truncated = root.findtext("s3:IsTruncated", default="false", namespaces=S3_NS) == "true"
        token = root.findtext("s3:NextContinuationToken", namespaces=S3_NS)
        if not truncated:
            break
        if not token:
            raise RuntimeError(f"S3 month index truncated without token: {spot_symbol}")
    value = {
        "schema": 1,
        "fetched_at_utc": _now(),
        "spot_symbol": spot_symbol,
        "source_prefix": prefix,
        "pages": pages,
        "months": sorted(months),
    }
    _write_json_gz(path, value)
    return value


def _month_next(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _month_keys_between(start_ms: int, end_ms: int) -> list[str]:
    """Month keys intersecting the half-open UTC interval [start_ms, end_ms)."""
    if end_ms <= start_ms:
        return []
    start = dt.datetime.fromtimestamp(start_ms / 1000, UTC)
    last = dt.datetime.fromtimestamp((end_ms - 1) / 1000, UTC)
    year, month = start.year, start.month
    result = []
    while (year, month) <= (last.year, last.month):
        result.append(f"{year:04d}-{month:02d}")
        year, month = _month_next(year, month)
    return result


def _monday_for_key(entry_ms: int) -> tuple[str, int, int, int]:
    """Return NY Monday key, Monday range, and original weekly expiry.

    The original signal assigns its NY week from the 12h key bar's open time.
    ``entry_ms`` is that bar's close / coincident H1 open, so its open is 12h earlier.
    """
    key_open = dt.datetime.fromtimestamp((entry_ms - TWELVE_HOURS_MS) / 1000, UTC)
    local = key_open.astimezone(NY)
    monday_date = local.date() - dt.timedelta(days=local.weekday())
    monday_local = dt.datetime.combine(monday_date, dt.time(0), NY)
    tuesday_local = monday_local + dt.timedelta(days=1)
    expiry_local = monday_local + dt.timedelta(days=7)
    return (
        monday_date.isoformat(),
        int(monday_local.astimezone(UTC).timestamp() * 1000),
        int(tuesday_local.astimezone(UTC).timestamp() * 1000),
        int(expiry_local.astimezone(UTC).timestamp() * 1000),
    )


def _load_candidates(config: dict) -> list[dict]:
    path = ROOT / config["candidate_file"]
    events: list[dict] = []
    seen: set[str] = set()
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"event_id", "symbol", "entry_ms"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"Candidate input lacks columns: {required - set(reader.fieldnames or [])}")
        for row in reader:
            event_id = row["event_id"]
            if event_id in seen:
                raise ValueError(f"Duplicate frozen event_id: {event_id}")
            seen.add(event_id)
            parts = event_id.split("|")
            if len(parts) != 4 or parts[0] != "12H" or not parts[1].endswith("USDT"):
                raise ValueError(f"Invalid frozen 12H event id: {event_id}")
            base_symbol = parts[1][:-4]
            timestamp_ns, side = int(parts[2]), int(parts[3])
            entry_ms = int(row["entry_ms"])
            if side not in (-1, 1) or timestamp_ns % 1_000_000:
                raise ValueError(f"Invalid close timestamp or side in {event_id}")
            if timestamp_ns // 1_000_000 != entry_ms or row["symbol"] != base_symbol:
                raise ValueError(f"Candidate row and event id disagree: {event_id}")
            if (entry_ms - TWELVE_HOURS_MS) % TWELVE_HOURS_MS:
                raise ValueError(f"Frozen 12H close is off UTC grid: {event_id}")
            parent_week, week_start_ms, monday_end_ms, week_expiry_ms = _monday_for_key(entry_ms)
            events.append({
                "event_id": event_id,
                "symbol": base_symbol,
                "spot_symbol": base_symbol + "USDT",
                "side": side,
                "entry_ms": entry_ms,
                "parent_week": parent_week,
                "parent_week_source": "event_key_reconstruction",
                "parent_week_check": "source_signal_unchecked",
                "week_start_ms": week_start_ms,
                "monday_end_ms": monday_end_ms,
                "week_expiry_ms": week_expiry_ms,
                "key_open_ms": entry_ms - TWELVE_HOURS_MS,
                "source_record_status": "pending",
                "source_week_mismatch": False,
                "required_months": _month_keys_between(week_start_ms, entry_ms),
            })
    return events


def _attach_parent_weeks(events: list[dict], config: dict) -> dict:
    """Use frozen signal week where present and verify its key-open NY-week rule.

    Some supplemental/delisted candidate events have no symbol signals file or
    row. For those, the original ``event_id`` contains the exact 12H close and
    side; the week is reconstructed from key_open=close-12h using the same NY
    Monday assignment. Such rows are marked explicitly in the output.
    """
    by_symbol: dict[str, list[dict]] = {}
    for event in events:
        by_symbol.setdefault(event["symbol"], []).append(event)
    source_root = ROOT / config["parent_signal_root"]
    stats = {"signal_files_present": 0, "source_week_matches": 0,
             "source_rows_missing": 0, "source_files_missing": 0,
             "week_mismatches": 0, "side_or_time_mismatches": 0}
    for symbol, group in by_symbol.items():
        path = source_root / (symbol + "USDT") / "signals.csv.gz"
        if not path.is_file():
            stats["source_files_missing"] += 1
            for event in group:
                event["source_record_status"] = "missing_source_file"
            continue
        stats["signal_files_present"] += 1
        wanted = {event["event_id"]: event for event in group}
        found: set[str] = set()
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"tf", "signal_id", "close_ns", "side", "week"}.issubset(reader.fieldnames or []):
                raise ValueError(f"Unexpected frozen signal schema: {path}")
            for row in reader:
                event_id = row["signal_id"]
                if row["tf"] != "12H" or event_id not in wanted:
                    continue
                if event_id in found:
                    raise ValueError(f"Duplicate source signal id {event_id} in {path}")
                found.add(event_id)
                event = wanted[event_id]
                if int(row["close_ns"]) // 1_000_000 != event["entry_ms"] or int(row["side"]) != event["side"]:
                    event["source_record_status"] = "source_time_or_side_mismatch"
                    stats["side_or_time_mismatches"] += 1
                    continue
                source_week = str(row["week"])
                if source_week != event["parent_week"]:
                    event["parent_week"] = source_week
                    event["parent_week_source"] = "frozen_signal_file"
                    event["parent_week_check"] = "mismatch_vs_key_open"
                    event["source_week_mismatch"] = True
                    parsed = dt.date.fromisoformat(source_week)
                    monday_local = dt.datetime.combine(parsed, dt.time(0), NY)
                    event["week_start_ms"] = int(monday_local.astimezone(UTC).timestamp() * 1000)
                    event["monday_end_ms"] = event["week_start_ms"] + DAY_MS
                    event["week_expiry_ms"] = int((monday_local + dt.timedelta(days=7)).astimezone(UTC).timestamp() * 1000)
                    event["required_months"] = _month_keys_between(event["week_start_ms"], event["entry_ms"])
                    event["source_record_status"] = "source_week_mismatch"
                    stats["week_mismatches"] += 1
                else:
                    event["parent_week_source"] = "frozen_signal_file"
                    event["parent_week_check"] = "matched_key_open"
                    event["source_record_status"] = "matched"
                    stats["source_week_matches"] += 1
        for event in group:
            if event["event_id"] not in found and event["source_record_status"] == "pending":
                event["source_record_status"] = "missing_signal_row"
                stats["source_rows_missing"] += 1
            elif event["source_record_status"] == "source_time_or_side_mismatch":
                event["parent_week_check"] = "source_time_or_side_mismatch"
    return stats


def _read_archive(path: Path, spot_symbol: str, month: str) -> tuple[dict[int, dict], dict]:
    """Parse a cached official 1h ZIP; timestamps are normalized to milliseconds."""
    records: dict[int, dict] = {}
    year, mon = map(int, month.split("-"))
    timestamp_units: set[str] = set()
    rows = zero_trade = 0
    first = last = None
    error = ""
    try:
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"expected one CSV member, got {members}")
            with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding="utf-8", newline="") as text:
                for line_no, row in enumerate(csv.reader(text), 1):
                    if len(row) < 12:
                        raise ValueError(f"row {line_no} has {len(row)} fields")
                    open_raw, close_raw = int(row[0]), int(row[6])
                    unit = "us" if open_raw >= 100_000_000_000_000 else "ms"
                    timestamp_units.add(unit)
                    if unit == "us":
                        open_ms, close_ms = open_raw // 1000, close_raw // 1000
                    else:
                        open_ms, close_ms = open_raw, close_raw
                    if close_ms != open_ms + HOUR_MS - 1:
                        raise ValueError(f"row {line_no} close_time does not match 1h interval")
                    stamp = dt.datetime.fromtimestamp(open_ms // 1000, UTC)
                    if open_ms % 1000 or stamp.minute or stamp.second or stamp.strftime("%Y-%m") != month:
                        raise ValueError(f"row {line_no} has an off-grid or wrong-month open time")
                    o, h, low, c, volume = map(Decimal, row[1:6])
                    trades = int(row[8])
                    values = (o, h, low, c, volume)
                    if not all(v.is_finite() for v in values) or min(o, h, low, c) <= 0:
                        raise ValueError(f"row {line_no} has non-finite or non-positive OHLC")
                    if low > min(o, c) or h < max(o, c) or low > h or volume < 0 or trades < 0:
                        raise ValueError(f"row {line_no} violates OHLCV/trade-count bounds")
                    zero = trades == 0 or volume == 0
                    if (trades == 0) != (volume == 0):
                        raise ValueError(f"row {line_no} has inconsistent zero-volume/trade count")
                    if open_ms in records:
                        raise ValueError(f"duplicate 1h open timestamp {open_ms}")
                    records[open_ms] = {
                        "open": o, "high": h, "low": low, "close": c,
                        "volume": volume, "trades": trades, "zero_trade": zero,
                    }
                    rows += 1
                    zero_trade += int(zero)
                    first = open_ms if first is None else first
                    last = open_ms
        if len(timestamp_units) != 1:
            raise ValueError(f"mixed timestamp units in archive: {sorted(timestamp_units)}")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        records.clear()
    inspection = {
        "rows": rows,
        "first_open_ms": first,
        "last_open_ms": last,
        "timestamp_unit": next(iter(timestamp_units)) if len(timestamp_units) == 1 else "mixed_or_unknown",
        "zero_trade_rows": zero_trade,
        "error": error,
    }
    receipt_path = path.with_name(path.name + ".receipt.json.gz")
    receipt = {}
    if receipt_path.is_file():
        try:
            receipt = _read_json_gz(receipt_path)
        except Exception:
            receipt = {}
    receipt.setdefault("schema", 1)
    receipt.setdefault("spot_symbol", spot_symbol)
    receipt.setdefault("month", month)
    receipt.setdefault("archive_url", _archive_url(spot_symbol, month))
    receipt["inspection"] = inspection
    _write_json_gz(receipt_path, receipt)
    return records, inspection


def _archive_url(spot_symbol: str, month: str) -> str:
    return f"{ARCHIVE_BASE}/{spot_symbol}/1h/{spot_symbol}-1h-{month}.zip"


def _validate_zip_bytes(payload: bytes, spot_symbol: str, month: str) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            expected = f"{spot_symbol}-1h-{month}.csv"
            if members != [expected]:
                return False, f"unexpected ZIP members: {members}"
            bad = archive.testzip()
            if bad is not None:
                return False, f"bad ZIP member: {bad}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def _download_archive(cache_root: Path, spot_symbol: str, month: str) -> dict:
    filename = f"{spot_symbol}-1h-{month}.zip"
    path = cache_root / "raw" / spot_symbol / "1h" / filename
    receipt_path = path.with_name(path.name + ".receipt.json.gz")
    if path.is_file() and receipt_path.is_file():
        try:
            receipt = _read_json_gz(receipt_path)
            if receipt.get("compressed_bytes") == path.stat().st_size and receipt.get("archive_status") == "ok":
                return {"spot_symbol": spot_symbol, "month": month, "status": "cached", "path": str(path)}
        except Exception:
            pass
    if path.is_file():
        payload = path.read_bytes()
        valid, reason = _validate_zip_bytes(payload, spot_symbol, month)
        if valid:
            receipt = {
                "schema": 1, "spot_symbol": spot_symbol, "month": month,
                "archive_url": _archive_url(spot_symbol, month), "archive_status": "ok",
                "compressed_bytes": len(payload), "cached_file_revalidated_at_utc": _now(),
            }
            _write_json_gz(receipt_path, receipt)
            return {"spot_symbol": spot_symbol, "month": month, "status": "cached_revalidated", "path": str(path)}
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    status, payload = _request(_archive_url(spot_symbol, month))
    if status == 404:
        receipt = {
            "schema": 1, "spot_symbol": spot_symbol, "month": month,
            "archive_url": _archive_url(spot_symbol, month), "archive_status": "http_404",
            "checked_at_utc": _now(),
        }
        _write_json_gz(receipt_path, receipt)
        return {"spot_symbol": spot_symbol, "month": month, "status": "http_404", "path": ""}
    valid, reason = _validate_zip_bytes(payload, spot_symbol, month)
    if not valid:
        receipt = {
            "schema": 1, "spot_symbol": spot_symbol, "month": month,
            "archive_url": _archive_url(spot_symbol, month), "archive_status": "invalid_zip",
            "compressed_bytes": len(payload), "error": reason, "checked_at_utc": _now(),
        }
        _write_json_gz(receipt_path, receipt)
        return {"spot_symbol": spot_symbol, "month": month, "status": "invalid_zip", "path": ""}
    _atomic_bytes(path, payload)
    _write_json_gz(receipt_path, {
        "schema": 1, "spot_symbol": spot_symbol, "month": month,
        "archive_url": _archive_url(spot_symbol, month), "archive_status": "ok",
        "compressed_bytes": len(payload), "downloaded_at_utc": _now(),
    })
    return {"spot_symbol": spot_symbol, "month": month, "status": "downloaded", "path": str(path)}


def _utc_iso(epoch_ms: int) -> str:
    return dt.datetime.fromtimestamp(epoch_ms / 1000, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _base_event_row(event: dict) -> dict:
    expected_h1 = max(0, (event["entry_ms"] - event["week_start_ms"]) // HOUR_MS)
    return {
        "event_id": event["event_id"], "symbol": event["symbol"],
        "spot_symbol": event["spot_symbol"], "side": event["side"],
        "entry_ms": event["entry_ms"], "parent_week": event["parent_week"],
        "parent_week_source": event["parent_week_source"],
        "parent_week_check": event["parent_week_check"],
        "parent_signal_source_status": event["source_record_status"],
        "week_start_utc": _utc_iso(event["week_start_ms"]),
        "monday_end_utc": _utc_iso(event["monday_end_ms"]),
        "parent_week_expiry_utc": _utc_iso(event["week_expiry_ms"]),
        "key_open_utc": _utc_iso(event["key_open_ms"]),
        "key_close_utc": _utc_iso(event["entry_ms"]),
        "covered": False, "spot_status": "unknown_pending", "spot_reclaim": None,
        "spot_confirms": None, "spot_h1_expected": expected_h1,
        "spot_h1_observed": 0, "spot_h1_missing": expected_h1,
        "spot_monday_h1_observed": 0, "spot_key_h1_observed": 0,
        "spot_zero_trade_hours": 0, "spot_monday_zero_trade_hours": 0,
        "spot_key_zero_trade_hours": 0,
        "spot_monday_high": None, "spot_monday_low": None,
        "spot_key_high": None, "spot_key_low": None, "spot_key_close": None,
        "spot_lower_1618": None, "spot_upper_1618": None,
        "required_months": ";".join(event["required_months"]),
        "missing_months": "", "archive_source": "Binance public Spot monthly klines, 1h",
        "timestamp_units": "", "missing_h1_examples": "", "unknown_reason": "",
    }


def _mark_unknown(row: dict, status: str, reason: str) -> dict:
    row["spot_status"] = status
    row["unknown_reason"] = reason
    row["spot_reclaim"] = None
    row["spot_confirms"] = None
    return row


def _event_geometry(event: dict, bars: dict[int, dict], inspection: dict,
                    missing_months: list[str]) -> dict:
    row = _base_event_row(event)
    row["missing_months"] = ";".join(missing_months)
    row["timestamp_units"] = inspection.get("timestamp_unit", "")
    if event["source_week_mismatch"]:
        return _mark_unknown(row, "unknown_parent_week_mismatch", "frozen signal week differs from key-open NY Monday")
    if event["source_record_status"] in {"source_time_or_side_mismatch"}:
        return _mark_unknown(row, "unknown_parent_signal_mismatch", event["source_record_status"])
    if event["key_open_ms"] < event["monday_end_ms"]:
        return _mark_unknown(row, "unknown_key_not_wholly_after_monday", "12H key bar opens before the NY Monday range is complete")
    if event["entry_ms"] > event["week_expiry_ms"]:
        return _mark_unknown(row, "unknown_key_after_parent_week", "12H key bar closes after the original NY week expires")
    if event["entry_ms"] % HOUR_MS:
        return _mark_unknown(row, "unknown_key_off_h1_grid", "12H close is not an exact UTC H1 boundary")
    expected_times = list(range(event["week_start_ms"], event["entry_ms"], HOUR_MS))
    row["spot_h1_expected"] = len(expected_times)
    observed_times = [stamp for stamp in expected_times if stamp in bars]
    row["spot_h1_observed"] = len(observed_times)
    row["spot_h1_missing"] = len(expected_times) - len(observed_times)
    row["spot_monday_h1_observed"] = sum(event["week_start_ms"] <= t < event["monday_end_ms"] for t in observed_times)
    row["spot_key_h1_observed"] = sum(event["key_open_ms"] <= t < event["entry_ms"] for t in observed_times)
    if missing_months:
        absent = [t for t in expected_times if t not in bars]
        row["missing_h1_examples"] = ";".join(_utc_iso(t) for t in absent[:5])
        return _mark_unknown(row, "unknown_no_month_archive", "one or more required Spot 1h monthly ZIPs are absent from S3 index")
    if row["spot_h1_missing"]:
        absent = [t for t in expected_times if t not in bars]
        row["missing_h1_examples"] = ";".join(_utc_iso(t) for t in absent[:5])
        return _mark_unknown(row, "unknown_intra_month_h1_gap", "required UTC H1 opens are absent inside archived month data")
    series = [bars[t] for t in expected_times]
    monday = series[:24]
    key_start = event["entry_ms"] - TWELVE_HOURS_MS
    key = [bars[t] for t in range(key_start, event["entry_ms"], HOUR_MS)]
    if len(monday) != 24 or len(key) != 12:
        return _mark_unknown(row, "unknown_incomplete_window", "Monday or confirmation window is not the required 24/12 H1 bars")
    row["spot_zero_trade_hours"] = sum(x["zero_trade"] for x in series)
    row["spot_monday_zero_trade_hours"] = sum(x["zero_trade"] for x in monday)
    row["spot_key_zero_trade_hours"] = sum(x["zero_trade"] for x in key)
    row["spot_monday_high"] = max(x["high"] for x in monday)
    row["spot_monday_low"] = min(x["low"] for x in monday)
    row["spot_key_high"] = max(x["high"] for x in key)
    row["spot_key_low"] = min(x["low"] for x in key)
    row["spot_key_close"] = key[-1]["close"]
    width = row["spot_monday_high"] - row["spot_monday_low"]
    if not width.is_finite() or width <= 0:
        return _mark_unknown(row, "unknown_zero_or_invalid_monday_range", "Spot Monday high-low range is not positive")
    lower = row["spot_monday_low"] - Decimal("0.618") * width
    upper = row["spot_monday_high"] + Decimal("0.618") * width
    row["spot_lower_1618"] = lower
    row["spot_upper_1618"] = upper
    if event["side"] == 1:
        confirms = row["spot_key_low"] < lower < row["spot_key_close"] < upper
    else:
        confirms = row["spot_key_high"] > upper > row["spot_key_close"] > lower
    row["covered"] = True
    row["spot_reclaim"] = bool(confirms)
    row["spot_confirms"] = bool(confirms)
    row["spot_status"] = "observed_zero_trade_hours" if row["spot_zero_trade_hours"] else "observed"
    return row


def _events_to_process(config: dict, smoke: bool) -> tuple[list[dict], dict]:
    events = _load_candidates(config)
    source_stats = _attach_parent_weeks(events, config)
    if not smoke:
        return events, source_stats
    ada = sorted((e for e in events if e["symbol"] == "ADA" and e["entry_ms"] < 1_735_689_600_000),
                 key=lambda e: e["entry_ms"])
    btc = sorted((e for e in events if e["symbol"] == "BTC" and e["entry_ms"] >= 1_735_689_600_000),
                 key=lambda e: e["entry_ms"])
    if not ada or not btc:
        raise ValueError("Smoke set needs an ADA pre-2025 and BTC 2025+ candidate")
    return [ada[0], btc[0]], source_stats


def _progress(label: str, done: int, total: int) -> None:
    if total and (done == total or done % 100 == 0):
        print(f"{label}: {done}/{total}", flush=True)


def run(*, smoke: bool = False, refresh_index: bool = False, workers: int | None = None) -> dict:
    config = _load_config()
    cache_root = ROOT / config["cache_root"]
    workers = int(workers or config.get("workers", 4))
    if not 1 <= workers <= 4:
        raise ValueError("workers must be in the configured safe range 1..4")
    events, week_stats = _events_to_process(config, smoke)
    symbols = sorted({e["symbol"] for e in events})
    pair_index = spot_pair_index(cache_root, refresh=refresh_index)
    pair_names = set(pair_index["pairs"])
    pair_symbols = {base: base + "USDT" for base in symbols if base + "USDT" in pair_names}
    print(f"Candidates={len(events)} symbols={len(symbols)} exact_spot_pairs={len(pair_symbols)} workers={workers}", flush=True)

    month_indexes: dict[str, dict] = {}
    index_errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(spot_month_index, cache_root, spot, refresh=refresh_index): spot
                   for spot in pair_symbols.values()}
        for done, future in enumerate(as_completed(futures), 1):
            spot = futures[future]
            try:
                month_indexes[spot] = future.result()
            except Exception as exc:
                index_errors[spot] = f"{type(exc).__name__}: {exc}"
            _progress("Indexed Spot 1h pair months", done, len(futures))

    required_by_spot: dict[str, set[str]] = {}
    for event in events:
        spot = event["spot_symbol"]
        if spot in pair_symbols.values():
            required_by_spot.setdefault(spot, set()).update(event["required_months"])
    archive_tasks: list[tuple[str, str]] = []
    no_month_by_spot: dict[str, set[str]] = {}
    for spot, months in required_by_spot.items():
        available = set(month_indexes.get(spot, {}).get("months", []))
        no_month_by_spot[spot] = months - available
        archive_tasks.extend((spot, month) for month in sorted(months & available))

    archive_status: dict[tuple[str, str], dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download_archive, cache_root, spot, month): (spot, month)
                   for spot, month in archive_tasks}
        total = len(futures)
        for done, future in enumerate(as_completed(futures), 1):
            key = futures[future]
            try:
                archive_status[key] = future.result()
            except Exception as exc:
                archive_status[key] = {"status": "download_error", "error": f"{type(exc).__name__}: {exc}"}
            _progress("Fetched/reused official Spot 1h ZIPs", done, total)

    events_by_symbol: dict[str, list[dict]] = {}
    for event in events:
        events_by_symbol.setdefault(event["symbol"], []).append(event)
    rows: list[dict] = []
    archive_errors: dict[tuple[str, str], str] = {}
    for done, symbol in enumerate(sorted(events_by_symbol), 1):
        spot = symbol + "USDT"
        group = events_by_symbol[symbol]
        bars: dict[int, dict] = {}
        units: set[str] = set()
        inspections: list[dict] = []
        if spot in pair_symbols.values() and spot not in index_errors:
            available = set(month_indexes.get(spot, {}).get("months", []))
            months_needed = sorted({m for e in group for m in e["required_months"] if m in available})
            for month in months_needed:
                result = archive_status.get((spot, month), {})
                if result.get("status") not in {"downloaded", "cached", "cached_revalidated"}:
                    archive_errors[(spot, month)] = result.get("error", result.get("status", "download_missing"))
                    continue
                archive_path = cache_root / "raw" / spot / "1h" / f"{spot}-1h-{month}.zip"
                records, inspection = _read_archive(archive_path, spot, month)
                inspections.append(inspection)
                if inspection.get("timestamp_unit") not in {"mixed_or_unknown", ""}:
                    units.add(inspection["timestamp_unit"])
                if inspection.get("error"):
                    archive_errors[(spot, month)] = inspection["error"]
                    continue
                for stamp, bar in records.items():
                    if stamp in bars:
                        archive_errors[(spot, month)] = f"duplicate timestamp across monthly archives: {stamp}"
                        bars.pop(stamp, None)
                        continue
                    bars[stamp] = bar
        for event in group:
            row = _base_event_row(event)
            row["timestamp_units"] = ";".join(sorted(units))
            if spot not in pair_names:
                row = _mark_unknown(row, "unknown_no_exact_spot_pair", "no exact BASEUSDT Spot monthly kline prefix; aliases are not inferred")
            elif spot in index_errors:
                row = _mark_unknown(row, "unknown_spot_index_error", index_errors[spot])
            else:
                missing = sorted(set(event["required_months"]) - set(month_indexes.get(spot, {}).get("months", [])))
                unavailable = [m for m in event["required_months"] if (spot, m) in archive_errors]
                if unavailable:
                    row = _mark_unknown(row, "unknown_archive_download_or_parse", ";".join(f"{m}:{archive_errors[(spot,m)]}" for m in unavailable))
                else:
                    row = _event_geometry(event, bars, {"timestamp_unit": ";".join(sorted(units))}, missing)
            rows.append(row)
        _progress("Built Spot geometry rows", done, len(events_by_symbol))

    output = ROOT / config["smoke_panel" if smoke else "event_panel"]
    _write_csv_gz(output, rows, PANEL_COLUMNS)
    month_index_events = sum(
        1 for event in events
        if event["spot_symbol"] in month_indexes
        and not (set(event["required_months"]) - set(month_indexes[event["spot_symbol"]].get("months", [])))
    )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["spot_status"]] = counts.get(row["spot_status"], 0) + 1
    summary = {
        "schema": 1,
        "generated_at_utc": _now(),
        "candidate_file": config["candidate_file"],
        "parent_signal_root": config["parent_signal_root"],
        "spot_source": "official Binance Spot monthly 1h klines",
        "smoke": smoke,
        "events": len(events),
        "symbols": len(symbols),
        "exact_spot_pair_symbols": len(pair_symbols),
        "event_month_index_complete": month_index_events,
        "source_week_checks": week_stats,
        "status_counts": counts,
        "required_archive_symbol_months": len(archive_tasks),
        "archives_downloaded": sum(value.get("status") == "downloaded" for value in archive_status.values()),
        "archives_reused": sum(value.get("status", "").startswith("cached") for value in archive_status.values()),
        "archive_errors": len(archive_errors) + len(index_errors),
        "panel_file": str(output),
        "cache_root": str(cache_root),
        "decision_definition": config["geometry"],
    }
    summary_path = ROOT / config["smoke_summary" if smoke else "summary"]
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_tmp = summary_path.with_name(summary_path.name + ".part")
    summary_tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(summary_tmp, summary_path)
    print(json.dumps({"events": len(rows), "statuses": counts, "panel": str(output),
                      "archives": len(archive_tasks), "summary": str(summary_path)}, ensure_ascii=False), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true", help="fetch one ADA pre-2025 and one BTC 2025+ event")
    mode.add_argument("--run", action="store_true", help="fetch/build all frozen candidates")
    parser.add_argument("--workers", type=int, default=None, help="parallelism, capped at 4")
    parser.add_argument("--refresh-index", action="store_true", help="refresh cached official S3 indexes")
    args = parser.parse_args()
    run(smoke=args.smoke, refresh_index=args.refresh_index, workers=args.workers)


if __name__ == "__main__":
    main()

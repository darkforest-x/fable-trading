"""Backfill missing 5m candles for incomplete intraday retest events.

Only ``coverage == incomplete_5m`` rows in ``OUT/events.csv.gz`` drive archive
requests.  The cache keeps full Binance monthly archives when available; when a
monthly archive is unavailable, it stitches only required UTC daily archives
and records the exact candle timestamps in a sidecar receipt.
"""

import argparse
import csv
from datetime import date, datetime, time, timedelta, timezone
import gzip
import io
import json
from pathlib import Path
import re
import urllib.error
import urllib.request
import zipfile
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = ROOT / "data/research/alt-isolated-12h-wicks-20261111/expanded12/all_replay_v1"
OUT = BASE / "intraday_retest"
DEFAULT_EVENTS = OUT / "events.csv.gz"
DEFAULT_CACHE = OUT / "binance_monthly"

FIVE_MIN_MS = 5 * 60_000
HOUR_MS = 60 * 60_000
DAY_MS = 24 * HOUR_MS
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
BINANCE_BASE = "https://data.binance.vision/data/futures/um"
DEFAULT_HEADER = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def parse_month(value):
    match = re.fullmatch(r"\s*(\d{4})-(\d{2})(?:-\d{2})?\s*", str(value or ""))
    if match is None:
        raise ValueError(f"invalid event month: {value!r}")
    year, month = map(int, match.groups())
    if not 1 <= month <= 12:
        raise ValueError(f"invalid event month: {value!r}")
    return year, month


def parse_ms(value):
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty touch_ms")
    try:
        return int(raw)
    except ValueError:
        return int(float(raw))


def month_start_ms(month):
    year, number = parse_month(month)
    return int(datetime(year, number, 1, tzinfo=UTC).timestamp() * 1000)


def next_ny_month_start_ms(month):
    year, number = parse_month(month)
    if number == 12:
        year, number = year + 1, 1
    else:
        number += 1
    return int(datetime(year, number, 1, 0, 0, tzinfo=NY).timestamp() * 1000)


def utc_month(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms // 1000, tz=UTC).strftime("%Y-%m")


def utc_day(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms // 1000, tz=UTC).strftime("%Y-%m-%d")


def expected_month_open_times(month):
    start = month_start_ms(month)
    year, number = parse_month(month)
    if number == 12:
        next_start = month_start_ms(f"{year + 1}-01")
    else:
        next_start = month_start_ms(f"{year}-{number + 1:02d}")
    return set(range(start, next_start, FIVE_MIN_MS))


def month_window(touch_ms, event_month):
    """Return the event's half-open 5m interval [touch_ms, end_ms).

    The study rule is touch + 1h + 24h, capped at the next New York month
    boundary, then extended by another 24h. ``event_month`` is the study's NY
    calendar month; data packages themselves are grouped by UTC month.
    """
    if touch_ms % FIVE_MIN_MS:
        raise ValueError(f"touch_ms is not aligned to a 5m open: {touch_ms}")
    known_ms = touch_ms + HOUR_MS
    stop_ms = min(known_ms + DAY_MS, next_ny_month_start_ms(event_month))
    end_ms = stop_ms + DAY_MS
    return touch_ms, end_ms


def read_required_events(events_path):
    required = {}
    event_count = 0
    with gzip.open(events_path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"coverage", "touch_ms", "symbol"}.issubset(reader.fieldnames):
            raise ValueError("events.csv.gz must include symbol, touch_ms, and coverage columns")
        for line_number, row in enumerate(reader, start=2):
            if (row.get("coverage") or "").strip() != "incomplete_5m":
                continue
            symbol = (row.get("symbol") or "").strip().upper()
            if not re.fullmatch(r"[A-Z0-9]+", symbol):
                raise ValueError(f"invalid symbol at events row {line_number}: {symbol!r}")
            touch_ms = parse_ms(row.get("touch_ms"))
            event_month = (row.get("month") or "").strip()
            if not event_month:
                event_month = datetime.fromtimestamp(touch_ms // 1000, tz=NY).strftime("%Y-%m")
            else:
                parse_month(event_month)
            start_ms, end_ms = month_window(touch_ms, event_month)
            for open_ms in range(start_ms, end_ms, FIVE_MIN_MS):
                month = utc_month(open_ms)
                required.setdefault((symbol, month), set()).add(open_ms)
            event_count += 1
    return required, event_count


def monthly_url(symbol, month):
    return (
        f"{BINANCE_BASE}/monthly/klines/{symbol}/5m/"
        f"{symbol}-5m-{month}.zip"
    )


def daily_url(symbol, day):
    return (
        f"{BINANCE_BASE}/daily/klines/{symbol}/5m/"
        f"{symbol}-5m-{day}.zip"
    )


def month_cache_path(cache_root, symbol, month):
    return cache_root / symbol / f"{symbol}-5m-{month}.zip"


def daily_cache_path(cache_root, symbol, day):
    return cache_root / symbol / f"{symbol}-5m-{day}.zip"


def receipt_path(month_path):
    return month_path.with_name(month_path.name + ".receipt.json")


def parse_zip(blob):
    """Return (header, rows-by-open-time) from one Binance Kline ZIP."""
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in ZIP, found {len(names)}")
        with archive.open(names[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            header = None
            rows = {}
            for row in reader:
                if not row:
                    continue
                try:
                    open_ms = int(row[0])
                except (ValueError, IndexError):
                    if header is None:
                        header = row
                        continue
                    raise ValueError(f"non-numeric kline open_time: {row[:1]!r}")
                if len(row) < 6:
                    raise ValueError(f"malformed kline row: {row!r}")
                if open_ms % FIVE_MIN_MS:
                    raise ValueError(f"unaligned kline open_time: {open_ms}")
                rows[open_ms] = row
    if header is None:
        header = DEFAULT_HEADER
    if rows and any(len(row) != len(header) for row in rows.values()):
        raise ValueError("kline CSV header and row widths differ")
    return header, rows


def read_zip_path(path):
    return parse_zip(path.read_bytes())


def fetch_zip(url, timeout=30):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "fable-trading historical-kline backfill"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None, {"url": url, "http_status": response.status, "error": "unexpected HTTP status"}
            blob = response.read()
        # Parse before reporting success so malformed or non-ZIP responses are
        # treated as failed archive attempts and can fall back to daily files.
        parse_zip(blob)
        return blob, {"url": url, "http_status": 200, "bytes": len(blob)}
    except urllib.error.HTTPError as error:
        return None, {"url": url, "http_status": error.code, "error": str(error.reason)}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, zipfile.BadZipFile) as error:
        return None, {"url": url, "http_status": None, "error": f"{type(error).__name__}: {error}"}


def atomic_write_bytes(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def atomic_write_json(path, value):
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_bytes(path, payload)


def make_month_zip(path, symbol, month, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_buffer = io.StringIO(newline="")
    writer = csv.writer(csv_buffer, lineterminator="\n")
    writer.writerow(header)
    for open_ms in sorted(rows):
        writer.writerow(rows[open_ms])
    temporary = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{symbol}-5m-{month}.csv", csv_buffer.getvalue().encode("utf-8"))
    temporary.replace(path)


def add_month_rows(target, incoming, month):
    start = month_start_ms(month)
    expected = expected_month_open_times(month)
    end = max(expected) + FIVE_MIN_MS if expected else start
    for open_ms, row in incoming.items():
        if start <= open_ms < end:
            target[open_ms] = row


def read_previous_receipt(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get_local_data(cache_root, symbol, month, month_path):
    """Read an existing monthly/partial ZIP and prior daily ZIPs for this month."""
    rows = {}
    header = DEFAULT_HEADER
    local_errors = []
    loaded_month = False
    if month_path.is_file():
        try:
            header, month_rows = read_zip_path(month_path)
            add_month_rows(rows, month_rows, month)
            loaded_month = True
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            local_errors.append({"path": str(month_path), "error": f"{type(error).__name__}: {error}"})

    symbol_dir = cache_root / symbol
    cached_daily_dates = set()
    daily_pattern = f"{symbol}-5m-{month}-??.zip"
    for daily_path in sorted(symbol_dir.glob(daily_pattern)) if symbol_dir.is_dir() else []:
        day = daily_path.name.removesuffix(".zip").rsplit("-", 1)[-1]
        try:
            daily_header, daily_rows = read_zip_path(daily_path)
            if not rows:
                header = daily_header
            add_month_rows(rows, daily_rows, month)
            cached_daily_dates.add(day)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            local_errors.append({"path": str(daily_path), "error": f"{type(error).__name__}: {error}"})
    return rows, header, loaded_month, cached_daily_dates, local_errors


def day_needed_times(needed_times):
    by_day = {}
    for open_ms in needed_times:
        by_day.setdefault(utc_day(open_ms), set()).add(open_ms)
    return by_day


def cache_daily_blob(path, blob):
    atomic_write_bytes(path, blob)


def result_is_full_month(rows, month):
    expected = expected_month_open_times(month)
    return bool(expected) and set(rows) == expected


def process_month(cache_root, symbol, month, needed_times):
    month_path = month_cache_path(cache_root, symbol, month)
    previous_receipt = read_previous_receipt(receipt_path(month_path))
    rows, header, loaded_month, cached_daily_dates, local_errors = get_local_data(
        cache_root, symbol, month, month_path
    )
    attempts = []
    successful_urls = []
    downloaded_month = False
    downloaded_days = []

    missing = set(needed_times) - set(rows)
    # A local month ZIP or previously stitched daily data already covers these
    # exact event bars; avoid any remote request for this symbol-month.
    if missing and not loaded_month and not cached_daily_dates:
        url = monthly_url(symbol, month)
        blob, result = fetch_zip(url)
        attempts.append({"kind": "monthly", "scope": month, **result})
        if blob is not None:
            try:
                downloaded_header, monthly_rows = parse_zip(blob)
                rows.update(monthly_rows)
                header = downloaded_header
                downloaded_month = True
                successful_urls.append(url)
            except (ValueError, zipfile.BadZipFile) as error:
                attempts[-1]["error"] = f"invalid ZIP content: {type(error).__name__}: {error}"
                attempts[-1]["http_status"] = 200
                attempts[-1].pop("bytes", None)
        missing = set(needed_times) - set(rows)

    needed_by_day = day_needed_times(needed_times)
    day_cache_dir = cache_root / symbol
    for day in sorted(needed_by_day):
        still_missing = needed_by_day[day] - set(rows)
        if not still_missing:
            continue
        if day in cached_daily_dates:
            # The stored daily object is valid but has no additional rows for
            # the requested timestamps; leave that fact for the receipt.
            continue
        path = daily_cache_path(cache_root, symbol, day)
        if path.is_file():
            try:
                daily_header, daily_rows = read_zip_path(path)
                if not rows:
                    header = daily_header
                add_month_rows(rows, daily_rows, month)
                cached_daily_dates.add(day)
                continue
            except (OSError, ValueError, zipfile.BadZipFile) as error:
                local_errors.append({"path": str(path), "error": f"{type(error).__name__}: {error}"})

        url = daily_url(symbol, day)
        blob, result = fetch_zip(url)
        attempts.append({"kind": "daily", "scope": day, **result})
        if blob is None:
            continue
        try:
            daily_header, daily_rows = parse_zip(blob)
            cache_daily_blob(path, blob)
            if not rows:
                header = daily_header
            add_month_rows(rows, daily_rows, month)
            downloaded_days.append(day)
            successful_urls.append(url)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            attempts[-1]["error"] = f"invalid ZIP content: {type(error).__name__}: {error}"
            attempts[-1]["http_status"] = 200
            attempts[-1].pop("bytes", None)

    missing = sorted(set(needed_times) - set(rows))
    month_complete = result_is_full_month(rows, month)
    month_data_url = monthly_url(symbol, month)
    source_urls = list(previous_receipt.get("source_urls", []))
    for url in successful_urls:
        if url not in source_urls:
            source_urls.append(url)
    if downloaded_month and downloaded_days:
        source_kind = "monthly_plus_daily"
    elif downloaded_month:
        source_kind = "binance_monthly"
    elif downloaded_days or cached_daily_dates:
        source_kind = "daily_stitch"
    elif loaded_month:
        source_kind = previous_receipt.get("source_kind", "local_month_archive")
    elif rows:
        source_kind = "local_partial_archive"
    else:
        source_kind = "unavailable"

    # Keep an exact official monthly ZIP when it contains the entire UTC month.
    # Otherwise write only actual rows, and mark the cache partial in its receipt.
    if downloaded_month and month_complete:
        atomic_write_bytes(month_path, blob)
    elif rows and (downloaded_month or downloaded_days or not month_path.is_file()):
        make_month_zip(month_path, symbol, month, header, rows)

    final_rows = rows
    expected = expected_month_open_times(month)
    covered_days = sorted({utc_day(open_ms) for open_ms in final_rows})
    now = datetime.now(UTC).isoformat(timespec="seconds")
    receipt = {
        "schema_version": 1,
        "symbol": symbol,
        "interval": "5m",
        "utc_month": month,
        "cache_zip": str(month_path),
        "source_kind": source_kind,
        "month_complete": month_complete,
        "expected_month_rows": len(expected),
        "actual_rows": len(final_rows),
        "covered_days": covered_days,
        "open_time_min_ms": min(final_rows) if final_rows else None,
        "open_time_max_ms": max(final_rows) if final_rows else None,
        "required_rows": len(needed_times),
        "required_rows_present": len(needed_times) - len(missing),
        "unavailable_required_open_ms": missing,
        "source_urls": source_urls,
        "attempts": attempts,
        "local_errors": local_errors,
        "updated_at_utc": now,
    }
    if not month_complete:
        # The zip contains observed bars only. This explicit list prevents a
        # daily-stitch cache from being mistaken for a complete month later.
        receipt["actual_open_ms"] = sorted(final_rows)
    atomic_write_json(receipt_path(month_path), receipt)

    unavailable = []
    required_by_day = day_needed_times(needed_times)
    missing_set = set(missing)
    for attempt in attempts:
        if "error" not in attempt:
            continue
        if attempt["kind"] == "monthly":
            resolved = not missing_set
        else:
            unresolved_for_day = required_by_day.get(attempt["scope"], set()) & missing_set
            resolved = not unresolved_for_day
        unavailable.append({
            "symbol": symbol,
            "utc_month": month,
            "kind": attempt["kind"],
            "scope": attempt["scope"],
            "url": attempt["url"],
            "http_status": attempt.get("http_status"),
            "error": attempt["error"],
            "resolved_by_fallback": resolved,
            "checked_at_utc": now,
        })
    return {
        "symbol": symbol,
        "utc_month": month,
        "required_rows": len(needed_times),
        "rows_present": len(needed_times) - len(missing),
        "unavailable_rows": len(missing),
        "month_complete": month_complete,
        "source_kind": source_kind,
        "unavailable_attempts": unavailable,
    }


def update_unavailable_ledger(cache_root, results):
    records = {}
    ledger_path = cache_root / "unavailable.jsonl"
    if ledger_path.is_file():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (record.get("symbol"), record.get("url"))
            records[key] = record
    for result in results:
        for record in result["unavailable_attempts"]:
            key = (record["symbol"], record["url"])
            previous = records.get(key, {})
            record["first_seen_at_utc"] = previous.get("first_seen_at_utc", record["checked_at_utc"])
            records[key] = record
    if not records:
        return
    payload = "".join(
        json.dumps(records[key], ensure_ascii=False, sort_keys=True) + "\n"
        for key in sorted(records)
    ).encode("utf-8")
    atomic_write_bytes(ledger_path, payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args(argv)
    if not args.events.is_file():
        raise SystemExit(f"events file not found; run the main program first: {args.events}")

    required, event_count = read_required_events(args.events)
    if not required:
        print(json.dumps({"incomplete_events": event_count, "symbol_months": 0}, sort_keys=True))
        return

    results = []
    for (symbol, month), timestamps in sorted(required.items()):
        results.append(process_month(args.cache_root, symbol, month, timestamps))
    update_unavailable_ledger(args.cache_root, results)
    summary = {
        "incomplete_events": event_count,
        "symbol_months": len(results),
        "required_rows": sum(item["required_rows"] for item in results),
        "rows_present": sum(item["rows_present"] for item in results),
        "unavailable_rows": sum(item["unavailable_rows"] for item in results),
        "monthly_zips_complete": sum(item["month_complete"] for item in results),
        "unavailable_archive_attempts": sum(len(item["unavailable_attempts"]) for item in results),
        "cache_root": str(args.cache_root),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

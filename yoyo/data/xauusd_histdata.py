"""Read HistData XAU/USD M1 BID quote archives entirely in memory.

Source: https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/XAUUSD
Contract: https://ftpsite.histdata.com/f-a-q/ describes BID OHLC and fixed EST
timestamps without daylight saving. Source calendar timestamps therefore move
forward exactly five hours to UTC, including in summer. This is historical
XAU/USD quote data, not a perpetual/futures instrument or an identified broker's
execution feed. ASK/spread and executable volume are not supplied here.

The reader follows the site's ordinary file_down form, never writes raw bars,
never fills missing minutes, and retains zero-volume rows. Identical UTC+OHLCV
duplicates are counted and removed; conflicting duplicates stop the reader.
``time`` is a UTC
DatetimeIndex of minute opens; ``time_close`` is the exclusive minute boundary.
Calendar archives are selected in fixed EST before filtering [start, end) in
UTC. There are no derived features or future-looking labels in this module.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import io
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Callable

import pandas as pd


BASE_URL = "https://www.histdata.com"
PAGE_BASE = BASE_URL + "/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/xauusd"
EST_FIXED = timezone(timedelta(hours=-5), "EST_fixed")
ONE_MINUTE = pd.Timedelta(minutes=1)
NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]
MAX_ZIP_BYTES = 64 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
SOURCE_CONTRACT = {
    "provider": "HistData.com",
    "symbol": "XAUUSD",
    "product": "XAU/USD historical quote data",
    "execution_venue": "unspecified_by_source",
    "price_side": "BID",
    "source_timezone": "fixed EST (UTC-05:00), no DST",
    "utc_conversion": "source timestamp + 5 hours",
    "timestamp_semantics": "UTC minute bar open; time_close exclusive +1 minute",
    "synthetic_fills": False,
    "zero_volume_policy": "retain; volume is not an executable-volume estimate",
    "duplicate_policy": "remove exact UTC+OHLCV duplicates with audit counts; reject conflicting duplicates",
    "checksum_semantics": "locally computed ZIP SHA256, not a publisher checksum",
}


class HistDataError(RuntimeError):
    """Source availability, archive schema, or quote integrity failed."""

    def __init__(self, message: str, *, metadata: dict[str, Any] | None = None):
        self.metadata = metadata or {}
        super().__init__(message)


class HistDataRateLimitError(HistDataError):
    """No retry is made; retry_after_seconds exposes the source cooldown."""

    def __init__(self, retry_after_seconds: float | None):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            "HistData returned HTTP 429; this client is stopped, with no retry. "
            f"Retry-After seconds: {retry_after_seconds!r}"
        )


@dataclass
class FetchResult:
    frame: pd.DataFrame
    metadata: dict[str, Any]


def _utc(value: object) -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            raise ValueError("NaT")
        return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    except (ValueError, TypeError) as exc:
        raise HistDataError(f"invalid timestamp: {value!r}") from exc


def _bounds(start: object, end: object) -> tuple[pd.Timestamp, pd.Timestamp]:
    lower, upper = _utc(start), _utc(end)
    if lower >= upper:
        raise HistDataError("start must precede exclusive end")
    if lower != lower.floor("min") or upper != upper.floor("min"):
        raise HistDataError("range bounds must align to UTC minute opens")
    return lower, upper


def _token(year: int, month: int | None) -> str:
    if type(year) is not int or not 1900 <= year <= 9998:
        raise HistDataError("invalid archive year")
    if month is not None and (type(month) is not int or not 1 <= month <= 12):
        raise HistDataError("invalid archive month")
    return f"{year:04d}" if month is None else f"{year:04d}{month:02d}"


def archive_plan(
    start: object, end: object, *, as_of: object | None = None
) -> list[tuple[int, int | None]]:
    """Use closed-year ZIPs and current-year monthly ZIPs in source EST.

    A UTC January 1 start can require the preceding source year's archive.
    ``as_of`` selects the source's current calendar year; it is not a claim
    that the current archive is complete or that every market minute exists.
    """
    lower, upper = _bounds(start, end)
    now = _utc(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC")
    first = lower.tz_convert(EST_FIXED)
    last = (upper - ONE_MINUTE).tz_convert(EST_FIXED)
    current = now.tz_convert(EST_FIXED)
    if (last.year, last.month) > (current.year, current.month):
        raise HistDataError("range requests a future source-calendar month")
    plan = []
    for year in range(first.year, last.year + 1):
        if year < current.year:
            plan.append((year, None))
        else:
            first_month = first.month if year == first.year else 1
            last_month = last.month if year == last.year else 12
            plan.extend((year, month) for month in range(first_month, last_month + 1))
    return plan


def _summary(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": len(frame),
        "time_min": frame.index[0].isoformat() if len(frame) else None,
        "time_max": frame.index[-1].isoformat() if len(frame) else None,
        "time_close_max": frame["time_close"].iloc[-1].isoformat() if len(frame) else None,
        "zero_volume_rows": int(frame["volume"].eq(0).sum()),
        "nonconsecutive_minute_gaps": int((frame.index.to_series().diff() > ONE_MINUTE).sum()),
    }


def _validate_order(frame: pd.DataFrame) -> None:
    if frame.index.has_duplicates:
        duplicates = frame.index[frame.index.duplicated(keep=False)]
        sample = [stamp.isoformat() for stamp in duplicates.unique()[:3]]
        raise HistDataError(
            f"conflicting duplicate minute timestamps: {len(duplicates)} rows, "
            f"{duplicates.nunique()} minutes; first UTC times {sample}",
            metadata={"duplicate_timestamp_rows": len(duplicates),
                      "duplicate_timestamps": int(duplicates.nunique()),
                      "duplicate_utc_examples": sample},
        )
    if not frame.index.is_monotonic_increasing:
        raise HistDataError("unsorted minute timestamps; refusing to reorder")


def parse_archive(
    payload: bytes,
    year: int,
    month: int | None = None,
    *,
    max_zip_bytes: int = MAX_ZIP_BYTES,
    max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
) -> FetchResult:
    """Validate all source rows before returning a UTC-indexed frame.

    ZIP contents stay in memory. Expected CSV identity, duplicate members,
    paths, expansion size, minute alignment, source period, ordering, finite
    prices, candle bounds and nonnegative volume are strict, fail-closed gates.
    """
    token = _token(year, month)
    expected = f"DAT_ASCII_XAUUSD_M1_{token}.csv"
    if not payload or len(payload) > max_zip_bytes:
        raise HistDataError("empty or oversized ZIP response")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if not names or len(names) > 8 or len(set(names)) != len(names):
                raise HistDataError("invalid or duplicate ZIP members")
            for info in infos:
                name = info.filename
                if ("/" in name or "\\" in name or name in {".", ".."}
                        or ":" in name or info.is_dir() or info.flag_bits & 1
                        or ((info.external_attr >> 16) & 0o170000) == 0o120000):
                    raise HistDataError("unsafe ZIP member")
                if name != expected and not name.lower().endswith(".txt"):
                    raise HistDataError(f"unexpected ZIP member: {name!r}")
            if names.count(expected) != 1:
                raise HistDataError(f"expected exactly one {expected}")
            if sum(info.file_size for info in infos) > max_uncompressed_bytes:
                raise HistDataError("ZIP uncompressed size exceeds limit")
            csv_payload = archive.read(expected)
    except HistDataError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
        raise HistDataError("unreadable ZIP or failed ZIP integrity check") from exc

    try:
        raw = pd.read_csv(
            io.BytesIO(csv_payload), sep=";", header=None, dtype=str,
            keep_default_na=False, skip_blank_lines=False, encoding="ascii",
        )
        if raw.empty or raw.shape[1] != 6:
            raise HistDataError("expected nonempty six-column M1 CSV")
        raw.columns = ["source_time", *NUMERIC_COLUMNS]
        if not raw["source_time"].str.fullmatch(r"[0-9]{8} [0-9]{4}00").all():
            raise HistDataError("invalid or non-minute source timestamp")
        source_time = pd.to_datetime(raw["source_time"], format="%Y%m%d %H%M%S", errors="raise")
        if not source_time.dt.year.eq(year).all():
            raise HistDataError("timestamp outside archive year")
        if month is not None and not source_time.dt.month.eq(month).all():
            raise HistDataError("timestamp outside archive month")
        frame = raw[NUMERIC_COLUMNS].apply(pd.to_numeric, errors="raise").astype(float)
        if not (frame.abs() < float("inf")).all().all():
            raise HistDataError("non-finite OHLCV")
        prices = frame[["open", "high", "low", "close"]]
        if (prices <= 0).any().any() or frame["volume"].lt(0).any():
            raise HistDataError("non-positive OHLC or negative volume")
        if (frame["high"].lt(prices.max(axis=1)).any()
                or frame["low"].gt(prices.min(axis=1)).any()):
            raise HistDataError("inconsistent OHLC candle bounds")
        frame.index = pd.DatetimeIndex(
            source_time.dt.tz_localize(EST_FIXED).dt.tz_convert("UTC"), name="time"
        )
        frame["time_close"] = frame.index + ONE_MINUTE
        raw_rows = len(frame)
        # Compare the UTC timestamp AND all five source numeric fields. Preserve
        # source order; conflicting timestamps must never choose first/last.
        exact_duplicates = frame.reset_index().duplicated(keep="first").to_numpy()
        duplicate_exact_removed = int(exact_duplicates.sum())
        frame = frame.loc[~exact_duplicates].copy()
        try:
            _validate_order(frame)
        except HistDataError as exc:
            exc.metadata.update({"archive_token": token, "raw_rows": raw_rows,
                                 "duplicate_exact_removed": duplicate_exact_removed,
                                 "unique_rows": int(frame.index.nunique()),
                                 "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)})
            raise
    except (ValueError, TypeError, UnicodeError, pd.errors.ParserError) as exc:
        raise HistDataError("invalid CSV values or structure") from exc
    return FetchResult(frame, {
        **SOURCE_CONTRACT, "year": year, "month": month, "archive_token": token,
        "raw_rows": raw_rows, "duplicate_exact_removed": duplicate_exact_removed,
        "unique_rows": len(frame),
        "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
        "csv_bytes": len(csv_payload), "csv_member": expected, "zip_members": names,
        **_summary(frame),
    })


class _DownloadForm(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self.current = None
            if values.get("id") == "file_down" or values.get("name") == "file_down":
                self.current = {"action": values.get("action"), "method": values.get("method"), "fields": {}}
                self.forms.append(self.current)
        elif tag == "input" and self.current is not None:
            name, value = values.get("name"), values.get("value")
            if name:
                fields = self.current["fields"]
                if name in fields:
                    raise HistDataError("duplicate download form field")
                fields[name] = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.current = None


def _form_fields(html: bytes, year: int, month: int | None) -> dict[str, str]:
    parser = _DownloadForm()
    try:
        parser.feed(html.decode("utf-8"))
    except UnicodeError as exc:
        raise HistDataError("download page is not UTF-8 HTML") from exc
    if len(parser.forms) != 1:
        raise HistDataError("ordinary download form unavailable; no CAPTCHA/challenge bypass attempted")
    form = parser.forms[0]
    if form["action"] != "/get.php" or str(form["method"]).upper() != "POST":
        raise HistDataError("download form action or method changed")
    expected = {"date": str(year), "datemonth": _token(year, month),
                "platform": "ASCII", "timeframe": "M1", "fxpair": "XAUUSD"}
    fields = form["fields"]
    if any(fields.get(key) != value for key, value in expected.items()):
        raise HistDataError("download form instrument, period, or format changed")
    if set(fields) != {*expected, "tk"} or not fields.get("tk"):
        raise HistDataError("download token missing or download form fields changed")
    return fields


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return None


class HistDataClient:
    """Serial, paced HTTP client; HTTP 429 stops this instance permanently.

    No retry is scheduled before or after Retry-After: callers receive the
    source cooldown on HistDataRateLimitError and must not retry earlier.
    Tokens are used only in the normal same-site POST, never in metadata.
    """

    def __init__(self, *, pause_seconds: float = 1.0, timeout: float = 30.0,
                 max_zip_bytes: int = MAX_ZIP_BYTES) -> None:
        if not math.isfinite(pause_seconds) or not 0 <= pause_seconds <= 60:
            raise ValueError("pause_seconds must be between 0 and 60")
        if not math.isfinite(timeout) or not 0 < timeout <= 60 or max_zip_bytes <= 0:
            raise ValueError("invalid timeout or ZIP size limit")
        self.pause_seconds, self.timeout, self.max_zip_bytes = pause_seconds, timeout, max_zip_bytes
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._last_request: float | None = None
        self._rate_limit: HistDataRateLimitError | None = None

    def _request(self, request: urllib.request.Request, limit: int) -> tuple[bytes, str]:
        if self._rate_limit is not None:
            raise self._rate_limit
        if self._last_request is not None:
            remaining = self.pause_seconds - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                final_url = response.geturl()
                parsed = urllib.parse.urlsplit(final_url)
                if parsed.scheme != "https" or parsed.netloc != "www.histdata.com":
                    raise HistDataError("unexpected download redirect destination")
                if response.status != 200:
                    raise HistDataError(f"unexpected HTTP status: {response.status}")
                payload = response.read(limit + 1)
                if len(payload) > limit:
                    raise HistDataError("HTTP response exceeds size limit")
                return payload, final_url
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                self._rate_limit = HistDataRateLimitError(_retry_after(exc.headers.get("Retry-After")))
                raise self._rate_limit from exc
            raise HistDataError(f"HistData HTTP {exc.code} for {request.full_url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HistDataError(f"HistData network failure for {request.full_url}") from exc
        finally:
            self._last_request = time.monotonic()

    def fetch_archive(self, year: int, month: int | None = None) -> FetchResult:
        """Fetch one ordinary form and ZIP, validating the full archive."""
        _token(year, month)
        page_url = f"{PAGE_BASE}/{year}" + (f"/{month}" if month is not None else "")
        headers = {"User-Agent": "Mozilla/5.0 (compatible; fable-trading-research/1.0)"}
        html, page_url = self._request(urllib.request.Request(page_url, headers=headers), 1024 * 1024)
        fields = _form_fields(html, year, month)
        endpoint = BASE_URL + "/get.php"
        request = urllib.request.Request(endpoint, data=urllib.parse.urlencode(fields).encode("ascii"),
                                         headers={**headers, "Referer": page_url,
                                                  "Content-Type": "application/x-www-form-urlencoded"})
        payload, download_url = self._request(request, self.max_zip_bytes)
        try:
            result = parse_archive(payload, year, month, max_zip_bytes=self.max_zip_bytes)
        except HistDataError as exc:
            exc.metadata.update({"page_url": page_url, "download_url": download_url,
                                 "archive_token": _token(year, month),
                                 "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)})
            raise
        result.metadata.update({"page_url": page_url, "download_url": download_url,
                                "download_method": "POST", "fetched_at": pd.Timestamp.now(tz="UTC").isoformat()})
        return result

    def fetch_range(self, start: object, end: object, *, as_of: object | None = None,
                    progress_callback: Callable[[dict[str, Any]], None] | None = None) -> FetchResult:
        """Return actual minute opens within [start, end), without gap fills.

        Progress receives each validated archive's token, row counts and audit
        metadata, with no form token. Empty retained archives are allowed at
        holidays/boundaries; an entirely empty requested result fails closed.
        """
        lower, upper = _bounds(start, end)
        frames, audits = [], []
        for year, month in archive_plan(lower, upper, as_of=as_of):
            result = self.fetch_archive(year, month)
            retained = result.frame.loc[(result.frame.index >= lower) & (result.frame.index < upper)].copy()
            audit = {**result.metadata, "retained": _summary(retained)}
            audits.append(audit)
            if len(retained):
                frames.append(retained)
            if progress_callback is not None:
                progress_callback(dict(audit))
        if not frames:
            raise HistDataError("no actual source bars within the requested UTC range")
        frame = pd.concat(frames)
        _validate_order(frame)
        return FetchResult(frame, {
            **SOURCE_CONTRACT, "requested_start": lower.isoformat(),
            "requested_end_exclusive": upper.isoformat(), "archives": audits,
            "archive_count": len(audits), **_summary(frame),
            "coverage_note": "Observed bars only; archive existence does not establish complete market coverage.",
        })


def fetch_range(start: object, end: object, *, as_of: object | None = None,
                pause_seconds: float = 1.0, timeout: float = 30.0,
                progress_callback: Callable[[dict[str, Any]], None] | None = None) -> FetchResult:
    """Convenience API; e.g. fetch_range('2012-01-01', '2026-09-01')."""
    return HistDataClient(pause_seconds=pause_seconds, timeout=timeout).fetch_range(
        start, end, as_of=as_of, progress_callback=progress_callback,
    )

"""Offline contract tests for real HistData M1 format; no market data is written."""

from __future__ import annotations

import hashlib
import io
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.message import Message

import pandas as pd
import pytest

from yoyo.data import xauusd_histdata as hist


def _zip(rows: list[str], token: str = "202609", *, name: str | None = None,
         extra: str | None = None) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name or f"DAT_ASCII_XAUUSD_M1_{token}.csv", "\n".join(rows) + "\n")
        if extra is not None:
            archive.writestr(extra, "uninterpreted report")
    return stream.getvalue()


def _row(stamp: str, prices: str = "100;102;99;101;0") -> str:
    return f"{stamp};{prices}"


def _form(token: str = "202609", *, pair: str = "XAUUSD", action: str = "/get.php") -> bytes:
    fields = {"tk": "fixture-public-form-token", "date": token[:4], "datemonth": token,
              "platform": "ASCII", "timeframe": "M1", "fxpair": pair}
    html = f'<form id="file_down" method="POST" action="{action}">'
    html += "".join(f'<input type="hidden" name="{key}" value="{value}">' for key, value in fields.items())
    return (html + "</form>").encode()


def test_bid_fixed_est_summer_winter_zero_volume_and_unfilled_gap() -> None:
    payload = _zip([_row("20260102 000000"), _row("20260701 000000")], "2026")
    result = hist.parse_archive(payload, 2026)
    assert result.frame.index.name == "time"
    assert str(result.frame.index.tz) == "UTC"
    assert list(result.frame.index) == [pd.Timestamp("2026-01-02T05:00Z"), pd.Timestamp("2026-07-01T05:00Z")]
    assert list(result.frame.columns) == ["open", "high", "low", "close", "volume", "time_close"]
    assert len(result.frame) == 2  # No fabricated intervening or zero-volume deletion.
    assert (result.frame["time_close"] - result.frame.index).eq(pd.Timedelta(minutes=1)).all()
    assert result.metadata["price_side"] == "BID"
    assert result.metadata["zero_volume_rows"] == 2
    assert result.metadata["nonconsecutive_minute_gaps"] == 1
    assert result.metadata["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result.metadata["bytes"] == len(payload)


def test_current_year_months_and_prior_year_utc_boundary() -> None:
    plan = hist.archive_plan("2012-01-01", "2026-09-01", as_of="2026-09-08")
    assert plan == [(year, None) for year in range(2011, 2026)] + [(2026, month) for month in range(1, 9)]
    assert hist.archive_plan("2026-09-01T05:00Z", "2026-09-02", as_of="2026-09-08") == [(2026, 9)]
    assert hist.archive_plan("2026-09-01", "2026-09-02", as_of="2026-09-08") == [(2026, 8), (2026, 9)]
    with pytest.raises(hist.HistDataError, match="future"):
        hist.archive_plan("2026-09-01", "2026-11-01", as_of="2026-09-08")
    with pytest.raises(hist.HistDataError, match="precede"):
        hist.archive_plan("2026-09-01", "2026-09-01")
    with pytest.raises(hist.HistDataError, match="minute"):
        hist.archive_plan("2026-09-01T00:00:01Z", "2026-09-02")


@pytest.mark.parametrize(("rows", "message"), [
    ([_row("20260901 000000"), _row("20260901 000000", "100;102;99;100;0")], "duplicate"),
    ([_row("20260901 000100"), _row("20260901 000000")], "unsorted"),
    ([_row("20260901 000001")], "minute"),
    ([_row("20260230 000000")], "CSV"),
    ([_row("20260831 235900")], "month"),
    ([_row("20250901 000000")], "year"),
    ([_row("20260901 000000", "100;99;98;101;0")], "bounds"),
    ([_row("20260901 000000", "100;102;101;101;0")], "bounds"),
    ([_row("20260901 000000", "100;102;99;101;-1")], "negative"),
    ([_row("20260901 000000", "0;102;0;101;0")], "non-positive"),
    ([_row("20260901 000000", "100;inf;99;101;0")], "non-finite"),
    ([_row("20260901 000000", "100;102;NaN;101;0")], "CSV|non-finite"),
    (["20260901 000000;100;102;99;101"], "six-column"),
    ([_row("20260901 000000"), ""], "timestamp|CSV"),
])
def test_invalid_source_rows_fail_without_repairs(rows: list[str], message: str) -> None:
    with pytest.raises(hist.HistDataError, match=message):
        hist.parse_archive(_zip(rows), 2026, 9)


def test_only_exact_utc_ohlcv_duplicates_are_removed_with_audit_counts() -> None:
    payload = _zip([_row("20260901 000000"), _row("20260901 000000"), _row("20260901 000100")])
    result = hist.parse_archive(payload, 2026, 9)
    assert len(result.frame) == 2  # Same prices in DIFFERENT minutes remain.
    assert result.metadata["raw_rows"] == 3
    assert result.metadata["duplicate_exact_removed"] == 1
    assert result.metadata["unique_rows"] == 2
    assert result.metadata["zero_volume_rows"] == 2
    conflict = _zip([_row("20260901 000000"), _row("20260901 000000", "100;102;99;101;1")])
    with pytest.raises(hist.HistDataError, match="conflicting duplicate") as error:
        hist.parse_archive(conflict, 2026, 9)
    assert error.value.metadata["duplicate_timestamps"] == 1
    assert error.value.metadata["duplicate_timestamp_rows"] == 2
    assert error.value.metadata["duplicate_exact_removed"] == 0
    assert error.value.metadata["sha256"] == hashlib.sha256(conflict).hexdigest()


@pytest.mark.parametrize("name", ["../DAT_ASCII_XAUUSD_M1_202609.csv", "nested/data.csv",
                                  "DAT_ASCII_EURUSD_M1_202609.csv", "DAT_ASCII_XAUUSD_M1_202608.csv"])
def test_zip_identity_and_paths_rejected(name: str) -> None:
    with pytest.raises(hist.HistDataError, match="ZIP member"):
        hist.parse_archive(_zip([_row("20260901 000000")], name=name), 2026, 9)


def test_zip_limits_bad_zip_and_safe_text_report() -> None:
    payload = _zip([_row("20260901 000000")], extra="report.txt")
    assert hist.parse_archive(payload, 2026, 9).metadata["zip_members"][-1] == "report.txt"
    with pytest.raises(hist.HistDataError, match="oversized"):
        hist.parse_archive(payload, 2026, 9, max_zip_bytes=2)
    with pytest.raises(hist.HistDataError, match="uncompressed"):
        hist.parse_archive(payload, 2026, 9, max_uncompressed_bytes=2)
    with pytest.raises(hist.HistDataError, match="unreadable ZIP"):
        hist.parse_archive(b"<html>challenge page</html>", 2026, 9)
    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("DAT_ASCII_XAUUSD_M1_202609.csv", _row("20260901 000000"))
        with pytest.warns(UserWarning, match="Duplicate"):
            archive.writestr("DAT_ASCII_XAUUSD_M1_202609.csv", _row("20260901 000100"))
    with pytest.raises(hist.HistDataError, match="duplicate ZIP"):
        hist.parse_archive(duplicate.getvalue(), 2026, 9)


def test_form_matches_period_and_cannot_change_destination_or_instrument() -> None:
    assert hist._form_fields(_form("2012"), 2012, None)["datemonth"] == "2012"
    assert hist._form_fields(_form(), 2026, 9)["fxpair"] == "XAUUSD"
    for html in [_form(pair="XAUTUSD"), _form("202608"), _form(action="https://example.org/get.php"), b"captcha"]:
        with pytest.raises(hist.HistDataError):
            hist._form_fields(html, 2026, 9)


class _Response(io.BytesIO):
    status = 200

    def __init__(self, payload: bytes, url: str):
        super().__init__(payload)
        self.url = url

    def geturl(self) -> str:
        return self.url


def test_normal_form_post_is_paced_and_metadata_excludes_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = hist.HistDataClient(pause_seconds=1)
    calls = []
    sleeps = []
    payload = _zip([_row("20260901 000000")])

    def open_request(request: urllib.request.Request, *, timeout: float) -> _Response:
        calls.append(request)
        return _Response(_form() if request.get_method() == "GET" else payload, request.full_url)

    monkeypatch.setattr(client._opener, "open", open_request)
    monkeypatch.setattr(hist.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(hist.time, "sleep", sleeps.append)
    result = client.fetch_archive(2026, 9)
    assert [request.get_method() for request in calls] == ["GET", "POST"]
    assert sleeps == [1.0]
    fields = urllib.parse.parse_qs(calls[1].data.decode())
    assert fields["tk"] == ["fixture-public-form-token"]
    assert calls[1].get_header("Referer") == calls[0].full_url
    assert "fixture-public-form-token" not in str(result.metadata)
    assert result.metadata["download_url"] == "https://www.histdata.com/get.php"


def test_429_stops_without_retry_and_preserves_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    client = hist.HistDataClient(pause_seconds=0)
    calls = []
    headers = Message()
    headers["Retry-After"] = "120"

    def limited(request: urllib.request.Request, *, timeout: float) -> None:
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 429, "rate limited", headers, None)

    monkeypatch.setattr(client._opener, "open", limited)
    for _ in range(2):
        with pytest.raises(hist.HistDataRateLimitError) as error:
            client.fetch_archive(2026, 9)
        assert error.value.retry_after_seconds == 120
    assert len(calls) == 1
    monkeypatch.setattr(hist.time, "time", lambda: 0)
    assert hist._retry_after("Thu, 01 Jan 1970 00:02:00 GMT") == 120
    assert hist._retry_after("bad value") is None


def test_range_trims_exclusive_utc_end_and_reports_real_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    client = hist.HistDataClient(pause_seconds=0)
    received, progress = [], []
    rows = {
        (2025, None): [_row("20251231 185900"), _row("20251231 190000"), _row("20251231 235900")],
        (2026, 1): [_row("20260101 000000"), _row("20260101 000100")],
    }

    def fetch_archive(year: int, month: int | None) -> hist.FetchResult:
        received.append((year, month))
        token = str(year) if month is None else f"{year}{month:02d}"
        return hist.parse_archive(_zip(rows[(year, month)], token), year, month)

    monkeypatch.setattr(client, "fetch_archive", fetch_archive)
    result = client.fetch_range("2026-01-01", "2026-01-01T05:01Z", as_of="2026-09-08", progress_callback=progress.append)
    assert received == [(2025, None), (2026, 1)]
    assert result.frame.index.tolist() == [pd.Timestamp("2026-01-01T00:00Z"), pd.Timestamp("2026-01-01T04:59Z"), pd.Timestamp("2026-01-01T05:00Z")]
    assert result.metadata["rows"] == 3
    assert result.metadata["time_close_max"] == "2026-01-01T05:01:00+00:00"
    assert [item["retained"]["rows"] for item in progress] == [2, 1]
    assert result.metadata["nonconsecutive_minute_gaps"] == 1


def test_entire_empty_retained_range_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    client = hist.HistDataClient(pause_seconds=0)
    monkeypatch.setattr(client, "fetch_archive", lambda year, month: hist.parse_archive(
        _zip([_row("20260901 000000")]), 2026, 9))
    with pytest.raises(hist.HistDataError, match="no actual source bars"):
        client.fetch_range("2026-09-04", "2026-09-05", as_of="2026-09-08")

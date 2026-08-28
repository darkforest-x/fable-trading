from __future__ import annotations

import hashlib
import io
import zipfile

import pandas as pd

from yoyo.data.binance_um_archives import (
    admitted_symbols,
    archive_months,
    archive_urls,
    parse_checksum,
    parse_month_zip,
)


def _zip(rows: list[str], *, name: str) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(name, "\n".join(rows) + "\n")
    return payload.getvalue()


def test_archive_months_is_inclusive() -> None:
    assert archive_months("2025-12-15T00:00:00Z", "2026-02-28T00:00:00Z") == [
        "2025-12",
        "2026-01",
        "2026-02",
    ]


def test_archive_urls_percent_encode_non_ascii_symbol() -> None:
    archive, checksum = archive_urls("币安人生USDT", "2026-04")
    assert "币安人生" not in archive
    assert "%E5%B8%81%E5%AE%89%E4%BA%BA%E7%94%9FUSDT" in archive
    assert checksum == archive + ".CHECKSUM"


def test_admitted_symbols_filters_contract_quote_and_boundary() -> None:
    before = pd.Timestamp("2026-05-01T00:00:00Z")
    old = int(pd.Timestamp("2025-01-01T00:00:00Z").timestamp() * 1000)
    late = int(before.timestamp() * 1000)
    payload = {
        "symbols": [
            {"symbol": "B", "pair": "B", "quoteAsset": "USDT", "contractType": "PERPETUAL", "onboardDate": old, "status": "TRADING"},
            {"symbol": "A", "pair": "A", "quoteAsset": "USDT", "contractType": "PERPETUAL", "onboardDate": old, "status": "SETTLING"},
            {"symbol": "LATE", "pair": "LATE", "quoteAsset": "USDT", "contractType": "PERPETUAL", "onboardDate": late, "status": "TRADING"},
            {"symbol": "SPOT", "pair": "SPOT", "quoteAsset": "USDT", "contractType": "", "onboardDate": old, "status": "TRADING"},
            {"symbol": "COIN", "pair": "COIN", "quoteAsset": "USD", "contractType": "PERPETUAL", "onboardDate": old, "status": "TRADING"},
        ]
    }
    assert [row["symbol"] for row in admitted_symbols(payload, before=before)] == ["A", "B"]


def test_parse_checksum_and_month_zip() -> None:
    name = "BTCUSDT-15m-2026-04.csv"
    rows = [
        "1775001600000,100,102,99,101,12,1775002499999,0,1,0,0,0",
        "1775002500000,101,103,100,102,13,1775003399999,0,1,0,0,0",
    ]
    payload = _zip(rows, name=name)
    digest = hashlib.sha256(payload).hexdigest()
    assert parse_checksum(
        f"{digest}  BTCUSDT-15m-2026-04.zip\n".encode(),
        expected_filename="BTCUSDT-15m-2026-04.zip",
    ) == digest
    frame, audit = parse_month_zip(
        payload,
        symbol="BTCUSDT",
        month="2026-04",
        expected_sha256=digest,
    )
    assert list(frame.columns) == ["ts", "open", "high", "low", "close", "volume", "open_time"]
    assert len(frame) == 2
    assert audit["epoch_unit"] == "milliseconds"
    assert audit["non_15m_gaps"] == 0


def test_parse_month_zip_accepts_header_and_microseconds() -> None:
    name = "ETHUSDT-15m-2026-04.csv"
    header = ",".join(
        [
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
        ]
    )
    rows = [
        header,
        "1775001600000000,100,102,99,101,12,1775002499999999,0,1,0,0,0",
    ]
    payload = _zip(rows, name=name)
    digest = hashlib.sha256(payload).hexdigest()
    frame, audit = parse_month_zip(
        payload,
        symbol="ETHUSDT",
        month="2026-04",
        expected_sha256=digest,
    )
    assert int(frame.iloc[0]["ts"]) == 1_775_001_600_000
    assert audit["epoch_unit"] == "microseconds"

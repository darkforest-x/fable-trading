from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from src.data.fetch_okx import (
    ArchiveFetchError,
    aggregate_archive_bytes,
    archive_months,
    archive_url,
)


def _archive_payload(*, missing_minute: int | None = None) -> bytes:
    rows = [
        "instrument_name,open,high,low,close,vol,vol_ccy,vol_quote,open_time,confirm"
    ]
    start = int(pd.Timestamp("2024-01-01T00:00:00Z").timestamp() * 1_000)
    for minute in range(30):
        if minute == missing_minute:
            continue
        price = 100.0 + minute
        rows.append(
            f"BTC-USDT-SWAP,{price},{price + 2},{price - 2},{price + 1},"
            f"{minute + 1},0,0,{start + minute * 60_000},0"
        )
    raw = ("\n".join(rows) + "\n").encode()
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BTC-USDT-SWAP-candlesticks-2024-01.csv", raw)
    return target.getvalue()


def test_archive_months_are_complete_and_exclusive() -> None:
    months = archive_months(
        "2023-07",
        "2023-09",
        max_exclusive="2023-10-01T00:00:00Z",
    )
    assert [month.strftime("%Y-%m") for month in months] == [
        "2023-07",
        "2023-08",
        "2023-09",
    ]
    with pytest.raises(ArchiveFetchError, match="crosses exclusive boundary"):
        archive_months(
            "2023-07",
            "2023-10",
            max_exclusive="2023-10-15T00:00:00Z",
        )


def test_archive_url_uses_official_monthly_contract() -> None:
    assert archive_url(
        "BTC_USDT_SWAP", pd.Timestamp("2024-01-01T00:00:00Z")
    ).endswith(
        "/202401/BTC-USDT-SWAP-candlesticks-2024-01.zip?v=999"
    )


def test_archive_aggregation_keeps_only_complete_utc_15m_groups() -> None:
    frame, audit = aggregate_archive_bytes(
        _archive_payload(missing_minute=20),
        symbol="BTC_USDT_SWAP",
        month=pd.Timestamp("2024-01-01T00:00:00Z"),
    )
    assert len(frame) == 1
    assert frame.iloc[0].to_dict() == {
        "ts": 1704067200000,
        "open": 100.0,
        "high": 116.0,
        "low": 98.0,
        "close": 115.0,
        "volume": 120,
        "open_time": pd.Timestamp("2024-01-01T00:00:00Z"),
    }
    assert audit["raw_1m_rows"] == 29
    assert audit["complete_15m_rows"] == 1
    assert audit["incomplete_15m_groups_dropped"] == 1
    assert audit["confirm_values"] == ["0"]


def test_archive_aggregation_rejects_wrong_instrument() -> None:
    with pytest.raises(ArchiveFetchError, match="instrument drift"):
        aggregate_archive_bytes(
            _archive_payload(),
            symbol="ETH_USDT_SWAP",
            month=pd.Timestamp("2024-01-01T00:00:00Z"),
        )

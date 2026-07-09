from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.data import update_okx
from src.data.loader import load_series


def _write_kline(path: Path, rows: list[str]) -> None:
    path.write_text(
        "ts,open,high,low,close,volume,open_time,confirm\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def test_load_series_merges_sorts_and_dedupes_open_time(tmp_path: Path) -> None:
    first = tmp_path / "okx_BTC_USDT_SWAP_15m_2.csv"
    second = tmp_path / "okx_BTC_USDT_SWAP_15m_2_latest.csv"
    _write_kline(
        first,
        [
            "1000,1,2,0.5,1.5,10,1970-01-01 00:00:01+00:00,1",
            "2000,2,3,1.5,2.5,20,1970-01-01 00:00:02+00:00,1",
        ],
    )
    _write_kline(
        second,
        [
            "2000,2,4,1.5,3.5,30,1970-01-01 00:00:02+00:00,1",
            "3000,3,4,2.5,3.5,30,1970-01-01 00:00:03+00:00,0",
        ],
    )

    frame = load_series([first, second])

    assert frame["open_time"].tolist() == [
        pd.Timestamp("1970-01-01 00:00:01+00:00"),
        pd.Timestamp("1970-01-01 00:00:02+00:00"),
    ]
    assert frame["close"].tolist() == [1.5, 3.5]
    assert frame["volume"].tolist() == [10, 30]


def test_update_okx_no_new_confirmed_bars_leaves_file_in_place(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "okx_BTC_USDT_SWAP_15m_1.csv"
    path.write_text(
        "ts,open,high,low,close,volume,open_time\n"
        "1000,1,2,0.5,1.5,10,1970-01-01 00:00:01+00:00\n",
        encoding="utf-8",
    )
    requested_urls: list[str] = []

    def fake_request(url: str) -> dict[str, object]:
        requested_urls.append(url)
        return {
            "code": "0",
            "data": [
                ["2000", "1", "2", "0.5", "1.5", "10", "", "", "0"],
                ["1000", "1", "2", "0.5", "1.5", "10", "", "", "1"],
            ],
        }

    monkeypatch.setattr(update_okx, "FETCH_DIR", tmp_path)
    monkeypatch.setattr(update_okx, "_request", fake_request)

    symbol, added = update_okx.update_file(path, bar="15m")

    assert symbol == "BTC_USDT_SWAP"
    assert added == 0
    assert "bar=15m" in requested_urls[0]
    assert path.exists()
    assert not (tmp_path / "okx_BTC_USDT_SWAP_15m_2.csv").exists()

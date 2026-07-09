from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.data.bars import bar_to_timedelta, normalize_bar, purge_window
from src.data import update_okx
from src.data.update_okx import FILE_RE
from src.judgment import build_dataset
from src.judgment.train import load_splits


def _minimal_dataset(path: Path) -> None:
    frame = pd.DataFrame(
        {
            "signal_time": pd.date_range("2026-01-01", periods=10, freq="1h", tz="UTC"),
            "label": [0, 1] * 5,
            "realized_ret": [0.01, -0.01] * 5,
        }
    )
    frame.to_csv(path, index=False)


def test_bar_helpers_validate_and_convert_widths() -> None:
    assert normalize_bar("1H") == "1H"
    assert bar_to_timedelta("30m") == pd.Timedelta(minutes=30)
    assert purge_window(72, "5m") == pd.Timedelta(minutes=365)
    with pytest.raises(ValueError):
        normalize_bar("2H")


def test_load_splits_purge_uses_requested_bar_width(tmp_path: Path) -> None:
    path = tmp_path / "dataset.csv"
    _minimal_dataset(path)

    train_15m, _, _ = load_splits(path, horizon_bars=1, bar="15m")
    train_1h, _, _ = load_splits(path, horizon_bars=1, bar="1H")

    assert len(train_15m) == 8
    assert len(train_1h) == 6


def test_update_okx_filename_regex_accepts_supported_bars() -> None:
    matched = FILE_RE.match("okx_BTC_USDT_SWAP_1H_123.csv")

    assert matched is not None
    assert matched.group("symbol") == "BTC_USDT_SWAP"
    assert matched.group("bar") == "1H"


def test_update_okx_uses_file_bar_in_api_and_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "okx_BTC_USDT_30m_1.csv"
    path.write_text(
        "ts,open,high,low,close,volume,open_time\n"
        "1000,1,2,0.5,1.5,10,1970-01-01 00:00:01+00:00\n",
        encoding="utf-8",
    )
    requested_urls: list[str] = []

    def fake_request(url: str) -> dict:
        requested_urls.append(url)
        return {
            "code": "0",
            "data": [
                ["2000", "1", "2", "0.5", "1.5", "10", "", "", "1"],
                ["1000", "1", "2", "0.5", "1.5", "10", "", "", "1"],
            ],
        }

    monkeypatch.setattr(update_okx, "FETCH_DIR", tmp_path)
    monkeypatch.setattr(update_okx, "_request", fake_request)

    symbol, added = update_okx.update_file(path)

    assert symbol == "BTC_USDT"
    assert added == 1
    assert "bar=30m" in requested_urls[0]
    assert (tmp_path / "okx_BTC_USDT_30m_2.csv").exists()
    assert not path.exists()


def test_build_dataset_passes_bar_to_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_iter_series(*, bar: str, min_bars: int):
        seen["bar"] = bar
        seen["min_bars"] = min_bars
        return iter(())

    monkeypatch.setattr(build_dataset, "iter_series", fake_iter_series)

    dataset = build_dataset.build(mode="strict", bar="30m", horizon_bars=24)

    assert dataset.empty
    assert seen == {"bar": "30m", "min_bars": build_dataset.MIN_BARS}
    assert dataset.attrs["bar"] == "30m"
    assert dataset.attrs["horizon_bars"] == 24

"""Synthetic source contracts: units, cutoff, metadata and pagination."""
import math
import gzip
import json

import pytest

from yoyo.data.altseason_sources import HOUR, PublicClient, digest, normalize_bars, normalize_catalog, page_requests


def test_metadata_keeps_delisted_crypto_and_rejects_tradfi():
    common = dict(symbol="ABCUSDT", baseAsset="ABC", underlyingType="COIN", contractType="PERPETUAL", quoteAsset="USDT", marginAsset="USDT", status="SETTLING")
    rows = normalize_catalog("binance", {"symbols": [common, dict(common, symbol="AAPLUSDT", underlyingType="EQUITY")]}, "now")
    assert rows[0]["eligible"] is False
    assert rows[1]["eligible"] is True and rows[1]["status"] == "SETTLING"


def test_unicode_exchange_ticker_is_preserved():
    common = dict(symbol="币安人生USDT", baseAsset="币安人生", underlyingType="COIN", contractType="PERPETUAL", quoteAsset="USDT", marginAsset="USDT", status="TRADING")
    assert normalize_catalog("binance", {"symbols": [common]}, "now")[0]["symbol"] == "币安人生USDT"
    with pytest.raises(ValueError, match="Unsafe"):
        normalize_catalog("binance", {"symbols": [dict(common, symbol="../ABC")]}, "now")


def test_gate_unknown_or_stock_classification_is_not_crypto():
    common = dict(name="ABC_USDT", type="direct", quanto_multiplier="10", status="delisted")
    assert not normalize_catalog("gate", [common], "now")[0]["eligible"]
    assert normalize_catalog("gate", [dict(common, contract_type="")], "now")[0]["eligible"]
    assert not normalize_catalog("gate", [dict(common, contract_type="stocks")], "now")[0]["eligible"]
    assert not normalize_catalog("gate", [dict(common, contract_type="", is_pre_market=True)], "now")[0]["eligible"]


def test_gate_quote_is_sum_not_contract_volume_times_close():
    frame = normalize_bars("gate", [{"t": 0, "o": "100", "h": "120", "l": "90", "c": "110", "v": 20, "sum": "21000"}], {"contract_multiplier": 10}, 0, HOUR)
    assert frame.iloc[0]["volume"] == 20
    assert frame.iloc[0]["base_volume"] == 200
    assert frame.iloc[0]["quote_volume"] == 21000


def test_binance_uses_documented_quote_field_and_excludes_right_bar():
    row = [0, "10", "12", "9", "11", "7", HOUR - 1, "75", 1, "3", "30", "0"]
    later = [HOUR] + row[1:6] + [2 * HOUR - 1] + row[7:]
    frame = normalize_bars("binance", [row, later], {}, 0, HOUR)
    assert len(frame) == 1 and frame.volume.iloc[0] == 7 and frame.quote_volume.iloc[0] == 75
    assert math.isnan(frame.contract_volume.iloc[0])


def test_okx_unconfirmed_is_excluded_and_descending_rows_sorted():
    rows = [[str(HOUR), "10", "12", "9", "11", "20", "2", "21", "0"], ["0", "10", "12", "9", "11", "20", "2", "21", "1"]]
    frame = normalize_bars("okx", {"data": rows}, {}, 0, 2 * HOUR)
    assert len(frame) == 1 and frame.ts.iloc[0] == 0 and frame.quote_volume.iloc[0] == 21


@pytest.mark.parametrize("venue,count", [("binance", 3), ("gate", 2), ("okx", 11)])
def test_pages_exactly_partition_3144_hours(venue, count):
    pages = list(page_requests(venue, "BTC", 0, 3144 * HOUR))
    assert len(pages) == count
    assert pages[0][0] == 0 and pages[-1][1] == 3144 * HOUR
    assert all(a[1] == b[0] for a, b in zip(pages, pages[1:]))
    if venue == "gate":
        assert all("limit" not in x[3] for x in pages)
    if venue == "okx":
        assert all(x[3]["after"] == x[1] and x[3]["before"] == x[0] - 1 for x in pages)


def test_bad_ohlc_and_conflicting_duplicate_fail():
    row = [0, "10", "12", "9", "11", "7", HOUR - 1, "75"]
    with pytest.raises(ValueError, match="OHLC"):
        normalize_bars("binance", [[0, "10", "8", "9", "11", "7", HOUR - 1, "75"]], {}, 0, HOUR)
    with pytest.raises(ValueError, match="Conflicting"):
        normalize_bars("binance", [row, row[:4] + ["10"] + row[5:]], {}, 0, HOUR)


def test_cached_raw_receipt_rejects_changed_bytes(tmp_path):
    client = PublicClient("binance", tmp_path)
    request = {"method": "GET", "url": "https://fapi.binance.com/fapi/v1/klines", "params": {"symbol": "ABCUSDT"}}
    key = digest(json.dumps(request, sort_keys=True).encode())
    directory = tmp_path / "raw" / "binance" / key[:2]
    directory.mkdir(parents=True)
    (directory / (key + ".json")).write_text(json.dumps({"request": request, "body_sha256": digest(b"[]")}))
    path = directory / (key + ".json.gz")
    path.write_bytes(gzip.compress(b"[]"))
    assert client.get("/fapi/v1/klines", {"symbol": "ABCUSDT"})[0] == []
    path.write_bytes(gzip.compress(b"[1]"))
    with pytest.raises(ValueError, match="receipt mismatch"):
        client.get("/fapi/v1/klines", {"symbol": "ABCUSDT"})

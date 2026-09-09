"""Funding clock, missingness and pagination contracts without real data."""
import math

import pytest

from yoyo.data.altseason_funding import FundingClient, collect, normalize, request_params


def test_okx_missing_realized_does_not_inherit_forecast():
    payload = {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "fundingTime": "1000", "realizedRate": "", "fundingRate": "0.01"}]}
    row = normalize("okx", "BTC-USDT-SWAP", payload, 0, 2000).iloc[0]
    assert math.isnan(row.realized_rate) and math.isnan(row.mark_price)


def test_gate_original_second_clock_preserved_and_signed_rate():
    row = normalize("gate", "BTC_USDT", [{"t": 28801, "r": "-0.002"}], 0, 86400000).iloc[0]
    assert row.funding_time == 28801000 and row.realized_rate == -0.002 and math.isnan(row.mark_price)


def test_binance_mark_and_right_boundary():
    payload = [{"symbol": "BTCUSDT", "fundingTime": t, "fundingRate": "0.0001", "markPrice": "50000"} for t in [0, 1000, 2000]]
    rows = normalize("binance", "BTCUSDT", payload, 1000, 2000)
    assert len(rows) == 1 and rows.mark_price.iloc[0] == 50000


def test_duplicate_and_symbol_mismatch_fail():
    rows = [{"t": 1, "r": "0.01"}, {"t": 1, "r": "0.02"}]
    with pytest.raises(ValueError, match="Conflicting"):
        normalize("gate", "ABC_USDT", rows, 0, 3000)
    with pytest.raises(ValueError, match="symbol mismatch"):
        normalize("binance", "BTCUSDT", [{"symbol": "ETHUSDT", "fundingTime": 1000, "fundingRate": "0.1"}], 0, 3000)


def test_documented_endpoint_clocks():
    assert request_params("gate", "BTC_USDT", 1000, 10000, 8999)[1] == {"contract": "BTC_USDT", "from": 1, "to": 8, "limit": 1000}
    assert request_params("okx", "BTC-USDT-SWAP", 1000, 10000, 9000)[1]["before"] == 999
    assert request_params("binance", "BTCUSDT", 1000, 10000, 5001)[1]["startTime"] == 5001


class FakeClient:
    def __init__(self, venue, root, pages):
        self.venue, self.root, self.pages = venue, root, iter(pages)
        self.params = []

    def get(self, path, params):
        self.params.append(params)
        return next(self.pages), {"request": {"url": path, "params": params}, "body_sha256": "test", "relative_body_path": "fake", "fetched_at": "test"}


def test_gate_descending_pages_use_one_second_exclusive_step(tmp_path):
    client = FakeClient("gate", tmp_path, [[{"t": 7, "r": "0.01"}, {"t": 5, "r": "0.02"}], [{"t": 1, "r": "0.03"}], []])
    result = collect(client, "BTC_USDT", 0, 10000)
    assert result["row_count"] == 3 and result["full_settlement_schedule"] == "unknown"
    assert [x["to"] for x in client.params] == [9, 4, 0]


def test_binance_full_or_short_page_both_advance_until_empty(tmp_path):
    row = lambda t: {"symbol": "BTCUSDT", "fundingTime": t, "fundingRate": "0.01"}
    client = FakeClient("binance", tmp_path, [[row(1000), row(3000)], [row(7000)], []])
    result = collect(client, "BTCUSDT", 0, 10000)
    assert result["row_count"] == 3 and [x["startTime"] for x in client.params] == [0, 3001, 7001]
    assert result["missing_mark_price_rows"] == 3


def test_ignored_cursor_fails_not_infinite_retry(tmp_path):
    client = FakeClient("gate", tmp_path, [[{"t": 7, "r": "0.01"}], [{"t": 7, "r": "0.01"}]])
    with pytest.raises(ValueError, match="ignored requested range"):
        collect(client, "BTC_USDT", 0, 10000)


def test_client_refuses_fast_budget_and_ambient_credentials(tmp_path):
    with pytest.raises(ValueError, match="interval"):
        FundingClient("binance", tmp_path, minimum_interval=0.1)
    assert FundingClient("binance", tmp_path)._session().trust_env is False

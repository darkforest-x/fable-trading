"""Contract tests for the console's display-only OKX candle adapter."""
from __future__ import annotations

import pytest

from yoyo.console.market_data import MarketDataError, parse_okx_candles


def test_parse_okx_candles_orders_rows_and_keeps_live_state() -> None:
    payload = {
        "code": "0",
        "data": [
            ["1711901700000", "102", "105", "101", "104", "12", "0", "0", "0"],
            ["1711900800000", "100", "103", "99", "102", "10", "0", "0", "1"],
        ],
    }

    candles = parse_okx_candles(payload)

    assert [item["time"] for item in candles] == [1711900800, 1711901700]
    assert candles[0]["confirmed"] is True
    assert candles[1]["confirmed"] is False
    assert candles[1]["close"] == 104.0


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"code": "50011", "msg": "rate limit", "data": []}, "rate limit"),
        ({"code": "0", "data": []}, "empty"),
        (
            {
                "code": "0",
                "data": [["1711900800000", "100", "98", "99", "102", "10", "0", "0", "1"]],
            },
            "bounds",
        ),
    ],
)
def test_parse_okx_candles_fails_closed(payload: dict, message: str) -> None:
    with pytest.raises(MarketDataError, match=message):
        parse_okx_candles(payload)

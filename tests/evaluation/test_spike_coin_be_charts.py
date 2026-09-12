"""Focused display contracts for the SPIKE coin break-even chart book."""
from yoyo.evaluation.spike_coin_be_charts import tv_url


def test_chart_tradingview_link_keeps_the_source_venue_and_interval() -> None:
    assert tv_url("okx", "ETH-USDT-SWAP", 60) == "https://www.tradingview.com/chart/?symbol=OKX%3AETHUSDT.P&interval=60"
    assert tv_url("binance", "ETH/USDT:USDT", 30) == "https://www.tradingview.com/chart/?symbol=BINANCE%3AETHUSDT.P&interval=30"

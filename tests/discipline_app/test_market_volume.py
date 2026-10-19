"""Public volume normalization without credentials or exchange writes."""
import time
from decimal import Decimal
import pytest
from yoyo.discipline_app.market import PublicMarket, estimated_turnover


def test_volume_is_comparable_usdt_not_base_units_or_contract_lots():
    btc = estimated_turnover(dict(last='80000', volCcy24h='2', vol24h='200'))
    doge = estimated_turnover(dict(last='.2', volCcy24h='100000', vol24h='1000'))
    assert Decimal(btc) == 160000
    assert Decimal(doge) == 20000
    assert Decimal(btc) > Decimal(doge)
    assert Decimal(estimated_turnover(dict(last='.00000123', volCcy24h='900000000'))) == Decimal('1107')
    assert Decimal(estimated_turnover(dict(last='100', volCcy24h='0'))) == 0


@pytest.mark.parametrize('value', [None, '', 'NaN', 'Infinity', '-1', 'broken', True])
def test_invalid_volume_is_unknown(value):
    assert estimated_turnover(dict(last='100', volCcy24h=value, vol24h='999999')) is None


def test_no_lot_count_fallback_or_invalid_price_estimate():
    assert estimated_turnover(dict(last='100', vol24h='999999')) is None
    for price in ['0', '-1', 'NaN', 'Infinity', '']:
        assert estimated_turnover(dict(last=price, volCcy24h='5')) is None


def test_market_keeps_quote_when_volume_is_missing():
    class Book:
        def update_market(self, instruments, quotes):
            self.quotes = quotes
    book = Book()
    market = PublicMarket(book)
    market.metadata_at = time.time()
    market.instruments = {symbol: {} for symbol in ['BTC-USDT-SWAP', 'ETH-USDT-SWAP']}
    rows = [dict(instId=symbol, last='100', bidPx='99', askPx='101', ts='100000', sodUtc8='100')
            for symbol in market.instruments]
    rows[0]['volCcy24h'] = '12.5'
    market.get = lambda endpoint, params: rows
    market.tick()
    assert Decimal(book.quotes['BTC-USDT-SWAP']['volume24h_usdt']) == 1250
    assert book.quotes['ETH-USDT-SWAP']['volume24h_usdt'] is None
    assert book.quotes['ETH-USDT-SWAP']['last'] == '100'

"""Public workstation projections cannot masquerade stale or sampled data as live totals."""
from collections import Counter
from threading import Event, Lock
import time

import pytest
from yoyo.monitor.market_workstation import WorkstationData, trade_flow, liquidation_events, notional

SYMBOL = 'BTC-USDT-SWAP'
NOW = 1792252800000
INSTRUMENT = {'instId': SYMBOL, 'ctVal': '.01', 'ctMult': '1', 'ctValCcy': 'BTC'}


def trade(id='1', **kwargs):
    return dict(instId=SYMBOL, tradeId=id, side='buy', px='80000', sz='2', ts=str(NOW-1000), **kwargs)


def test_trade_delta_has_contract_units_and_bounded_sample_coverage():
    rows = [trade(), dict(trade('2'), side='sell', sz='1', ts=str(NOW-500))]
    result = trade_flow(rows + [rows[0]], INSTRUMENT, NOW)
    assert result['count'] == 2
    assert result['buy_usdt'] == 1600 and result['sell_usdt'] == 800
    assert result['delta_usdt'] == 800 and result['curve'][-1]['value'] == 800
    assert result['start_ms'] == NOW-1000 and result['end_ms'] == NOW-500
    assert '样本' in result['scope'] and '非完整' in result['scope']


def test_trade_projection_rejects_stale_future_foreign_and_invalid_rows():
    rows = [dict(trade(), ts=str(NOW-900001)), dict(trade('2'), ts=str(NOW+1001)),
            dict(trade('3'), instId='ETH-USDT-SWAP'), dict(trade('4'), px='NaN'),
            dict(trade('5'), side='mystery'), dict(trade('6'), sz='-1')]
    assert trade_flow(rows, INSTRUMENT, NOW)['available'] is False
    assert notional(trade(), {}) is None
    assert notional(trade(), dict(INSTRUMENT, ctValCcy='EUR')) is None
    assert notional(trade(), dict(INSTRUMENT, ctValCcy='USDT', ctVal='10')) == 20


def test_liquidations_are_actual_past_events_with_bankruptcy_prices():
    detail = dict(ts=str(NOW-1000), posSide='long', bkPx='80000', sz='2')
    rows = [{'instId': SYMBOL, 'details': [detail, detail, dict(detail, ts=str(NOW-10800001))]},
            {'instId': 'ETH-USDT-SWAP', 'details': [detail]}]
    result = liquidation_events(rows, INSTRUMENT, NOW)
    assert result['count'] == 1 and result['long_usdt'] == 1600
    assert result['short_usdt'] == 0
    assert '破产价' in result['scope'] and '非未来' in result['scope']


class PublicFixture:
    def __init__(self):
        self.release = Event()
        self.started = Event()
        self.calls = Counter()
        self.lock = Lock()
        self.fail = False

    def get(self, path, **params):
        with self.lock:
            self.calls[path] += 1
        self.started.set()
        self.release.wait(3)
        if self.fail:
            raise ValueError('transport failed')
        symbol = params.get('instId', SYMBOL)
        if path.endswith('instruments'):
            return [dict(INSTRUMENT, instId=symbol)]
        if path.endswith('trades'):
            return [dict(trade(), instId=symbol)]
        if path.endswith('liquidation-orders'):
            return []
        return [{'instId': symbol, 'ts': str(NOW), 'fundingRate': '0', 'oiUsd': '42'}]

    def close(self):
        self.release.set()


def settled(service, symbol=SYMBOL):
    deadline = time.monotonic()+3
    while time.monotonic()<deadline:
        with service.lock:
            if not any(e['pending'] for e in service.cache.values()):
                return service.snapshot(symbol)
        Event().wait(.005)
    raise AssertionError('public fixture did not finish')


def test_snapshot_is_nonblocking_coalesced_and_retires_stale_success():
    http = PublicFixture()
    now = [NOW/1000]
    service = WorkstationData(http, lambda: now[0])
    try:
        start = time.monotonic()
        first = service.snapshot(SYMBOL)
        assert time.monotonic()-start < .1
        assert all(f['pending'] for f in first['fields'].values())
        for _ in range(5):
            service.snapshot(SYMBOL)
        http.release.set()
        data = settled(service)
        assert sum(http.calls.values()) == 7
        assert data['flow']['available']
        assert data['liquidations']['count'] == 0
        assert 'data' not in data['fields']['trades']
        http.fail = True
        now[0] += 11
        service.snapshot(SYMBOL)
        data = settled(service)
        assert not data['fields']['trades']['stale'] and data['fields']['trades']['error']
        assert 'flow' not in data
        now[0] += 20
        service.snapshot(SYMBOL)
        data = settled(service)
        assert data['fields']['trades']['stale'] and data['fields']['trades']['error']
        assert 'flow' not in data
        calls = sum(http.calls.values())
        service.snapshot(SYMBOL)
        assert sum(http.calls.values()) == calls
        with pytest.raises(ValueError):
            service.snapshot('../../BTC')
    finally:
        http.release.set()
        service.close()

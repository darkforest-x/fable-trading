"""Native display bars must not change Monday anchors or the scanner universe."""
import threading
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.data.candle_hub import STEPS, bar_open
from yoyo.monitor.monday_candles import MondayCandles, PERIODS, install
from yoyo.monitor.monday_fib import week_window

NOW = 1792742400000


class Client:
    def __init__(self):
        self.calls = []
        self.now = NOW
        self.fail = False

    def clock(self):
        return self.now

    def get(self, path, params):
        self.calls.append((path, params))
        if self.fail:
            raise RuntimeError('private upstream detail')
        step = STEPS[params['bar']]
        return [[str(int(params['after'])-step*n), '10', '12', '9', '11', '4', '40', '400', '1']
                for n in range(1, 241)]


def run_fetch(service, period):
    # Inline worker invocation avoids sleep-based assertions and external requests.
    entry = dict(candles=[], loading=True, last_close_ms=0)
    service.slots.acquire()
    service._fetch('BTC-USDT-SWAP', period, entry)
    return entry


@pytest.mark.parametrize('period', PERIODS)
def test_native_bar_boundaries_and_complete_history(period):
    client = Client()
    service = MondayCandles(client=client)
    try:
        entry = run_fetch(service, period)
        native = '1Dutc' if period == '1D' else period
        step = STEPS[native]
        assert len(entry['candles']) == 720
        assert all(b['time'] % step == 0 for b in entry['candles'])
        assert entry['candles'][-1]['time'] + step == bar_open(NOW, native)
        assert all(q['bar'] == native for _, q in client.calls)
        assert all(path == '/api/v5/market/history-candles' for path, _ in client.calls)
        assert len(client.calls) == 3
        assert entry['candles'][0]['volume'] == 4  # Contracts, not base or quote units.
        service.cache[('BTC-USDT-SWAP', period)] = entry
        snapshot = service.snapshot('BTC-USDT-SWAP', period)
        window = week_window(NOW)
        assert snapshot['monday'] == dict(start_ms=window['start'], end_ms=window['end'])
        assert 'high' not in snapshot['monday']  # Anchors are never recomputed from display bars.
        assert not snapshot['loading'] and len(client.calls) == 3
    finally:
        service.close()


def test_failure_retains_old_bars_with_explicit_error_and_bounded_nonblocking_work():
    client = Client()
    service = MondayCandles(client=client)
    try:
        entry = run_fetch(service, '15m')
        previous = entry['candles']
        client.fail = True
        service.slots.acquire()
        service._fetch('BTC-USDT-SWAP', '15m', entry)
        assert entry['candles'] == previous
        assert entry['error'] == 'OKX K 线暂不可用，请重试'
        service.slots.acquire(); service.slots.acquire()
        for n in range(30):
            value = service.snapshot(f'C{n}-USDT-SWAP', '1m')
            assert not value['loading'] and value['error']
        assert len(service.cache) == 12
    finally:
        service.close()


def test_single_flight_and_period_isolation():
    client = Client()
    gate, entered = threading.Event(), threading.Event()
    original = client.get
    def blocked(path, params):
        entered.set(); gate.wait(2)
        return original(path, params)
    client.get = blocked
    service = MondayCandles(client=client)
    try:
        assert service.snapshot('BTC-USDT-SWAP', '1m')['loading']
        assert entered.wait(1)
        assert service.snapshot('BTC-USDT-SWAP', '1m')['loading']
        assert len(service.cache) == 1
        assert service.snapshot('BTC-USDT-SWAP', '5m')['loading']
        assert not service.snapshot('BTC-USDT-SWAP', '1D')['loading']
    finally:
        gate.set(); service.close()


def test_api_validates_input_without_upstream_io(tmp_path):
    app = FastAPI(); install(app, tmp_path)
    service = app.state.monday_candles
    service.close()
    with TestClient(app) as client:
        for period in PERIODS:
            response = client.get('/api/monday-fib/candles', params=dict(symbol='BTC-USDT-SWAP', period=period))
            assert response.status_code == 200 and response.json()['bar'] == period
        assert client.get('/api/monday-fib/candles?symbol=BTC-USDT-SWAP&period=1s').status_code == 422
        assert client.get('/api/monday-fib/candles?symbol=https://bad&period=1m').status_code == 422


def test_closed_bar_refresh_reuses_history_and_merges_overlapping_corrections():
    client = Client(); service = MondayCandles(client=client)
    try:
        entry = run_fetch(service, '15m')
        original_start = entry['candles'][0]['time']
        client.now += STEPS['15m']
        original_get = client.get
        def correction(path, params):
            rows = original_get(path, params)
            rows[2][4] = '10.5'
            return rows
        client.get = correction
        service.slots.acquire(); service._fetch('BTC-USDT-SWAP', '15m', entry)
        assert len(client.calls) == 4  # One page, instead of three on every close.
        assert len(entry['candles']) == 720
        assert entry['candles'][0]['time'] == original_start + STEPS['15m']
        assert entry['candles'][-3]['close'] == 10.5
        assert entry['last_close_ms'] == bar_open(client.now, '15m')
        client.now += STEPS['15m'] * 1000
        service.slots.acquire(); service._fetch('BTC-USDT-SWAP', '15m', entry)
        assert len(client.calls) == 7  # No overlap: normal bounded history load.
        assert len(entry['candles']) == 720
    finally:
        service.close()


def test_delayed_upstream_tail_does_not_claim_the_current_close():
    client = Client(); service = MondayCandles(client=client)
    original_get = client.get
    def lagged(path, params):
        rows = original_get(path, params)
        return [r for r in rows if int(r[0]) < bar_open(client.now, '1H') - STEPS['1H']]
    client.get = lagged
    try:
        entry = run_fetch(service, '1H')
        assert entry['last_close_ms'] == bar_open(client.now, '1H') - STEPS['1H']
        service.cache[('BTC-USDT-SWAP', '1H')] = entry
        value = service.snapshot('BTC-USDT-SWAP', '1H')
        assert value['now_ms'] == client.now
        assert value['next_close_ms'] == bar_open(client.now, '1H') + STEPS['1H']
    finally:
        service.close()

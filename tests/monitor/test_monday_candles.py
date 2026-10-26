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
        native = {'12H': '12Hutc', '1D': '1Dutc'}.get(period, period)
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
        assert snapshot['monday'] == dict(start_ms=window['start'], end_ms=window['end'], expires_ms=window['expires'])
        assert 'high' not in snapshot['monday']  # Anchors are never recomputed from display bars.
        assert not snapshot['loading'] and len(client.calls) == 3
    finally:
        service.close()


@pytest.mark.parametrize('period', PERIODS)
def test_live_display_tail_never_enters_the_closed_snapshot(period):
    client = Client()
    native = {'12H': '12Hutc', '1D': '1Dutc'}.get(period, period)
    step = STEPS[native]
    boundary = bar_open(NOW, native)
    class Live(Client):
        def get(self, path, params):
            self.calls.append((path, params))
            assert path == '/api/v5/market/candles'
            assert params == dict(instId='BTC-USDT-SWAP', bar=native, limit='3')
            return [[str(boundary), '10', '18', '2', '11', '4', '40', '400', '0'],
                    [str(boundary-step), '10', '12', '9', '11', '4', '40', '400', '1']]
    live = Live(); service = MondayCandles(client=client, live_client=live)
    try:
        entry = run_fetch(service, period)
        service.cache[('BTC-USDT-SWAP', period)] = entry
        before = service.snapshot('BTC-USDT-SWAP', period)
        display = service.recent('BTC-USDT-SWAP', period)
        assert display['forming_candle']['time'] == boundary
        assert display['forming_candle']['low'] == 2
        assert display['closed_candles'][0]['time'] == boundary-step
        assert display['native_bar'] == native and not display['error']
        assert service.snapshot('BTC-USDT-SWAP', period) == before
        assert all(row['low'] == 9 for row in before['candles'])
        assert len(client.calls) == 3 and len(live.calls) == 1
    finally:
        service.close()


def test_live_failures_and_read_budget_never_leak_or_fabricate_ohlc():
    client = Client(); client.fail = True
    service = MondayCandles(client=client, live_client=client)
    try:
        result = service.recent('BTC-USDT-SWAP', '1H')
        assert result['forming_candle'] is None and result['closed_candles'] == []
        assert result['error'] and 'private' not in str(result)
        service.live_slots.acquire(); service.live_slots.acquire()
        before = len(client.calls)
        assert service.recent('BTC-USDT-SWAP', '4H')['error']
        assert len(client.calls) == before
        assert not service.cache
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


def test_first_response_returns_a_page_before_history_finishes_without_polling():
    client = Client(); service = MondayCandles(client=client)
    older_started, finish_history = threading.Event(), threading.Event()
    original_get = client.get
    def blocked_history(path, params):
        if client.calls:
            older_started.set()
            assert finish_history.wait(2)
        return original_get(path, params)
    client.get = blocked_history
    try:
        first = service.snapshot('BTC-USDT-SWAP', '1H', wait_ms=8000)
        assert older_started.wait(1)
        assert len(first['candles']) == 240 and first['loading']
        assert first['history_complete'] is False
        assert first['last_close_ms'] == bar_open(NOW, '1H')
        assert len(client.calls) == 1
        finish_history.set()
        service.pool.shutdown(wait=True)
        full = service.snapshot('BTC-USDT-SWAP', '1H')
        assert len(full['candles']) == 720 and not full['loading']
        assert full['history_complete'] and len(client.calls) == 3
        assert full['candles'][-240:] == first['candles']
    finally:
        finish_history.set(); service.close()


def test_partial_history_failure_retains_first_screen_and_retries_missing_pages():
    client = Client(); service = MondayCandles(client=client)
    original_get = client.get
    def fail_older(path, params):
        if client.calls: raise RuntimeError('older history offline')
        return original_get(path, params)
    client.get = fail_older
    try:
        entry = run_fetch(service, '15m')
        assert len(entry['candles']) == 240 and entry['error']
        assert not entry['history_complete']
        service.cache[('BTC-USDT-SWAP', '15m')] = entry
        client.get = original_get; client.now += 10001
        service.snapshot('BTC-USDT-SWAP', '15m', wait_ms=8000)
        service.pool.shutdown(wait=True)
        assert len(entry['candles']) == 720 and entry['history_complete']
        assert not entry['error'] and len(client.calls) == 4
    finally:
        service.close()


def test_waiting_clients_are_bounded_and_shutdown_releases_a_cold_waiter():
    client = Client(); service = MondayCandles(client=client)
    entered, release = threading.Event(), threading.Event()
    def blocked(path, params):
        entered.set(); release.wait(2); raise RuntimeError('offline')
    client.get = blocked
    result = []
    thread = threading.Thread(target=lambda: result.append(service.snapshot('BTC-USDT-SWAP', '1m', 8000)))
    try:
        for _ in range(8): assert service.waiters.acquire(False)
        # Exhausting the wait budget never blocks or creates duplicate jobs.
        assert service.snapshot('BTC-USDT-SWAP', '1m', 8000)['loading']
        assert entered.wait(1)
        for _ in range(8): service.waiters.release()
        thread.start(); service.close(); thread.join(1)
        assert not thread.is_alive() and result[0]['candles'] == []
    finally:
        release.set(); service.close()


def test_extrema_api_is_read_only_validates_symbol_and_hides_upstream_errors(tmp_path, monkeypatch):
    from yoyo.monitor.monday_chart_levels import MondayChartLevels
    seen = []
    def read(self, symbol, at):
        seen.append((symbol, at))
        if symbol == 'ETH-USDT-SWAP':
            raise RuntimeError('private upstream detail')
        return dict(symbol=symbol, state='ready', high_ms=123, low_ms=456)
    monkeypatch.setattr(MondayChartLevels, 'read', read)
    app = FastAPI(); install(app, tmp_path)
    service = app.state.monday_candles
    monkeypatch.setattr(service.client, 'clock', lambda: NOW)
    service.close()
    with TestClient(app) as client:
        response = client.get('/api/monday-fib/levels?symbol=BTC-USDT-SWAP')
        assert response.status_code == 200 and response.json()['high_ms'] == 123
        assert seen == [('BTC-USDT-SWAP', NOW)]
        assert client.get('/api/monday-fib/levels?symbol=https://bad').status_code == 422
        assert len(seen) == 1
        failure = client.get('/api/monday-fib/levels?symbol=ETH-USDT-SWAP')
        assert failure.status_code == 503 and 'private' not in failure.text
        assert client.post('/api/monday-fib/levels?symbol=BTC-USDT-SWAP').status_code == 405

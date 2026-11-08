"""Historical browsing stays separate from bounded analytical snapshots."""
from types import SimpleNamespace
from datetime import datetime, timezone

from yoyo.data.candle_hub import STEPS, bar_open
from yoyo.monitor.monday_candles import MondayCandles
from yoyo.monitor.monday_chart_history import ChartHistory
from yoyo.monitor.monday_fib import week_window

NOW = 1793016000000


class Client:
    def __init__(self):
        self.calls, self.empty, self.fail, self.malformed = [], False, False, False
        self.first = bar_open(NOW, '1Dutc') - 1000 * STEPS['1Dutc']
        self.history_first = self.first
        self.page_size = 300

    def clock(self):
        return NOW

    def get(self, path, params):
        self.calls.append((path, params))
        if path.endswith('/instruments'):
            return [dict(instId=params['instId'], listTime=str(self.first))]
        if self.fail:
            raise RuntimeError('history_request_failed')
        if self.empty:
            return []
        if self.malformed:
            return None
        step, before = STEPS[params['bar']], int(params['after'])
        count = min(self.page_size, int(params['limit']))
        return [[str(t), '10', '12', '9', '11', '4', '40', '400', '1']
                for t in range(before-step, max(self.history_first-step, before-(count+1)*step), -step)]


def service():
    client = Client()
    return ChartHistory(SimpleNamespace(client=client, _candles=MondayCandles._candles), None), client


def test_paper_trade_window_uses_historical_clocks_for_altcoins_and_reuses_cache():
    history, client = service()
    entry = bar_open(NOW, '15m') - 5000 * STEPS['15m']
    exit_ms = entry + 7 * STEPS['15m'] + 1234
    calls = []
    history.levels = SimpleNamespace(read_display=lambda symbol, at:
        calls.append((symbol, at)) or dict(symbol=symbol, state='ready', week='2026-08-31'))
    value = history.trade_window('SOFI-USDT-SWAP', '15m', entry, exit_ms)
    assert value['source'] == 'OKX' and value['paperHistory']
    assert value['candles'][0]['time'] == entry - 240 * STEPS['15m']
    assert value['candles'][-1]['time'] == bar_open(exit_ms, '15m') + 80 * STEPS['15m']
    assert calls == [('SOFI-USDT-SWAP', entry)]
    count = len(client.calls)
    value['candles'].clear()
    assert history.trade_window('SOFI-USDT-SWAP', '15m', entry, exit_ms)['candles']
    assert len(client.calls) == count
    page = history.read('SOFI-USDT-SWAP', '15m', entry)
    assert page['state'] == 'ready' and len(page['candles']) == 300
    assert client.calls[-1][1]['limit'] == '300'


def test_paper_trade_window_aggregates_2m_without_inventing_bars_and_rejects_missing_fills():
    import pytest
    history, client = service()
    entry = NOW // 120000 * 120000 - 2000 * 120000
    history.levels = SimpleNamespace(read_display=lambda *args: dict(state='ready'))
    value = history.trade_window('SOFI-USDT-SWAP', '2m', entry, entry + 10 * 120000)
    assert len(value['candles']) == 331
    assert all(c['volume'] == 8 and c['time'] % 120000 == 0 for c in value['candles'])
    assert all(p['bar'] == '1m' for _, p in client.calls)
    client.empty = True
    with pytest.raises(RuntimeError, match='开平仓'):
        history.trade_window('HOOD-USDT-SWAP', '2m', entry, entry + 120000)
    with pytest.raises(ValueError, match='时间'):
        history.trade_window('HOOD-USDT-SWAP', '2m', entry, entry - 1)
    with pytest.raises(ValueError, match='跨度'):
        history.trade_window('HOOD-USDT-SWAP', '1m', entry - 600000000, entry)
    assert len(history.windows) == 1


def test_history_pages_continue_until_okx_returns_an_empty_api_boundary():
    history, client = service()
    cursor, times = bar_open(NOW, '1Dutc'), []
    while True:
        page = history.read('BTC-USDT-SWAP', '1D', cursor)
        assert page['native_bar'] == '1Dutc'
        if page['candles']:
            assert page['next_before_ms'] < cursor
            assert all(b['time'] < cursor for b in page['candles'])
            assert page['has_more'] and page['state'] == 'ready'
        else:
            assert page['state'] == 'exhausted'
            assert page['has_more'] is False
            assert page['end_reason'] == 'exchange_api_boundary'
            break
        times += [b['time'] for b in page['candles']]
        cursor = page['next_before_ms']
    assert len(times) == len(set(times)) == 1000
    assert min(times) == client.first
    history_calls = [params for path, params in client.calls if path.endswith('/history-candles')]
    assert all(params['limit'] == '300' for params in history_calls)
    before = len(client.calls)
    assert history.read('BTC-USDT-SWAP', '1D', cursor)['state'] == 'exhausted'
    assert len(client.calls) == before + 1


def test_all_selected_swap_symbols_can_page_history():
    history, client = service()
    for symbol in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'XAU-USDT-SWAP',
                   'SOL-USDT-SWAP', 'DOGE-USDT-SWAP', 'SOFI-USDT-SWAP'):
        page = history.read(symbol, '5m', NOW)
        assert page['state'] == 'ready' and len(page['candles']) == 300
        assert page['source'] == 'OKX' and page['has_more']


def test_short_pages_do_not_stop_before_an_older_page_and_api_boundary():
    history, client = service()
    client.page_size = 200
    client.history_first = client.first + 17 * STEPS['1Dutc']
    cursor, times, pages = bar_open(NOW, '1Dutc'), [], []
    while True:
        page = history.read('DOGE-USDT-SWAP', '1D', cursor)
        if not page['candles']:
            assert page['state'] == 'exhausted'
            assert page['end_reason'] == 'exchange_api_boundary'
            break
        pages.append(page)
        times.extend(row['time'] for row in page['candles'])
        assert page['state'] == 'ready' and page['has_more']
        assert page['next_before_ms'] < cursor
        cursor = page['next_before_ms']
    assert len(pages) > 1
    assert all(len(page['candles']) <= 200 for page in pages)
    assert len(pages[-1]['candles']) < 200
    assert min(times) == client.history_first
    assert len(times) == len(set(times)) == 983


def test_half_day_history_uses_utc_native_pages_and_aligned_cursor():
    history, client = service()
    cursor = bar_open(NOW, '12Hutc')
    page = history.read('BTC-USDT-SWAP', '12H', cursor)
    assert page['native_bar'] == '12Hutc'
    assert len(page['candles']) == 300
    assert page['next_before_ms'] == cursor - 300 * STEPS['12Hutc']
    assert all(b['time'] % STEPS['12Hutc'] == 0 for b in page['candles'])
    assert client.calls[-1][1]['bar'] == '12Hutc'


def test_history_page_cache_is_short_lived_and_returns_isolated_payload(monkeypatch):
    from yoyo.monitor import monday_chart_history
    from yoyo.monitor.monday_chart_history import HISTORY_PAGE_CACHE_SECONDS
    clock = [10.0]
    monkeypatch.setattr(monday_chart_history, 'monotonic', lambda: clock[0])
    history, client = service()
    first = history.read('BTC-USDT-SWAP', '15m', NOW)
    assert first['state'] == 'ready' and len(first['candles']) == 300
    calls = len(client.calls)
    first['candles'][0]['close'] = -1
    first['candles'].clear()

    cached = history.read('BTC-USDT-SWAP', '15m', NOW)
    assert len(client.calls) == calls
    assert len(cached['candles']) == 300 and cached['candles'][0]['close'] == 11.0
    clock[0] += HISTORY_PAGE_CACHE_SECONDS + .001
    assert history.read('BTC-USDT-SWAP', '15m', NOW)['state'] == 'ready'
    assert len(client.calls) == calls + 1


def test_chart_read_routes_return_jsonresponses_without_changing_payload(tmp_path):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from yoyo.monitor.monday_candles import install

    app = FastAPI()
    install(app, tmp_path)
    candles = app.state.monday_candles
    candle_value = dict(symbol='BTC-USDT-SWAP', bar='15m', candles=[dict(
        time=NOW-900000, open=10.0, high=12.0, low=9.0, close=11.0, volume=4.0)],
        loading=False, now_ms=NOW, next_close_ms=NOW+1000, native_bar='15m', source='OKX')
    history_value = dict(symbol='BTC-USDT-SWAP', bar='15m', native_bar='15m', source='OKX',
        before_ms=NOW, candles=[], next_before_ms=NOW, has_more=True, state='retry', error='可重试')
    candles.snapshot = lambda symbol, period, wait_ms: candle_value
    app.state.monday_chart_history.read = lambda symbol, period, before_ms: history_value
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, 'path')}
    try:
        candle_response = routes['/api/monday-fib/candles']('BTC-USDT-SWAP', '15m', 0)
        history_response = routes['/api/monday-fib/history']('BTC-USDT-SWAP', '15m', NOW)
        assert isinstance(candle_response, JSONResponse) and candle_response.status_code == 200
        assert isinstance(history_response, JSONResponse) and history_response.status_code == 200
        assert candle_response.body == JSONResponse(candle_value).body
        assert history_response.body == JSONResponse(history_value).body
    finally:
        candles.close()


def test_concurrent_duplicate_history_pages_share_one_upstream_read():
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    history, client = service()
    original_get = client.get
    calls = []
    calls_lock = threading.Lock()

    def slow_get(path, params):
        with calls_lock:
            calls.append((path, params))
        time.sleep(.05)
        return original_get(path, params)

    client.get = slow_get
    barrier = threading.Barrier(8)

    def read():
        barrier.wait()
        return history.read('ETH-USDT-SWAP', '15m', NOW)

    with ThreadPoolExecutor(max_workers=8) as pool:
        pages = list(pool.map(lambda _: read(), range(8)))
    assert len(calls) == 1
    assert all(page['state'] == 'ready' and len(page['candles']) == 300 for page in pages)


def test_valid_empty_response_marks_api_boundary_but_request_failure_retries():
    history, client = service()
    client.empty = True
    page = history.read('ETH-USDT-SWAP', '15m', NOW)
    assert page['state'] == 'exhausted' and not page['has_more']
    assert page['end_reason'] == 'exchange_api_boundary'
    assert page['next_before_ms'] == page['before_ms']
    client.empty = False
    client.malformed = True
    assert history.read('ETH-USDT-SWAP', '15m', NOW)['state'] == 'retry'
    client.malformed = False
    client.fail = True
    assert history.read('ETH-USDT-SWAP', '15m', NOW)['state'] == 'retry'
    # All slots busy: a read waits briefly, then reports retry without calling OKX.
    from yoyo.monitor.monday_chart_history import HISTORY_SLOTS
    history.slot_wait = 0.01
    for _ in range(HISTORY_SLOTS):
        history.slots.acquire()
    try:
        before = len(client.calls)
        assert history.read('ETH-USDT-SWAP', '15m', NOW)['state'] == 'retry'
        assert len(client.calls) == before
    finally:
        for _ in range(HISTORY_SLOTS):
            history.slots.release()


def test_concurrent_history_reads_queue_instead_of_failing():
    # Owner 2026-11-06: a second chart paging at the same time must not get an instant retry.
    import threading
    history, client = service()
    history.slots.acquire()
    try:
        assert history.read('ETH-USDT-SWAP', '15m', NOW)['state'] == 'ready'
    finally:
        history.slots.release()
    # Exercise the queue path independently of the short duplicate-page cache.
    history.pages.clear()
    from yoyo.monitor.monday_chart_history import HISTORY_SLOTS
    for _ in range(HISTORY_SLOTS):
        history.slots.acquire()
    results = []
    worker = threading.Thread(target=lambda: results.append(history.read('ETH-USDT-SWAP', '15m', NOW)['state']))
    worker.start()
    history.slots.release()
    worker.join(timeout=5)
    for _ in range(HISTORY_SLOTS - 1):
        history.slots.release()
    assert results == ['ready']


def test_week_cursor_covers_each_week_once_across_dst_and_keeps_missing_state():
    history, client = service()
    # The spring DST week is 167 hours. Window boundaries come from NY calendar.
    stamp = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()*1000)
    start, end = stamp('2026-02-23T05:00:00'), stamp('2026-03-23T04:00:00')
    calls = []
    def read(symbol, at):
        window = week_window(at)
        calls.append(at)
        return dict(symbol=symbol, week=window['week'], start_ms=window['start'],
                    expires_ms=window['expires'], state='missing_monday')
    history.levels = SimpleNamespace(read=read)
    first = history.weeks('DOGE-USDT-SWAP', start, end)
    assert len(first['weeks']) == 3 and first['next_before_ms'] > start
    second = history.weeks('DOGE-USDT-SWAP', start, first['next_before_ms'])
    rows = first['weeks'] + second['weeks']
    assert [w['week'] for w in rows] == ['2026-03-16', '2026-03-09', '2026-03-02', '2026-02-23']
    assert any(w['expires_ms']-w['start_ms'] == 167*3600000 for w in rows)
    assert all(w['state'] == 'missing_monday' for w in rows)
    assert second['next_before_ms'] is None


def test_two_minute_pages_are_complete_utc_pairs_with_progressing_cursors():
    history, client = service()
    before=NOW//120000*120000
    first=history.read('BTC-USDT-SWAP','2m',before)
    second=history.read('BTC-USDT-SWAP','2m',first['next_before_ms'])
    assert first['native_bar']=='1m' and len(first['candles'])==150
    assert all(b['time']%120000==0 and b['volume']==8 for b in first['candles'])
    assert max(b['time'] for b in second['candles'])<min(b['time'] for b in first['candles'])

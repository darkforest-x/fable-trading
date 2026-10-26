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
        self.calls, self.empty = [], False
        self.first = bar_open(NOW, '1Dutc') - 1000 * STEPS['1Dutc']

    def clock(self):
        return NOW

    def get(self, path, params):
        self.calls.append((path, params))
        if path.endswith('/instruments'):
            return [dict(instId=params['instId'], listTime=str(self.first))]
        if self.empty:
            return []
        step, before = STEPS[params['bar']], int(params['after'])
        return [[str(t), '10', '12', '9', '11', '4', '40', '400', '1']
                for t in range(before-step, max(self.first-step, before-301*step), -step)]


def service():
    client = Client()
    return ChartHistory(SimpleNamespace(client=client, _candles=MondayCandles._candles), None), client


def test_pages_pass_720_without_mutating_live_cache_and_stop_at_listing():
    history, client = service()
    cursor, times = bar_open(NOW, '1Dutc'), []
    while True:
        page = history.read('BTC-USDT-SWAP', '1D', cursor)
        assert page['native_bar'] == '1Dutc'
        assert page['next_before_ms'] < cursor
        assert all(b['time'] < cursor for b in page['candles'])
        times += [b['time'] for b in page['candles']]
        cursor = page['next_before_ms']
        if not page['has_more']:
            assert page['state'] == 'exhausted'
            break
    assert len(times) == len(set(times)) == 1000
    assert min(times) == client.first
    assert len([p for p, _ in client.calls if p.endswith('/instruments')]) == 1
    before = len(client.calls)
    assert history.read('BTC-USDT-SWAP', '1D', cursor)['state'] == 'exhausted'
    assert len(client.calls) == before


def test_all_three_shortcuts_and_alt_no_io():
    history, client = service()
    for symbol in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'XAU-USDT-SWAP'):
        assert len(history.read(symbol, '5m', NOW)['candles']) == 300
    before = len(client.calls)
    assert history.read('DOGE-USDT-SWAP', '5m', NOW)['state'] == 'limited'
    assert len(client.calls) == before


def test_half_day_history_uses_utc_native_pages_and_aligned_cursor():
    history, client = service()
    cursor = bar_open(NOW, '12Hutc')
    page = history.read('BTC-USDT-SWAP', '12H', cursor)
    assert page['native_bar'] == '12Hutc'
    assert len(page['candles']) == 300
    assert page['next_before_ms'] == cursor - 300 * STEPS['12Hutc']
    assert all(b['time'] % STEPS['12Hutc'] == 0 for b in page['candles'])
    assert client.calls[-1][1]['bar'] == '12Hutc'


def test_empty_page_and_busy_budget_are_retryable_not_false_exhaustion():
    history, client = service()
    client.empty = True
    page = history.read('ETH-USDT-SWAP', '15m', NOW)
    assert page['state'] == 'retry' and page['has_more']
    assert page['next_before_ms'] == page['before_ms']
    history.slots.acquire()
    try:
        before = len(client.calls)
        assert history.read('ETH-USDT-SWAP', '15m', NOW)['state'] == 'retry'
        assert len(client.calls) == before
    finally:
        history.slots.release()


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

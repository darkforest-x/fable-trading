"""Causal data and command durability checks; synthetic sources, no live IO."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from yoyo.discipline_app.replay_history import ReplayHistory, ReplayError
from yoyo.discipline_app.replay_store import ReplayStore, statistics
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book

S = 'BTC-USDT-SWAP'
START = 1773100800  # 2026-03-10 00:00 UTC


def bar(t, c=100):
    return dict(time=t, open=100., high=max(105., c), low=min(95., c), close=float(c), volume=10.)


def raw(item, confirmed='1'):
    return [str(item['time']*1000), *[str(item[k]) for k in ('open', 'high', 'low', 'close', 'volume')], '1', '100', confirmed]


class Relay:
    def __init__(self, data):
        self.data, self.calls = data, []

    def read(self, *args):
        self.calls.append(args)
        return dict(data=self.data)


def test_online_exact_clock_filters_future_and_unconfirmed():
    relay = Relay([raw(bar(START)), raw(bar(START-300)), raw(bar(START-600), '0')])
    history = ReplayHistory(relay, roots=[], clock=lambda:START+3600)
    rows = history.initial(S, '5m', 'okx_online', START+30)
    assert [r['time'] for r in rows] == [START-300]
    assert relay.calls == [(S, '5m', START+31)]
    with pytest.raises(ReplayError, match='覆盖'):
        history.initial(S, '5m', 'okx_online', START+900)


def test_context_and_advance_never_bridge_missing_bars():
    relay = Relay([raw(bar(t)) for t in (START-900, START-300, START, START+600)])
    history = ReplayHistory(relay, roots=[], clock=lambda:START+10000)
    assert len(history.initial(S, '5m', 'okx_online', START)) == 1
    config = dict(symbol=S, period='5m', source='okx_online')
    assert [r['time'] for r in history.advance(config, START, 3)] == [START]
    with pytest.raises(ReplayError, match='缺口'):
        history.advance(config, START+300, 1)


def test_monday_closed_hourly_only_and_dst():
    monday = int(datetime(2026, 3, 9, 4, tzinfo=timezone.utc).timestamp())
    relay = Relay([raw(bar(monday+i*3600)) for i in range(24)])
    history = ReplayHistory(relay, roots=[], clock=lambda:monday+10*86400)
    config = dict(symbol=S, source='okx_online')
    assert history.monday(config, monday+23*3600)['state'] == 'developing'
    assert relay.calls == []
    result = history.monday(config, monday+86400)
    assert result['start'] == monday
    assert result['high'] == 105 and result['lower'] == pytest.approx(88.82)
    relay.data.pop()
    assert history.monday(config, monday+86400)['state'] == 'missing_monday'


def test_local_resamples_only_complete_bars(tmp_path):
    path = tmp_path/'okx_BTC_USDT_SWAP_15m_8.csv'
    path.write_text('ts,open,high,low,close,volume\n'+''.join(f'{(START+i*900)*1000},100,105,95,{100+i},2\n' for i in range(8)))
    history = ReplayHistory(roots=[tmp_path], clock=lambda:START+86400)
    assert dict(symbol=S, period='1H') in history.catalog()['local']
    rows = history.read(S, '1H', 'okx_local', START+3700)
    assert len(rows) == 1 and rows[0]['close'] == 103 and rows[0]['volume'] == 8
    # The next hour intentionally contains invalid future OHLC. Reading it
    # later fails, but prefetched prices cannot block the valid first hour.
    with pytest.raises(ReplayError, match='无效价格'):
        history.read(S, '1H', 'okx_local', START+7200)
    with pytest.raises(ReplayError, match='没有'):
        history.read(S, '1m', 'okx_local', START+3600)


def test_local_coarse_file_does_not_hide_older_fine_history(tmp_path):
    (tmp_path/'okx_BTC_USDT_SWAP_1H_2.csv').write_text('ts,open,high,low,close,volume\n'+f'{(START+86400)*1000},100,105,95,101,2\n')
    (tmp_path/'okx_BTC_USDT_SWAP_15m_4.csv').write_text('ts,open,high,low,close,volume\n'+''.join(f'{(START+i*900)*1000},100,105,95,101,2\n' for i in range(4)))
    history = ReplayHistory(roots=[tmp_path], clock=lambda:START+2*86400)
    rows = history.read(S, '1H', 'okx_local', START+3600)
    assert len(rows) == 1 and rows[0]['volume'] == 8


class History:
    def initial(self, *args):
        return [bar(START-600), bar(START-300)]

    def advance(self, config, clock, count):
        return [bar(clock+i*300) for i in range(count)]

    def monday(self, config, clock):
        return dict(state='ready', week='2026-03-02', start=START-8*86400,
                    end=START-7*86400, expires=START+86400, high=103, low=97, upper=106.708, lower=93.292)

    def catalog(self):
        return dict(local=[], periods=['5m'], sources=[])


class Worker:
    """Protocol stub only; financial execution is separately tested natively."""
    created = []

    def __init__(self, config):
        self.commands, self.stopped = [], False
        self.state = dict(clock=0, price='100', cash=config['cash'], equity=config['cash'],
                          available=config['cash'], fees='0', positions=[], orders=[], closed=[], fills=[])
        self.created.append(self)

    def call(self, payload):
        self.commands.append(deepcopy(payload))
        if payload['op'] == 'advance':
            self.state['clock'] = payload['bars'][-1]['time']+300
        if payload['op'] in ('order', 'orders'):
            self.state['orders'] += deepcopy(payload.get('items', [payload]))
        return deepcopy(self.state)

    def close(self):
        self.stopped = True


def create(store, rid='a'*32):
    return store.create(dict(request_id=rid, symbol=S, period='5m', source='okx_online', start=START, cash='1000'))


def test_command_idempotence_stale_version_and_durable_restore(tmp_path):
    store = ReplayStore(tmp_path/'r.sqlite', History(), Worker)
    first = create(store)
    body = dict(request_id='b'*32, version=0, action='advance', params=dict(count=2))
    after = store.command(first['id'], body)
    assert after['clock'] == START+600 and after['version'] == 1
    assert store.command(first['id'], body) == after
    with pytest.raises(ReplayError, match='另一处'):
        store.command(first['id'], dict(body, request_id='c'*32))
    store.stop()
    restored = ReplayStore(tmp_path/'r.sqlite', History(), Worker)
    last = restored.command(first['id'], dict(body, request_id='d'*32, version=1, params=dict(count=1)))
    assert last['clock'] == START+900
    assert len(Worker.created[-1].commands) == 4  # seed, two advances, next advance
    assert len(last['bars']) == 5
    restored.stop()


def test_sessions_and_monday_actions_are_isolated_and_deduplicated(tmp_path):
    store = ReplayStore(tmp_path/'r.sqlite', History(), Worker)
    a, b = create(store), create(store, 'b'*32)
    request = dict(request_id='c'*32, version=0, action='monday', params=dict(notional='100', take_profit='near'))
    result = store.command(a['id'], request)
    assert len(result['orders']) == 2
    assert result['orders'][0]['price'] == '93.29200000'
    assert result['orders'][0]['target'] == '97.00000000'
    assert store.get(b['id'])['orders'] == []
    with pytest.raises(ReplayError, match='已挂过'):
        store.command(a['id'], dict(request, request_id='d'*32, version=1))
    assert store.get(a['id'])['version'] == 1
    store.stop()


def test_worker_failure_discards_uncommitted_memory_and_keeps_journal(tmp_path):
    class Failing(Worker):
        def call(self, payload):
            result = super().call(payload)
            if payload.get('notional') == '13':
                raise ReplayError('拒绝')
            return result
    store = ReplayStore(tmp_path/'r.sqlite', History(), Failing)
    first = create(store)
    body = dict(request_id='b'*32, version=0, action='order', params=dict(side='long', kind='market', notional='13'))
    with pytest.raises(ReplayError, match='拒绝'):
        store.command(first['id'], body)
    assert store.get(first['id'])['orders'] == []
    assert not store.workers
    assert Worker.created[-1].stopped


def test_stats_include_mark_to_market_but_win_rate_closed_only():
    native = dict(equity='1030', cash='1020', closed=[dict(net_pnl=p) for p in (-10, -20, 50)])
    stats = statistics(native, '1000', [dict(equity=p) for p in (1000, 1100, 990, 1030)])
    assert stats['net_pnl'] == 30 and stats['realized_pnl'] == 20
    assert stats['win_rate'] == pytest.approx(100/3)
    assert stats['payoff'] == pytest.approx(50/15)
    assert stats['max_drawdown'] == 110 and stats['max_drawdown_pct'] == 10
    assert stats['longest_loss_streak'] == 2


def test_authenticated_replay_routes_never_resolve_live_account(tmp_path):
    book = Book(tmp_path/'paper.sqlite')
    book.resolve = lambda *_: pytest.fail('Replay must not resolve a real account')
    app = create_app(book, 's'*32, market_enabled=False)
    app.state.replay = ReplayStore(tmp_path/'replay.sqlite', History(), Worker)
    body = dict(request_id='a'*32, symbol=S, period='5m', source='okx_online', start=START, cash='1000')
    with TestClient(app) as client:
        assert client.post('/v1/replay/sessions', json=body).status_code == 401
        headers = {'Authorization': 'Bearer '+'s'*32, 'X-Spike-Account': 'unknown-real-account'}
        response = client.post('/v1/replay/sessions', json=body, headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['initial_cash'] == '1000'
        assert client.get('/v1/replay/sessions', headers=headers).json()['items'][0]['id'] == result['id']
    assert Worker.created[-1].stopped

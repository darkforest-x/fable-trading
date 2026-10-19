"""Account display fault injection. All exchange calls use an offline fake."""
from dataclasses import asdict
from decimal import Decimal
import json
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.okx_execution.client import APIError
from yoyo.okx_execution.config import Config, write_private
from yoyo.okx_execution.dashboard import Dashboard, account_snapshot, install, ledger_snapshot, pending, trade_row
from yoyo.okx_execution.store import Store


class Exchange:
    def __init__(self):
        self.calls, self.fail, self.positions = [], None, []

    def get(self, path, **query):
        self.calls.append((path, query))
        if self.fail:
            raise self.fail
        if path.endswith('/config'):
            return [dict(acctLv='2', posMode='net_mode', perm='read_only,trade', uid='PRIVATE_SENTINEL')]
        if path.endswith('/balance'):
            return [dict(details=[dict(ccy='USDT', eq='125', availBal='125', availEq='125', cashBal='125', frozenBal='0', upl='0')])]
        if path.endswith('/positions'):
            return self.positions
        return []


@pytest.fixture
def service(tmp_path):
    write_private(tmp_path / 'config.json', asdict(Config()))
    write_private(tmp_path / 'credentials.json', dict(api_key='PRIVATE_SENTINEL', secret='PRIVATE_SENTINEL', passphrase='PRIVATE_SENTINEL', demo=False))
    exchange = Exchange()

    def factory(config, credentials, *, writes):
        assert writes is False
        return exchange

    dashboard = Dashboard(tmp_path, factory)
    yield dashboard, exchange, tmp_path
    dashboard.close()


def settle(dashboard):
    dashboard.snapshot()
    dashboard.future.result(timeout=3)
    return dashboard.snapshot()


def test_readonly_snapshot_preserves_zero_and_reports_real_blocker(service):
    dashboard, exchange, path = service
    before = sorted(p.name for p in path.iterdir())
    value = settle(dashboard)
    assert value['account']['status'] == 'ready'
    assert value['account']['data']['balance']['eq'] == '125'
    assert value['account']['data']['position_count'] == 0
    assert value['account']['data']['balance']['upl'] == '0'
    assert value['blockers'] == ['position_mode']
    assert value['ledger']['worker']['status'] == 'not_started'
    assert value['ledger']['worker']['accepting_entries'] is None
    assert value['config']['max_positions'] is None
    assert Decimal(value['config']['nominal_risk_usdt']) == Decimal('2.5')
    assert 'PRIVATE_SENTINEL' not in json.dumps(value)
    assert sorted(p.name for p in path.iterdir()) == before
    assert len(exchange.calls) == 8
    settle(dashboard)
    assert len(exchange.calls) == 8  # cached, no poll storm


def test_network_failure_retains_last_good_as_stale_not_empty(service):
    dashboard, exchange, _ = service
    settle(dashboard)
    exchange.fail = APIError('PRIVATE_SENTINEL')
    dashboard.attempt_ms = 0
    value = settle(dashboard)
    assert value['account']['status'] == 'error'
    assert value['account']['data']['balance']['eq'] == '125'
    assert value['account']['error'] == 'account_unavailable'
    assert 'PRIVATE_SENTINEL' not in json.dumps(value)


def test_first_failure_is_unknown_not_flat(service):
    dashboard, exchange, _ = service
    exchange.fail = APIError('50113')
    value = settle(dashboard)
    assert value['account']['data'] is None
    assert value['account']['error'] == 'okx_50113'
    assert value['account']['status'] == 'error'


def test_key_removal_discards_previous_account(service):
    dashboard, _, path = service
    settle(dashboard)
    (path / 'credentials.json').unlink()
    value = dashboard.snapshot()
    assert value['account']['data'] is None
    assert value['account']['status'] == 'unconfigured'


def test_rotation_during_inflight_read_does_not_publish_old_account(service):
    dashboard, exchange, path = service
    started, release = threading.Event(), threading.Event()
    get = exchange.get

    def slow(path, **query):
        started.set()
        assert release.wait(2)
        return get(path, **query)

    exchange.get = slow
    dashboard.snapshot()
    assert started.wait(1)
    (path / 'credentials.json').unlink()
    assert dashboard.snapshot()['account']['data'] is None
    release.set()
    dashboard.future.result(timeout=3)
    assert dashboard.snapshot()['account']['data'] is None


@pytest.mark.parametrize('headers', [
    {'Origin':'https://untrusted.example'}, {'Sec-Fetch-Site':'cross-site'},
    {'Origin':'http://127.0.0.1:9999'},
])
def test_foreign_origin_denied_before_any_account_read(headers):
    class NeverRead:
        def snapshot(self, **kwargs):
            raise AssertionError('must deny before reading accounts')

    app = FastAPI()
    install(app, NeverRead())
    with TestClient(app, base_url='http://127.0.0.1:8766') as client:
        assert client.get('/api/okx-execution/status', headers=headers).status_code == 403


@pytest.mark.parametrize('peer,headers', [
    ('127.0.0.1', {}),
    ('192.0.2.10', {}),
    ('127.0.0.1', {'X-Fable-Gateway':'public','Origin':'http://127.0.0.1:8766','Sec-Fetch-Site':'same-origin'}),
    ('192.0.2.10', {'X-Forwarded-For':'192.0.2.20','X-Forwarded-Proto':'https'}),
])
def test_local_and_remote_routes_return_only_sanitized_readonly_data(service, peer, headers):
    dashboard, exchange, _ = service
    settle(dashboard)
    before = list(exchange.calls)
    app = FastAPI()
    install(app, dashboard)
    with TestClient(app, base_url='http://127.0.0.1:8766', client=(peer, 10000)) as client:
        response = client.get('/api/okx-execution/status', headers=headers)
        assert response.status_code == 200
        assert 'PRIVATE_SENTINEL' not in response.text
        assert response.json()['read_only'] is True
        assert response.json()['account']['data']['balance']['eq'] == '125'
        assert client.post('/api/okx-execution/status', headers=headers).status_code == 405
    assert exchange.calls == before  # Remote viewers share the same GET cache.


def test_worker_liveness_is_not_inferred_from_heartbeat_alone(tmp_path):
    store = Store(tmp_path)
    now = int(time.time() * 1000)
    store.set_meta('cutoff_ms', now)
    store.set_meta('heartbeat_ms', now)
    assert ledger_snapshot(tmp_path, now)['worker']['status'] == 'stopped'
    with store.lock():
        assert ledger_snapshot(tmp_path, now)['worker']['status'] == 'running'
        assert ledger_snapshot(tmp_path, now + 70_000)['worker']['status'] == 'unresponsive'
    assert ledger_snapshot(tmp_path, now)['worker']['status'] == 'stopped'


def test_legacy_skipped_identity_and_reason_are_visible_without_raw_errors():
    value = trade_row(dict(state='skipped', reason='exchange quote is stale',
        event=dict(symbol='XLM-USDT-SWAP',timeframe='15m',side='short',bar_close_ms=1792171800000)))
    assert value['symbol'] == 'XLM-USDT-SWAP' and value['timeframe'] == '15m'
    assert value['side'] == 'short' and value['reason_code'] == 'quote_stale'
    assert value['signal_ms'] == 1792171800000
    unsafe = trade_row(dict(state='skipped',reason='PRIVATE_SENTINEL',reason_code='PRIVATE_SENTINEL'))
    assert 'PRIVATE_SENTINEL' not in json.dumps(unsafe)


def test_waiting_read_has_no_position_and_exports_retry_metadata(tmp_path):
    store = Store(tmp_path)
    store.save(dict(id='waiting',state='retry_pending',reason_code='read_unavailable',attempts=2,
                    next_retry_ms=1792171800000,attempted_ms=1792171796000,
                    event=dict(symbol='XLM-USDT-SWAP',timeframe='15m',side='short')))
    value = ledger_snapshot(tmp_path, int(time.time()*1000))
    assert value['waiting'] == 1 and value['active'] == 0
    assert value['items'][0]['attempts'] == 2
    assert value['items'][0]['reason_code'] == 'read_unavailable'


def test_corrupt_ledger_is_unavailable_not_empty(tmp_path):
    (tmp_path / 'execution.sqlite3').write_text('broken')
    value = ledger_snapshot(tmp_path, int(time.time()*1000))
    assert value['status'] == 'unavailable'
    assert value['total'] is None


def test_net_pnl_requires_complete_receipts_not_reference_returns(tmp_path):
    trade = dict(id='one', state='closed', symbol='ALT-USDT-SWAP', timeframe='1H', side='long',
                 event={'performance': {'realized_r': 99999}}, entry_order=dict(fee='-.10', feeCcy='USDT'),
                 exit_order=dict(pnl='2',fee='-.11',feeCcy='USDT'), secret='PRIVATE_SENTINEL')
    assert trade_row(trade)['realized_net_usdt'] == '1.79'
    assert 'performance' not in json.dumps(trade_row(trade))
    trade['exit_order'].pop('fee')
    assert trade_row(trade)['realized_net_usdt'] is None
    store = Store(tmp_path)
    for i in range(55):
        store.save(dict(trade, id=str(i)))
    value = ledger_snapshot(tmp_path, int(time.time()*1000))
    assert value['closed'] == 55 and len(value['items']) == 50
    assert 'PRIVATE_SENTINEL' not in json.dumps(value)


def test_pagination_queries_cursor_and_flags_display_bound():
    class Pages:
        def __init__(self):
            self.queries = []

        def get(self, path, **query):
            self.queries.append(query)
            start = len(self.queries) * 100
            return [dict(algoId=str(i)) for i in range(start, start+100)]

    exchange = Pages()
    rows, truncated = pending(exchange, '/api/v5/trade/orders-algo-pending', 'algoId', ordType='conditional')
    assert truncated and len(rows) == 500
    assert exchange.queries[1]['after'] == '199'


def test_invalid_positions_do_not_become_zero_positions():
    exchange = Exchange()
    exchange.positions = [dict(pos='not-a-number')]
    with pytest.raises(ValueError):
        account_snapshot(exchange)


def test_missing_usdt_is_unknown_not_zero():
    exchange = Exchange()
    get = exchange.get
    exchange.get = lambda path, **query: [dict(details=[])] if path.endswith('/balance') else get(path, **query)
    with pytest.raises(ValueError):
        account_snapshot(exchange)

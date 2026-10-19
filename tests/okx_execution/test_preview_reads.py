"""Preview latency improves without skipping ownership, freshness or consent."""
from collections import Counter
from threading import Barrier, Event, Lock
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_manual_inventory import shared
from test_manual_live import h, preview, SYMBOL
from yoyo.okx_execution.client import APIError, Client, TransportError, signature
from yoyo.okx_execution.config import Config
from yoyo.okx_execution.manual_api import install
from yoyo.okx_execution.preview_reads import PooledOpener, PreviewClient, ReadPacer, preview_engine


def test_independent_reads_overlap_one_balance_and_next_preview_is_fresh(shared):
    original, calls, gate, lock = shared['exchange'].get, [], Barrier(4), Lock()
    first = {'/api/v5/account/'+p for p in ('config', 'balance', 'positions', 'instruments')}
    def get(path, **query):
        with lock:
            calls.append((path, query))
        if path in first:
            gate.wait(timeout=3)  # Serial execution cannot satisfy this barrier.
        return original(path, **query)
    shared['exchange'].get = get
    original_client = shared['engine'].client
    result = preview(shared)
    assert len(calls) == 11
    assert Counter(p for p, _ in calls)['/api/v5/account/balance'] == 1
    assert [q for p, q in calls if p.endswith('/balance')] == [{}]
    assert Counter(p for p, _ in calls)['/api/v5/market/ticker'] == 1
    assert 0 <= shared['queue'].get(result['id'])['plan']['quote_age_ms'] <= 5000
    assert result['target_risk'] == '30.00'
    assert shared['engine'].client is original_client
    calls.clear()
    shared['exchange'].balance.update(totalEq='900')
    second = preview(shared)
    assert second['target_risk'] == '27.00'
    assert len(calls) == 11 and second['id'] != result['id']
    assert not shared['exchange'].posts


def test_failed_inventory_never_publishes_a_draft(shared):
    original = shared['exchange'].get
    def get(path, **query):
        if path.endswith('/orders-algo-pending') and query['ordType'] == 'trigger':
            raise TransportError('unavailable')
        return original(path, **query)
    shared['exchange'].get = get
    with pytest.raises(TransportError):
        preview(shared)
    assert not shared['queue'].events() and not shared['exchange'].posts
    with shared['queue'].connect() as db:
        assert db.execute('SELECT COUNT(*) FROM requests').fetchone()[0] == 0
    # No cached failure or read thread survives the failed request.
    shared['exchange'].get = original
    assert preview(shared)['size'] == '60'


def test_later_inventory_page_is_still_checked(shared):
    original, afters = shared['exchange'].get, []
    def get(path, **query):
        if path.endswith('/orders-pending'):
            afters.append(query.get('after'))
            if 'after' not in query:
                return [dict(instId='BTC-USDT-SWAP', ordId=str(i)) for i in range(100)]
            return [dict(instId=SYMBOL, ordId='conflict-on-page-two')]
        return original(path, **query)
    shared['exchange'].get = get
    with pytest.raises(ValueError, match='owner-managed'):
        preview(shared)
    assert afters == [None, '99'] and not shared['exchange'].posts


def test_cache_is_local_copied_and_read_only(h):
    with preview_engine(h['engine'], ReadPacer()) as local:
        row = local.client.get('/api/v5/account/balance')
        row[0]['totalEq'] = '0'
        assert local.client.get('/api/v5/account/balance', ccy='USDT')[0]['totalEq'] == '1000'
        with pytest.raises(PermissionError):
            local.client.post('/api/v5/trade/order', {})
    assert h['engine'].client is h['exchange']


def test_pooled_transport_signs_exact_query_and_does_not_follow_redirects():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={'code': '0', 'data': []})
    opener = PooledOpener(transport=httpx.MockTransport(handler))
    client = PreviewClient(Client(Config(), dict(api_key='fixture', secret='secret', passphrase='phrase', demo=False)),
                           opener, ReadPacer())
    try:
        client.get('/api/v5/account/instruments', instId=SYMBOL, instType='SWAP')
        req = calls[0]
        assert req.headers['OK-ACCESS-SIGN'] == signature('secret', req.headers['OK-ACCESS-TIMESTAMP'],
                                                        'GET', req.url.raw_path.decode(), '')
        with pytest.raises(PermissionError):
            client.post('/api/v5/trade/order', {})
        with pytest.raises(PermissionError):
            opener.open(Request('https://www.okx.com/api/v5/trade/order', method='POST'), 10)
        assert len(calls) == 1
    finally:
        opener.close()
    assert opener.session.is_closed


@pytest.mark.parametrize('failure', ['redirect', 'http', 'timeout', 'shape', 'api'])
def test_transport_failure_has_no_retry_and_no_sensitive_message(failure):
    calls = []
    def handler(req):
        calls.append(req)
        if failure == 'redirect':
            return httpx.Response(302, headers={'location': 'https://example.com/SECRET'})
        if failure == 'http':
            return httpx.Response(503, text='SECRET')
        if failure == 'timeout':
            raise httpx.ReadTimeout('SECRET')
        return httpx.Response(200, json={'code': '500' if failure == 'api' else '0', 'data': {}, 'msg': 'SECRET'})
    opener = PooledOpener(transport=httpx.MockTransport(handler))
    client = PreviewClient(Client(Config()), opener, ReadPacer())
    try:
        with pytest.raises((TransportError, APIError)) as error:
            client.get('/api/v5/public/time')
        assert 'SECRET' not in str(error.value) and len(calls) == 1
    finally:
        opener.close()


def test_production_pool_closes_on_error_and_does_not_reuse_credentials(h, monkeypatch):
    pools, keys = [], []
    def handler(req):
        keys.append(req.headers['OK-ACCESS-KEY'])
        return httpx.Response(200, json={'code': '0', 'data': []})
    def factory():
        pool = PooledOpener(transport=httpx.MockTransport(handler))
        pools.append(pool)
        return pool
    monkeypatch.setattr('yoyo.okx_execution.preview_reads.PooledOpener', factory)
    for key in ('first-account', 'second-account'):
        h['engine'].client = Client(Config(), dict(api_key=key, secret='secret', passphrase='phrase', demo=False))
        with pytest.raises(ValueError):
            with preview_engine(h['engine'], ReadPacer()) as local:
                local.client.get('/api/v5/account/config')
                raise ValueError('fail closed')
    assert keys == ['first-account', 'second-account']
    assert all(p.session.is_closed for p in pools)


def test_reused_connections_never_reuse_auth_or_cookies():
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, headers={'set-cookie': 'account=old; Path=/'},
                              json={'code': '0', 'data': []})
    pool = PooledOpener(transport=httpx.MockTransport(handler))
    try:
        for key in ('first', 'second'):
            client = PreviewClient(Client(Config(), dict(api_key=key, secret='secret', passphrase='phrase', demo=False)),
                                   pool, ReadPacer())
            client.get('/api/v5/account/config')
        assert [r.headers['OK-ACCESS-KEY'] for r in requests] == ['first', 'second']
        assert all('cookie' not in r.headers for r in requests)
    finally:
        pool.close()


def test_router_closes_pool_with_custom_parent_lifespan(h):
    @asynccontextmanager
    async def lifespan(app):
        yield
    app = FastAPI(lifespan=lifespan)
    service = h['service']
    pool = service.preview_opener()
    install(app, h['book'], service=service)
    with TestClient(app):
        assert service.preview_opener() is pool and not pool.session.is_closed
    assert pool.session.is_closed


def test_endpoint_budgets_do_not_make_unrelated_reads_wait():
    now, sleeps = [0.0], []
    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay
    pacer = ReadPacer(clock=lambda: now[0], sleep=sleep)
    for path in ('/api/v5/account/config', '/api/v5/account/balance', '/api/v5/account/positions'):
        pacer.wait(path)
    assert not sleeps
    pacer.wait('/api/v5/account/config')
    assert sleeps == [0.81]
    for _ in range(4):
        pacer.wait('/api/v5/trade/orders-algo-pending')
    assert sleeps[1:] == pytest.approx([0.21, 0.21, 0.21])


def test_a_throttled_endpoint_does_not_hold_the_shared_lock():
    waiting, release = Event(), Event()
    def sleep(_):
        waiting.set()
        assert release.wait(3)
    pacer = ReadPacer(clock=lambda: 0, sleep=sleep)
    pacer.wait('/api/v5/account/config')
    with ThreadPoolExecutor(2) as pool:
        slow = pool.submit(pacer.wait, '/api/v5/account/config')
        assert waiting.wait(3)
        try:
            pool.submit(pacer.wait, '/api/v5/account/balance').result(timeout=1)
        finally:
            release.set()
        slow.result(timeout=1)


@pytest.mark.parametrize('delay,quote_reads', [(0, 1), (5000, 1), (5001, 2)])
def test_slow_inventory_refreshes_old_prefetched_quote(h, delay, quote_reads):
    original, requests = h['exchange'].get, []
    def get(path, **query):
        if path.endswith('/ticker'):
            requests.append(h['now'][0])
        return original(path, **query)
    h['exchange'].get = get
    with preview_engine(h['engine'], ReadPacer()) as local:
        local.client.prefetch(SYMBOL, shared=False)
        local.client.quote_future.result()
        h['now'][0] += delay
        rows = local.client.get('/api/v5/market/ticker', instId=SYMBOL)
        assert len(requests) == quote_reads
        assert 0 <= h['now'][0] - int(rows[0]['ts']) <= 5000
        assert local.client.quote_request_ms == requests[-1]
    assert not h['exchange'].posts


def test_stale_refresh_failure_cannot_fall_back_to_the_old_quote(h):
    original, calls = h['exchange'].get, []
    def get(path, **query):
        if path.endswith('/ticker'):
            calls.append(path)
            if len(calls) == 2:
                raise TransportError('quote unavailable')
        return original(path, **query)
    h['exchange'].get = get
    with preview_engine(h['engine'], ReadPacer()) as local:
        local.client.prefetch(SYMBOL, shared=False)
        local.client.quote_future.result()
        h['now'][0] += 5001
        with pytest.raises(TransportError):
            local.client.get('/api/v5/market/ticker', instId=SYMBOL)

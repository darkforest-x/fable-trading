"""Cache readers, scoped ledgers and receipt snapshots retain execution invariants."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from threading import Event
from types import SimpleNamespace
import time

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from yoyo.research_workspace import paper_desk as book, paper_desk_api as api
from yoyo.research_workspace import paper_desk_analytics as analytics
from yoyo.research_workspace.paper_desk_market import PublicHTTP, MarketRefresh
from yoyo.research_workspace.paper_desk_risk import PublicRisk

SYMBOL = 'BTC-USDT-SWAP'
PREFIX = '/paper-desk'


def quote(desk, symbol=SYMBOL, price='100'):
    at = book.clock_ms()
    return desk.save_quote(dict(symbol=symbol, bid=price, ask=price, source_ms=at, received_ms=at))


def ticker(price='100'):
    return dict(instId=SYMBOL, bidPx=price, askPx=price, last=price, ts=str(book.clock_ms()))


def test_shared_http_reuses_one_client_and_closes(monkeypatch):
    clients = []
    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)
        def get(self, path, params):
            return httpx.Response(200, json={'code': '0', 'data': [path]}, request=httpx.Request('GET', 'https://www.okx.com'))
        def close(self):
            self.closed = True
    monkeypatch.setattr(httpx, 'Client', Client)
    http = PublicHTTP()
    assert http.get('market/tickers') == ['market/tickers']
    assert http.get('public/instruments') == ['public/instruments']
    assert len(clients) == 1
    http.close()
    assert clients[0].closed


def test_readers_never_wait_for_refresh_and_stale_quotes_still_expire(monkeypatch):
    quotes = api.PublicQuotes()
    monkeypatch.setattr(quotes, 'fetch', lambda: [ticker()])
    quotes.refresh()
    quotes.background = True
    entered, release = Event(), Event()
    def slow():
        entered.set()
        assert release.wait(3)
        return [ticker('101')]
    monkeypatch.setattr(quotes, 'fetch', slow)
    quotes.cached_at = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(quotes.refresh)
        assert entered.wait(1)
        try:
            assert pool.submit(quotes.get, [SYMBOL]).result(timeout=.5)[SYMBOL]['bid'] == '100'
            at = quotes.cache[SYMBOL]['source_ms']
            monkeypatch.setattr(api, 'clock_ms', lambda: at+15001)
            assert pool.submit(quotes.get, [SYMBOL]).result(timeout=.5) == {}
        finally:
            release.set()
            refresh.result(timeout=2)


def test_failed_refresh_keeps_source_timestamp_and_does_not_make_quote_fresh(monkeypatch):
    quotes = api.PublicQuotes()
    monkeypatch.setattr(quotes, 'fetch', lambda: [ticker()])
    quotes.refresh()
    original = dict(quotes.cache[SYMBOL])
    quotes.background = True
    quotes.cached_at = None
    def offline():
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(quotes, 'fetch', offline)
    with pytest.raises(httpx.ConnectError):
        quotes.refresh()
    assert quotes.cache[SYMBOL] == original
    monkeypatch.setattr(api, 'clock_ms', lambda: original['source_ms']+15001)
    assert quotes.get([SYMBOL]) == {}


def test_background_risk_reads_only_snapshots_and_registers_new_symbol(monkeypatch):
    risk = PublicRisk()
    risk.background = True
    monkeypatch.setattr(risk, 'fetch', lambda *a, **kw: pytest.fail('reader performed network I/O'))
    assert risk.get(['W-USDT-SWAP']) == {}
    assert 'W-USDT-SWAP' in risk.requested
    risk.instruments = {SYMBOL: dict(ctVal='1', ctMult='1')}
    risk.instrument_at = time.monotonic()
    risk.marks = {SYMBOL: dict(markPx='100', ts=str(book.clock_ms()))}
    risk.tiers = {SYMBOL: (time.monotonic(), [dict(maxSz='10000', mmr='.01', maxLever='100')], book.clock_ms())}
    assert risk.get([SYMBOL])[SYMBOL]['mark_price'] == '100'
    risk.instrument_at -= 3601
    assert risk.get([SYMBOL]) == {}


def test_catalog_reader_rejects_expired_snapshot_without_network(monkeypatch):
    symbols = api.PublicSymbols()
    symbols.background = True
    symbols.cache = [dict(symbol=SYMBOL, base='BTC')]
    symbols.cached_at = time.monotonic()-301
    monkeypatch.setattr(symbols, 'fetch', lambda: pytest.fail('reader performed network I/O'))
    with pytest.raises(ValueError):
        symbols.get()


def test_refresh_lifecycle_closes_shared_resources(tmp_path, monkeypatch):
    http = PublicHTTP()
    risk = PublicRisk(http)
    quotes = api.PublicQuotes(risk, http)
    symbols = api.PublicSymbols(quotes, http)
    called = Event()
    monkeypatch.setattr(quotes, 'refresh', called.set)
    monkeypatch.setattr(symbols, 'refresh', lambda: None)
    monkeypatch.setattr(risk, 'refresh_marks', lambda: None)
    monkeypatch.setattr(risk, 'refresh_instruments', lambda: None)
    monkeypatch.setattr(risk, 'refresh_tiers', lambda *args: None)
    closed = []
    monkeypatch.setattr(http, 'close', lambda: closed.append(True))
    worker = MarketRefresh(book.PaperDesk(tmp_path), quotes, symbols, http)
    worker.start()
    threads = list(worker.threads)
    assert called.wait(1)
    worker.start()
    assert worker.threads == threads
    worker.close()
    assert closed == [True] and all(not t.is_alive() for t in threads)


def populated(tmp_path):
    desk = book.PaperDesk(tmp_path)
    a = desk.create_account('A', '1000', 'account-a')['id']
    b = desk.create_account('B', '1000', 'account-b')['id']
    q = quote(desk)
    closed = desk.open(a, 'long', '2', q['id'], '', 'entry-a', {}, 100)['position']
    desk.close(closed['id'], '1', quote(desk, price='110')['id'], 'partial-a')
    desk.open(a, 'long', '3', quote(desk, price='120')['id'], '', 'add-a', {SYMBOL: q}, 100)
    desk.close(closed['id'], '4', quote(desk, price='130')['id'], 'close-a')
    opened = desk.open(a, 'long', '1', q['id'], '', 'open-a-again', {}, 100)['position']
    desk.open(b, 'short', '1', q['id'], '', 'open-b', {}, 100)
    return desk, a, b, q, opened, closed


def test_live_snapshot_scopes_account_and_never_scans_fills(tmp_path, monkeypatch):
    desk, a, b, q, opened, closed = populated(tmp_path)
    statements = []
    connect = desk.connect
    @contextmanager
    def traced():
        with connect() as db:
            db.set_trace_callback(statements.append)
            yield db
    monkeypatch.setattr(desk, 'connect', traced)
    monkeypatch.setattr(analytics, 'closed_position_summaries', lambda *args: pytest.fail('history on live path'))
    snap = desk.snapshot({SYMBOL: q}, a, True)
    assert snap['active_account_id'] == a and snap['view'] == 'live'
    assert [p['id'] for p in snap['positions']] == [opened['id']]
    assert len(snap['accounts']) == 2 and snap['fills'] == []
    assert not any('fills' in sql.lower() for sql in statements)
    assert next(item for item in snap['accounts'] if item['id'] == a)['available'] is not None
    with pytest.raises(KeyError):
        desk.snapshot({}, 'missing', True)


def test_history_pages_keep_complete_lifecycles_and_stable_fill_order(tmp_path):
    desk, a, b, q, opened, closed = populated(tmp_path)
    page = desk.records(a, 'closed', 1, 0)
    assert page['total'] == 1 and not page['has_more']
    p = page['items'][0]
    assert p['id'] == closed['id'] and len(p['related_fills']) == 4
    assert Decimal(p['average_open_price']) == 112
    assert Decimal(p['average_close_price']) == 126
    assert Decimal(p['cumulative_entry_margin']) == Decimal('5.6')
    assert desk.records(a, 'closed', 1, 1)['items'] == []
    all_ids = [f['id'] for f in desk.snapshot({})['fills'] if f['account_id'] == a]
    pages = [desk.records(a, 'fills', 2, offset) for offset in [0, 2, 4]]
    assert [item['id'] for page in pages for item in page['items']] == all_ids
    assert [p['has_more'] for p in pages] == [True, True, False]
    with pytest.raises(ValueError):
        desk.records(a, 'fills', 1000)


def test_background_order_worker_does_not_build_full_history(tmp_path, monkeypatch):
    from yoyo.research_workspace.paper_desk_limits import LimitOrders, LimitOrderWorker
    desk, a, b, q, opened, closed = populated(tmp_path)
    monkeypatch.setattr(desk, 'snapshot', lambda *args: pytest.fail('worker loaded UI history'))
    worker = LimitOrderWorker(LimitOrders(desk), SimpleNamespace(get=lambda symbols: {SYMBOL: q}))
    worker.tick()
    assert worker.last_check_ms is not None
    assert '档位' in worker.last_error  # Missing risk metadata still fails closed.


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = FastAPI()
    router = APIRouter()
    api.install(router, app, SimpleNamespace(runtime=tmp_path))
    app.include_router(router)
    quotes = api.PublicQuotes()
    monkeypatch.setattr(quotes, 'fetch', lambda: [ticker()])
    app.state.paper_desk_quotes = quotes
    with TestClient(app) as c:
        yield c, app.state.paper_desk, quotes


def test_estimate_polling_never_persists_or_returns_executable_quote_id(client):
    c, desk, quotes = client
    for _ in range(5):
        response = c.get(PREFIX+'/estimate', params={'symbol': SYMBOL})
        assert response.status_code == 200 and 'id' not in response.json()
    with desk.connect() as db:
        assert db.execute('SELECT count(*) FROM quotes').fetchone()[0] == 0
    assert c.get(PREFIX+'/estimate', params={'symbol': '-USDT-SWAP'}).status_code == 422
    quotes.background = True
    quotes.cache[SYMBOL]['source_ms'] -= 16000
    assert c.get(PREFIX+'/estimate', params={'symbol': SYMBOL}).status_code == 503


def test_committed_reply_contains_selected_live_state_and_retry_does_not_duplicate(client, monkeypatch):
    c, desk, quotes = client
    a = c.post(PREFIX+'/accounts?include_snapshot=true', json=dict(name='A', initial_cash='1000', request_id='create-request-0001')).json()
    assert a['snapshot']['active_account_id'] == a['id']
    q = c.get(PREFIX+'/quote', params={'symbol': SYMBOL}).json()
    body = dict(side='long', quantity='1', quote_id=q['id'], leverage=100, request_id='open-request-00001')
    url = PREFIX+'/accounts/'+a['id']+'/open?include_snapshot=true'
    response = c.post(url, json=body)
    assert response.status_code == 201
    data = response.json()
    assert data['snapshot']['positions'][0]['id'] == data['position']['id']
    assert Decimal(data['snapshot']['accounts'][0]['available']) == Decimal('998.95')
    assert data['snapshot']['fills'] == []
    monkeypatch.setattr(desk, 'snapshot', lambda *args: (_ for _ in ()).throw(RuntimeError('display failed')))
    retry = c.post(url, json=body)
    assert retry.status_code == 201 and retry.json()['refresh_required']
    assert retry.json()['fill'] == data['fill']
    assert len(desk.export(a['id'])['fills']) == 1


def test_new_read_routes_validate_paging_and_account(client):
    c, desk, quotes = client
    a = desk.create_account('A', '1000', 'create-test')['id']
    assert c.get(PREFIX+'/live', params={'account_id': a}).json()['view'] == 'live'
    assert c.get(PREFIX+'/live', params={'account_id': 'missing'}).status_code == 404
    url = PREFIX+'/accounts/'+a+'/records'
    for params in [dict(kind='fills', limit=101), dict(kind='fills', offset=-1), dict(kind='bad')]:
        assert c.get(url, params=params).status_code == 422
    assert c.get(url, params={'kind': 'fills'}).json()['items'] == []


def test_estimate_prewarms_and_reports_entry_risk_without_network_or_receipts(client, monkeypatch):
    c, desk, quotes = client
    risk = PublicRisk()
    risk.background = True
    quotes.risk = risk
    monkeypatch.setattr(risk, 'fetch', lambda *a, **kw: pytest.fail('estimate performed risk network I/O'))
    params = {'symbol': SYMBOL}
    assert c.get(PREFIX+'/estimate', params=params).json()['entry_ready'] is False
    assert SYMBOL in risk.requested
    risk.instruments = {SYMBOL: dict(ctVal='1', ctMult='1')}
    risk.instrument_at = time.monotonic()
    risk.marks = {SYMBOL: dict(markPx='100', ts=str(book.clock_ms()))}
    risk.tiers = {SYMBOL: (time.monotonic(), [dict(maxSz='10000', mmr='.01', maxLever='100')], book.clock_ms())}
    assert c.get(PREFIX+'/estimate', params=params).json()['entry_ready'] is True
    risk.marks[SYMBOL]['ts'] = str(book.clock_ms()-16000)
    assert c.get(PREFIX+'/estimate', params=params).json()['entry_ready'] is False
    risk.marks[SYMBOL]['ts'] = str(book.clock_ms())
    risk.tiers[SYMBOL] = (time.monotonic()-3601, [], book.clock_ms()-3601000)
    assert c.get(PREFIX+'/estimate', params=params).json()['entry_ready'] is False
    with desk.connect() as db:
        assert db.execute('SELECT count(*) FROM quotes').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM fills').fetchone()[0] == 0

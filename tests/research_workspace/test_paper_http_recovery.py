"""Recover poisoned public pools without closing concurrent requests or stale marks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpcore
import httpx
import pytest

from yoyo.research_workspace.paper_desk_market import PublicHTTP
from yoyo.research_workspace import paper_desk as book


def response(path='market/tickers'):
    return httpx.Response(200, json={'code': '0', 'data': [path]},
                          request=httpx.Request('GET', 'https://www.okx.com/' + path))


@pytest.mark.parametrize('failure', [httpx.ConnectError, httpx.PoolTimeout, httpx.RemoteProtocolError])
def test_transport_failure_recovers_with_one_new_public_client(monkeypatch, failure):
    clients = []
    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            self.calls = 0
            clients.append(self)
        def get(self, path, params):
            self.calls += 1
            if self is clients[0]:
                raise failure('transport unavailable')
            return response(path)
        def close(self):
            self.closed = True
    monkeypatch.setattr(httpx, 'Client', Client)
    http = PublicHTTP()
    assert http.get('market/tickers', instType='SWAP') == ['market/tickers']
    assert http.get('public/mark-price') == ['public/mark-price']
    assert len(clients) == 2 and clients[0].closed
    assert [c.calls for c in clients] == [1, 2]
    http.close()
    assert clients[1].closed


def test_repeated_failure_is_bounded_and_does_not_publish_success(monkeypatch):
    clients = []
    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)
        def get(self, path, params):
            raise httpx.ConnectError('offline')
        def close(self):
            self.closed = True
    monkeypatch.setattr(httpx, 'Client', Client)
    http = PublicHTTP()
    with pytest.raises(httpx.ConnectError):
        http.get('market/tickers')
    assert len(clients) == 2 and all(c.closed for c in clients)
    assert not http.users and http.client is None


def test_late_old_failure_does_not_close_active_request_or_retire_new_pool(monkeypatch):
    clients, entered, release = [], Event(), Event()
    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)
        def get(self, path, params):
            if self is clients[0]:
                if path == 'slow':
                    entered.set()
                    assert release.wait(3)
                    assert not self.closed
                raise httpx.PoolTimeout('old pool')
            return response(path)
        def close(self):
            self.closed = True
    monkeypatch.setattr(httpx, 'Client', Client)
    http = PublicHTTP()
    with ThreadPoolExecutor(2) as pool:
        slow = pool.submit(http.get, 'slow')
        assert entered.wait(1)
        try:
            assert http.get('market/tickers') == ['market/tickers']
            assert not clients[0].closed
        finally:
            release.set()
        assert slow.result(timeout=2) == ['slow']
    assert len(clients) == 2 and clients[0].closed and not clients[1].closed
    assert http.get('public/mark-price') == ['public/mark-price']
    assert len(clients) == 2
    http.close()


def test_actual_failed_tls_tunnel_exhaustion_recovers(monkeypatch):
    # Reproduce the installed httpcore's CONNECT/TLS failure, no real sockets.
    class Stream(httpcore.NetworkStream):
        def read(self, max_bytes, timeout=None):
            return b'HTTP/1.1 200 Connection established\r\n\r\n'
        def write(self, buffer, timeout=None):
            pass
        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            raise httpcore.ConnectError('injected TLS EOF')
        def close(self):
            pass
    class Backend(httpcore.NetworkBackend):
        def connect_tcp(self, *args, **kwargs):
            return Stream()
    transport = httpx.HTTPTransport(proxy='http://proxy.test:8080',
                                    limits=httpx.Limits(max_connections=2))
    transport._pool._network_backend = Backend()
    old = httpx.Client(base_url='https://www.okx.com/api/v5/', transport=transport,
                       timeout=httpx.Timeout(.05), trust_env=False)
    for _ in range(2):
        with pytest.raises(httpx.ConnectError):
            old.get('market/tickers')
    with pytest.raises(httpx.PoolTimeout):
        old.get('market/tickers')
    real_client = httpx.Client
    def replacement(**kwargs):
        return real_client(**kwargs, transport=httpx.MockTransport(lambda r: response()))
    monkeypatch.setattr(httpx, 'Client', replacement)
    http = PublicHTTP()
    http.client = old
    try:
        assert http.get('market/tickers') == ['market/tickers']
        assert old.is_closed
    finally:
        http.close()


def test_missing_symbols_are_exact_and_recovery_restores_funds_without_ledger_write(tmp_path):
    desk = book.PaperDesk(tmp_path)
    account = desk.create_account('Freshness', '1000', 'create-freshness-qa')['id']
    marks = {}
    for i, symbol in enumerate(('ETH-USDT-SWAP', 'BONK-USDT-SWAP')):
        at = book.clock_ms()
        mark = dict(symbol=symbol, bid='100', ask='100', source_ms=at, received_ms=at)
        quote = desk.save_quote(mark)
        desk.open(account, 'long', '1', quote['id'], '', f'open-freshness-{i}', marks, 20)
        marks[symbol] = mark
    with desk.connect() as db:
        before = db.execute('SELECT COUNT(*) FROM fills').fetchone()[0]
    stale = dict(marks['BONK-USDT-SWAP'], source_ms=book.clock_ms()-16000)
    broken = desk.snapshot({'BONK-USDT-SWAP': stale})['accounts'][0]
    assert broken['missing_mark_symbols'] == ['BONK-USDT-SWAP', 'ETH-USDT-SWAP']
    assert broken['equity'] is None and broken['available'] is None
    recovered = desk.snapshot(marks)['accounts'][0]
    assert recovered['missing_mark_symbols'] == [] and recovered['marks_complete']
    assert recovered['equity'] is not None and recovered['available'] is not None
    with desk.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == before

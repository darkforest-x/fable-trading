"""Public history relay tests use synthetic rows and never contact an exchange."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from fastapi.testclient import TestClient
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.candles import CandleHistory, CandleUnavailable, fetch_okx

SYMBOL = 'BTC-USDT-SWAP'
ROW = ['1700000000000', '100', '105', '95', '101', '1', '10', '1000', '0']


def test_cache_is_bounded_isolated_and_single_flight():
    started, release = threading.Event(), threading.Event()
    calls = []
    def fetch(*key):
        calls.append(key)
        started.set()
        assert release.wait(2)
        return [ROW]
    now = [0]
    history = CandleHistory(fetch, lambda: now[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(history.read, SYMBOL, '1m')
        assert started.wait(1)
        second = pool.submit(history.read, SYMBOL, '1m')
        release.set()
        assert first.result() == second.result()
    assert len(calls) == 1
    history.read(SYMBOL, '1m')
    assert len(calls) == 1
    now[0] = 3
    history.read(SYMBOL, '1m')
    history.read(SYMBOL, '5m')
    history.read(SYMBOL, '1m', 1700000000)
    assert len(calls) == 4
    for i in range(80):
        history.read(f'COIN{i}-USDT-SWAP', '1m')
    assert len(history.cache) == 64
    assert not history.pending


def test_negative_cache_recovers_and_validation_does_not_fetch():
    calls = []
    now = [0]
    def fetch(*key):
        calls.append(key)
        if len(calls) == 1:
            raise OSError('private upstream detail')
        return [ROW]
    history = CandleHistory(fetch, lambda: now[0])
    for _ in range(2):
        with pytest.raises(CandleUnavailable, match='历史K线暂不可用'):
            history.read(SYMBOL, '1m')
    assert len(calls) == 1
    now[0] = 3
    assert history.read(SYMBOL, '1m')['data'] == [ROW]
    for key in [('https://internal/', '1m', None), (SYMBOL, '1s', None), (SYMBOL, '1m', -1)]:
        with pytest.raises(ValueError):
            history.read(*key)
    assert len(calls) == 2


def test_relay_authenticated_and_independent_of_book_reads_or_writes(tmp_path, monkeypatch):
    book = Book(tmp_path / 'book.sqlite3')
    app = create_app(book, 'x' * 36, market_enabled=False)
    calls = []
    app.state.candle_history = CandleHistory(lambda *key: calls.append(key) or [ROW])
    def forbidden(*args, **kwargs):
        raise AssertionError('candle relay touched the account')
    monkeypatch.setattr(book, 'snapshot', forbidden)
    monkeypatch.setattr(book, 'transaction', forbidden)
    with TestClient(app) as client:
        path = f'/v1/market/candles?symbol={SYMBOL}&period=1m'
        assert client.get(path).status_code == 401
        assert not calls
        headers = {'Authorization': 'Bearer ' + 'x' * 36}
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.json()['data'] == [ROW]
        assert client.get(path.replace('period=1m', 'period=1s'), headers=headers).status_code == 422
        assert client.get(path + '&before=-1', headers=headers).status_code == 422
        assert len(calls) == 1


def test_okx_url_is_fixed_cursor_ms_and_invalid_ohlc_rejected(monkeypatch):
    import json
    from yoyo.discipline_app import candles
    seen = []
    body = {'code': '0', 'data': [ROW.copy()]}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return json.dumps(body).encode()
    def open_url(request, timeout):
        seen.append((request.full_url, request.headers, timeout))
        return Response()
    monkeypatch.setattr(candles.urllib.request, 'urlopen', open_url)
    assert fetch_okx(SYMBOL, '1m', 1700000000) == [ROW]
    assert seen[0][0].startswith('https://www.okx.com/api/v5/market/history-candles?')
    assert 'after=1700000000000' in seen[0][0]
    assert 'Authorization' not in seen[0][1]
    body['data'][0][2] = '90'
    with pytest.raises(CandleUnavailable): fetch_okx(SYMBOL, '1m', None)

"""Concurrent display reads and compression use fake accounts only."""
import json
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
import pytest
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.policy import Rejected
from test_live_book import Exchange, quote, ticket


def test_display_reads_do_not_wait_for_writer_or_change_ledger(tmp_path):
    now = [1792332000.0]
    exchange = Exchange(lambda: now[0])
    book = LiveBook(tmp_path / 'live.sqlite3', exchange, lambda: now[0])
    quote(book)
    assert book.reconcile()
    with book.read_transaction() as (_, state):
        version = state['version']
    with ThreadPoolExecutor(max_workers=2) as pool:
        with book.transaction() as (_, pending):
            pending['account_equity'] = '2000'
            # Both must complete before the writer commits, and see 1000.
            state = pool.submit(book.snapshot).result(timeout=1)
            stats = pool.submit(book.statistics).result(timeout=1)
            assert state['equity'] == stats['account']['equity'] == '1000'
            assert state['version'] == version
        with book.read_transaction() as (_, state):
            assert state['version'] == version + 1
    assert book.snapshot()['equity'] == '2000'
    assert exchange.writes == []
    now[0] += 16
    state = book.snapshot()
    assert any('过期' in b for b in state['blockers'])
    with pytest.raises(Rejected, match='过期'):
        book.preview_in(state, ticket())


def test_compressed_private_response_is_not_http_cached(tmp_path):
    exchange = Exchange(lambda: 1792332000.0)
    book = LiveBook(tmp_path / 'live.sqlite3', exchange, exchange.clock)
    quote(book)
    book.reconcile()
    with book.transaction() as (_, state):
        state['instruments'] = {f'COIN{i}-USDT-SWAP':dict(symbol=f'COIN{i}-USDT-SWAP',tick='.01',step='.01') for i in range(494)}
    client = TestClient(create_app(book, 'x'*36, market_enabled=False))
    response = client.get('/v1/state', headers={'Authorization':'Bearer '+'x'*36, 'Accept-Encoding':'gzip'})
    assert response.status_code == 200
    assert response.headers['Content-Encoding'] == 'gzip'
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'Accept-Encoding' in response.headers['Vary']
    assert len(response.json()['instruments']) == 494
    stats = client.get('/v1/statistics', headers={'Authorization':'Bearer '+'x'*36}).json()
    assert stats['account_id'] == 'primary'
    assert stats['account_updated_at'] == exchange.clock()
    assert exchange.writes == []


def test_packaged_artwork_is_public_image_and_does_not_hide_missing_assets():
    from pathlib import Path
    from yoyo.discipline_app.scripts.cache_coin_logos import image_format
    assets = Path(__file__).resolve().parents[2] / 'yoyo/discipline_app/mobile/src/assets'
    manifest = json.loads((assets / 'coin-logos.json').read_text())
    assert len(set(manifest['bundled']) | set(manifest['fallback'])) == manifest['catalog_count']
    assert not set(manifest['bundled']) & set(manifest['fallback'])
    assert {'btc', 'eth', 'sol'} <= set(manifest['bundled'])
    for coin in manifest['bundled']:
        files = list((assets / 'coins').glob(coin+'.*'))
        assert len(files) == 1
        assert image_format(files[0].read_bytes()) == files[0].suffix[1:]

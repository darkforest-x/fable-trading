"""Managed statistics use one read snapshot, independent of external capital."""
import copy
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from test_live_book import live
from yoyo.discipline_app.api import create_app


@pytest.mark.parametrize('days', [0, 7, 30])
def test_live_total_uses_full_managed_history_and_remaining_positions(live, days):
    book, exchange, now = live
    with book.transaction() as (_, state):
        # Over 30 completed records, including one outside every period filter.
        state['closed'] = [
            dict(id=str(i), symbol='BTC-USDT-SWAP', side='long', pnl='-1.25',
                 risk='5', closed=now[0] - (40 * 86400 if i == 0 else 60))
            for i in range(35)
        ]
        # PnL already realized by a partially closed trade must count once;
        # only its remaining size contributes floating PnL.
        state['positions'] = [
            dict(symbol='BTC-USDT-SWAP', entry='90', remaining='2', direction=1, pnl='3.50'),
            dict(symbol='ETH-USDT-SWAP', entry='110', remaining='1', direction=-1, pnl='-0.25'),
        ]
        state['account_equity'] = '2500'
        state['cash'] = '2000'  # External floating PnL is not managed PnL.
    with book.read_transaction() as (db, state):
        before = copy.deepcopy(state)
        events_before = db.execute('SELECT count(*) FROM journal').fetchone()[0]
    result = book.statistics(days)
    account = result['account']
    assert D(account['equity']) == 2500
    assert D(account['realized_pnl']) == D('-40.50')
    assert D(account['unrealized_pnl']) == 30
    assert D(account['net_pnl']) == D('-10.50')
    assert D(account['net_pnl']) == D(account['realized_pnl']) + D(account['unrealized_pnl'])
    assert account['initial_equity'] is account['return_fraction'] is None
    assert result['closed']['count'] == (35 if days == 0 else 34)
    assert result['scope'] == 'app_managed_trades'
    with book.read_transaction() as (db, state):
        assert state == before
        assert db.execute('SELECT count(*) FROM journal').fetchone()[0] == events_before
    assert exchange.writes == []


def test_external_only_floating_and_transfers_cannot_change_managed_total(live):
    book, exchange, now = live
    with book.transaction() as (_, state):
        state['closed'] = [
            dict(id='closed', symbol='BTC-USDT-SWAP', side='long',
                 pnl='-12.00', risk='5', closed=now[0]-60),
        ]
        state['account_equity'] = '1025'
        state['cash'] = '1000'
    first = book.statistics()['account']
    assert D(first['net_pnl']) == D('-12')
    assert D(first['unrealized_pnl']) == 0
    with book.transaction() as (_, state):
        state['account_equity'] = '10025'
        state['cash'] = '10000'
    after_transfer = book.statistics()['account']
    for field in ('net_pnl', 'realized_pnl', 'unrealized_pnl', 'initial_equity', 'return_fraction'):
        assert after_transfer[field] == first[field]
    assert exchange.writes == []


def test_authenticated_empty_live_statistics_returns_zero_total(live):
    book, exchange, _ = live
    with TestClient(create_app(book, 'test-statistics-token-'+'x'*32, market_enabled=False)) as client:
        assert client.get('/v1/statistics').status_code == 401
        response = client.get('/v1/statistics', headers={'Authorization': 'Bearer test-statistics-token-'+'x'*32})
    assert response.status_code == 200
    account = response.json()['account']
    assert D(account['net_pnl']) == 0
    assert account['return_fraction'] is None
    assert response.headers['Cache-Control'] == 'no-store'
    assert exchange.writes == []

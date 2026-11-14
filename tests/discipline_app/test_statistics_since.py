"""Statistics cutoffs project closed trades without changing account equity."""
import json
from decimal import Decimal as D

from fastapi.testclient import TestClient

from test_live_book import live
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.statistics_basis import set_fixed_basis


TOKEN = 'statistics-since-test-token-1234567890'


def test_paper_since_uses_closed_trades_and_keeps_equity_and_float_separate(tmp_path):
    now = [1_792_310_400.0]
    book = Book(tmp_path/'paper.sqlite3', lambda: now[0], initial_equity='5000')
    with book.transaction() as (_, state):
        state['closed'] = [
            dict(id='old', symbol='BTC-USDT-SWAP', side='long', pnl='-8', risk='5', closed=now[0]-100),
            dict(id='new-long', symbol='BTC-USDT-SWAP', side='long', pnl='10', risk='5', closed=now[0]-20),
            dict(id='new-short', symbol='ETH-USDT-SWAP', side='short', pnl='-4', risk='5', closed=now[0]-5),
        ]
        state['positions'] = [dict(id='open', symbol='BTC-USDT-SWAP', side='long', direction=1,
                                   entry='100', remaining='1', pnl='7')]
        state['cash'] = '5005'  # Initial 5000 plus all realized closed/open-position PnL.
        state['quotes'] = {'BTC-USDT-SWAP': dict(last='103', time=now[0])}

    since = now[0]-50
    result = book.statistics(0, since)
    assert result['applied_since'] == since
    assert result['period_start'] == since
    assert result['closed']['count'] == 2
    assert result['closed']['net_pnl'] == '6'
    assert result['curve'][-1]['pnl'] == '6'
    assert {row['symbol']: row['count'] for row in result['symbols']} == {
        'BTC-USDT-SWAP': 1, 'ETH-USDT-SWAP': 1}
    assert {row['side']: row['count'] for row in result['sides']} == {'long': 1, 'short': 1}
    account = result['account']
    assert D(account['equity']) == D('5008')
    assert D(account['initial_equity']) == D('5000')
    assert D(account['realized_pnl']) == D('6')
    assert D(account['net_pnl']) == D('6')
    assert D(account['unrealized_pnl']) == D('3')
    assert D(account['return_fraction']) == D('.0012')

    full = book.statistics(0)
    assert full['applied_since'] is None
    assert D(full['account']['equity']) == D('5008')
    assert D(full['account']['net_pnl']) == D('8')


def test_live_since_excludes_open_partial_pnl_from_cumulative_but_keeps_float(live):
    book, exchange, now = live
    since = now[0]-50
    with book.transaction() as (db, state):
        state['closed'] = [
            dict(id='old', symbol='BTC-USDT-SWAP', side='long', pnl='50', risk='5', closed=now[0]-100),
            dict(id='new', symbol='ETH-USDT-SWAP', side='short', pnl='12', risk='5', closed=now[0]-10),
        ]
        state['positions'] = [dict(id='open', symbol='BTC-USDT-SWAP', side='long', direction=1,
                                   entry='90', remaining='2', pnl='-5')]
        state['quotes'] = {'BTC-USDT-SWAP': dict(last='100', time=now[0])}
        state['account_equity'] = '2500'
        state['cash'] = '2000'
        db.execute("INSERT INTO journal(time,kind,body) VALUES(?,?,?)",
                   (now[0]-100, 'live_closed', json.dumps(dict(id='old', fee='.50'))))
        db.execute("INSERT INTO journal(time,kind,body) VALUES(?,?,?)",
                   (now[0]-10, 'live_closed', json.dumps(dict(id='new', fee='.12'))))
        reference = state['account_reference']
    set_fixed_basis(book.path, '1000', reference, 'Synthetic statistics test', now[0])

    result = book.statistics(0, since)
    assert result['applied_since'] == since
    assert result['closed']['count'] == 1
    assert result['closed']['net_pnl'] == '12'
    assert result['fees'] == '0.12'
    assert result['sides'][1]['count'] == 1  # Short-side row.
    account = result['account']
    assert D(account['equity']) == D('2500')
    assert D(account['realized_pnl']) == D('12')
    assert D(account['net_pnl']) == D('12')
    assert D(account['unrealized_pnl']) == D('20')
    assert D(account['return_fraction']) == D('.012')
    assert result['statistics_started_at'] is None
    assert exchange.writes == []


def test_statistics_api_echoes_since_and_rejects_invalid_values(tmp_path):
    now = [1_792_310_400.0]
    book = Book(tmp_path/'paper.sqlite3', lambda: now[0])
    with TestClient(create_app(book, TOKEN, market_enabled=False)) as client:
        headers = {'Authorization': f'Bearer {TOKEN}'}
        since = now[0]-123.456789012345
        response = client.get('/v1/statistics', params={'days': 0, 'since': repr(since)}, headers=headers)
        assert response.status_code == 200
        assert response.json()['applied_since'] == since
        assert response.json()['period_start'] == since
        assert client.get('/v1/statistics', headers=headers).json()['applied_since'] is None
        assert client.get('/v1/statistics?since=-1', headers=headers).status_code == 422
        assert client.get('/v1/statistics?since=inf', headers=headers).status_code != 200

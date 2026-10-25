"""Display reset must not erase execution history or relax discipline."""
import copy
import json
import sqlite3
from uuid import uuid4

import pytest

from test_live_book import live
from yoyo.discipline_app.statistics_basis import set_fixed_basis
from yoyo.discipline_app.statistics_reset import reset_history


def closed(identity, stamp, pnl='-12'):
    return dict(id=identity, symbol='BTC-USDT-SWAP', side='long', pnl=pnl,
                risk='5', closed=stamp, remaining='0', protection='closed')


def test_reset_archives_only_display_epoch_and_new_trades_count(live):
    book, exchange, now = live
    with book.transaction() as (db, state):
        state['closed'] = [closed('old', now[0]-10)]
        state['intents'] = {'old': dict(id='old', phase='closed')}
        book.event(db, 'live_closed', dict(id='old', fee='.10'))
        reference = state['account_reference']
    set_fixed_basis(book.path, '1000', reference, 'Synthetic capital', now[0])
    with book.read_transaction() as (_, state):
        before = copy.deepcopy(state)
        risk_before = book.daily_pnl(state)
    request = str(uuid4())
    receipt = reset_history(book.path, request, 'Owner requested fresh app statistics', now[0])
    with sqlite3.connect(receipt['archive']) as archive:
        assert json.loads(archive.execute('SELECT body FROM state').fetchone()[0]) == before
        assert archive.execute("SELECT count(*) FROM journal WHERE kind='live_closed'").fetchone()[0] == 1
    with book.read_transaction() as (_, state):
        epoch = state['statistics_history_epoch']
        assert state == dict(before, version=before['version']+1, statistics_history_epoch=epoch)
        assert book.daily_pnl(state) == risk_before
        assert len(state['closed']) == 1
    assert reset_history(book.path, request, 'retry', now[0]+1)['id'] == request
    for days in (0, 7, 30):
        result = book.statistics(days)
        assert result['closed']['count'] == 0 and result['closed']['win_rate'] is None
        assert result['account']['net_pnl'] == '0'
        assert result['account']['realized_pnl'] == '0'
        assert result['account']['initial_equity'] == '1000'
        assert result['fees'] == '0' and result['curve'] == []
    snapshot = book.snapshot()
    assert snapshot['closed'] == [] and 'statistics_history_epoch' not in snapshot
    assert snapshot['statistics_started_at'] == now[0]
    now[0] += 100
    with book.transaction() as (db, state):
        state['closed'].append(closed('new', now[0], '7'))
        book.event(db, 'live_closed', dict(id='new', fee='.20'))
        # Delayed historical settlement must not reappear after the cutoff.
        state['closed'].append(closed('late-old', now[0]-200, '-50'))
        book.event(db, 'live_closed', dict(id='late-old', fee='9'))
    result = book.statistics(0)
    assert result['closed']['count'] == 1 and result['account']['net_pnl'] == '7'
    assert result['fees'] == '.20' or result['fees'] == '0.20'
    assert [row['id'] for row in book.snapshot()['closed']] == ['new']
    assert exchange.writes == []


@pytest.mark.parametrize('field,value', [('positions',[{'id':'position'}]), ('pending',[{'id':'order'}]),
    ('intents',{'uncertain':{'phase':'processing'}}), ('intents',{'filled':{'phase':'filled'}})])
def test_busy_ledger_refuses_reset_without_creating_archive(live, field, value):
    book, exchange, now = live
    with book.transaction() as (_, state):
        state[field] = value
    with book.read_transaction() as (_, state):
        before = copy.deepcopy(state)
    with pytest.raises(ValueError, match='outstanding'):
        reset_history(book.path, str(uuid4()), 'Synthetic', now[0])
    with book.read_transaction() as (_, state):
        assert state == before
    assert not (book.path.parent/'statistics-archives').exists()
    assert exchange.writes == []


def test_prepared_command_blocks_reset_even_without_a_position(live):
    book, exchange, now = live
    book.outbox.prepare('pending-command-123456', '/api/v5/trade/order', {})
    with pytest.raises(ValueError, match='outstanding exchange'):
        reset_history(book.path, str(uuid4()), 'Synthetic', now[0])
    assert not (book.path.parent/'statistics-archives').exists()
    assert exchange.writes == []


def test_epoch_cannot_hide_another_accounts_history(live):
    book, _, now = live
    with book.transaction() as (_, state):
        state['closed'] = [closed('visible', now[0]-10)]
        state['statistics_history_epoch'] = dict(account_reference='other', closed_ids=['visible'], started_at=now[0])
    assert book.statistics()['closed']['count'] == 1
    assert book.statistics()['statistics_started_at'] is None
    assert book.snapshot()['closed'][0]['id'] == 'visible'

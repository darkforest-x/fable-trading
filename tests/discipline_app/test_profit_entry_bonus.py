"""Owner's extra-entry rule: settled whole-trade net winners, once per day."""
import copy
import uuid
from decimal import Decimal
import pytest
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.policy import Policy, Rejected
from yoyo.discipline_app.live_book import LiveBook
from test_discipline_service import Clock, market, ticket
from test_live_book import live, enter, quote


def paper(tmp_path, **policy):
    book = Book(tmp_path/'bonus.sqlite3', Clock(), initial_equity='1000')
    book.configure(Policy(**policy)); market(book)
    return book


def test_only_full_net_win_earns_one_credit_and_survives_restart(tmp_path):
    book = paper(tmp_path, max_entries=1)
    result = book.enter(uuid.uuid4().hex, ticket(fraction='.02', leverage=10, sizing_mode='margin'), 1)
    with book.transaction() as (db, state):
        pos = state['positions'][0]
        book.reduce(db, state, pos, Decimal(pos['remaining'])/2, Decimal('102'), 'take_profit')
    assert book.snapshot()['discipline']['entries_bonus'] == 0
    with pytest.raises(Rejected, match='次数'): book.preview(ticket())
    with book.transaction() as (db, state):
        pos = state['positions'][0]
        book.reduce(db, state, pos, Decimal(pos['remaining']), Decimal('102'), 'take_profit')
    for _ in range(3):
        snap = book.snapshot()
        assert snap['daily']['entries'] == 1
        assert snap['discipline']['entries_limit'] == 2
        assert snap['discipline']['entries_remaining'] == 1
    with book.transaction() as (_, state):
        state['closed'].append(copy.deepcopy(state['closed'][0]))
    restarted = Book(book.path, book.clock)
    assert restarted.snapshot()['discipline']['entries_bonus'] == 1
    assert restarted.preview(ticket(fraction='.02', leverage=10, sizing_mode='margin'))['plan']
    assert result['id'] == snap['closed'][0]['id']
    restarted.enter(uuid.uuid4().hex, ticket(fraction='.02', leverage=10, sizing_mode='margin'), 1)
    with pytest.raises(Rejected, match='次数'): restarted.preview(ticket(symbol='ETH'))


def test_positive_price_move_but_net_loss_does_not_reward(tmp_path):
    book = paper(tmp_path)
    book.enter(uuid.uuid4().hex, ticket(fraction='.02', leverage=10, sizing_mode='margin'), 1)
    with book.transaction() as (db, state):
        pos = state['positions'][0]
        assert Decimal('100.2') > Decimal(pos['entry'])
        book.reduce(db, state, pos, Decimal(pos['remaining']), Decimal('100.2'), 'manual')
    snap = book.snapshot()
    assert Decimal(snap['closed'][0]['pnl']) < 0
    assert snap['discipline']['entries_bonus'] == 0


def test_credit_uses_actual_beijing_close_day_and_does_not_carry(tmp_path):
    book = paper(tmp_path)
    with book.transaction() as (_, state):
        state['closed'] = [dict(id='yesterday', pnl='1', remaining='0', closed=book.clock()-86400),
                           dict(id='today', pnl='1', remaining='0', closed=book.clock()),
                           dict(id='zero', pnl='0', remaining='0', closed=book.clock())]
    assert book.snapshot()['discipline']['entries_bonus'] == 1
    book.clock.advance(86400); market(book)
    assert book.snapshot()['discipline']['entries_bonus'] == 0
    assert book.snapshot()['discipline']['entries_remaining'] == 3


@pytest.mark.parametrize('reason,cooldown', [('当日净值亏损停手',0), ('连续亏损停手',86400)])
def test_winning_credit_never_clears_stop_or_cooldown(tmp_path, reason, cooldown):
    book = paper(tmp_path)
    with book.transaction() as (_, state):
        state['daily']['entries'] = 3
        state['daily']['locked_reason'] = reason
        state['cooldown_until'] = book.clock()+cooldown
        state['closed'] = [dict(id='win', pnl='1', remaining='0', closed=book.clock())]
    assert book.snapshot()['discipline']['entries_bonus'] == 1
    with pytest.raises(Rejected, match=reason): book.preview(ticket())


def test_old_policy_requires_owner_activation_and_preserves_existing_state(tmp_path):
    book = paper(tmp_path, profit_entry_bonus=0)
    with book.transaction() as (_, state):
        state['policy'].pop('profit_entry_bonus')
        state['closed'] = [dict(id='win', pnl='1', remaining='0', closed=book.clock())]
        before = copy.deepcopy(state)
    assert book.snapshot()['discipline']['entries_bonus'] == 0
    book.activate_profit_entry_bonus(); book.activate_profit_entry_bonus()
    after = book.snapshot()
    assert after['policy_revision'] == before['policy_revision']+1
    assert after['daily'] == before['daily']
    assert after['closed'] == before['closed']
    assert after['discipline']['entries_bonus'] == 1


def test_live_credit_waits_for_full_exchange_net_settlement(live):
    book, exchange, now = live
    enter(book)
    order = next(iter(exchange.orders.values()))
    assert book.snapshot()['discipline']['entries_bonus'] == 0
    now[0] += 10; quote(book)
    exchange.positions = []
    assert not book.reconcile()
    assert book.snapshot()['discipline']['entries_bonus'] == 0
    exchange.history = [dict(posId='pos-1',posSide='long',uTime=str(int(now[0]*1000)),type='2',
        closeTotalPos=order['sz'],realizedPnl='1.75',fee='-0.25')]
    for _ in range(4): book.reconcile()
    assert book.snapshot()['discipline']['entries_bonus'] == 1
    assert LiveBook(book.path, exchange, book.clock).snapshot()['discipline']['entries_bonus'] == 1

"""External exchange activity is observed, never counted or operated on."""
import copy


def observed(book):
    with book.read_transaction() as (_, state):
        return copy.deepcopy(state['external_activity'])

import uuid
from decimal import Decimal as D
from test_live_book import live, enter, ticket, quote
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.policy import Rejected
import pytest


def foreign_position():
    return dict(instId='ETH-USDT-SWAP', posSide='long', pos='4', posId='outside-position',
                mgnMode='cross', avgPx='90', notionalUsd='4', upl='-200', cTime='1792331000000')


def test_external_position_orders_and_algos_do_not_reserve_or_block_other_contracts(live):
    book, exchange, _ = live
    exchange.positions = [foreign_position()]
    exchange.foreign = [dict(instId='SOL-USDT-SWAP', posSide='long', ordId='outside-order',
                             clOrdId='outside', sz='100', accFillSz='20', px='100', cTime='1792331000000')]
    exchange.algos['outside-algo'] = dict(instId='SOL-USDT-SWAP', algoId='outside-algo',
                                          algoClOrdId='external-algo', state='live', posSide='long')
    assert book.reconcile()
    state = book.snapshot()
    assert state['blockers'] == []
    assert state['positions'] == state['pending'] == state['closed'] == []
    assert state['daily']['entries'] == state['loss_streak'] == 0
    assert len(observed(book)['current']) == 3
    order = next(r for r in observed(book)['current'] if r['kind'] == 'order')
    assert D(order['notional']) == D('80')
    book.preview(ticket())
    assert exchange.writes == []
    assert book.preview(ticket(symbol='ETH-USDT-SWAP'))['plan']
    assert 'external_activity' not in book.snapshot()
    record_id = observed(book)['current'][0]['id']
    with pytest.raises(Rejected, match='不存在'):
        book.close(uuid.uuid4().hex, record_id)
    with pytest.raises(Rejected, match='不存在'):
        book.cancel(uuid.uuid4().hex, record_id)
    assert exchange.writes == []


def test_external_history_survives_restart_without_inventing_settlement(live):
    book, exchange, now = live
    exchange.positions = [foreign_position()]
    assert book.reconcile()
    first = observed(book)['current'][0]
    assert book.reconcile()
    assert len([x for x in book.events() if x['kind'] == 'external_activity_observed']) == 1
    now[0] += 3
    exchange.positions = []
    assert book.reconcile()
    assert book.reconcile()
    restarted = LiveBook(book.path, exchange, book.clock)
    assert restarted.reconcile()
    state = restarted.snapshot()
    assert observed(book)['current'] == []
    saved = observed(book)['recent'][0]
    assert saved['id'] == first['id'] and saved['status'] == 'absent'
    assert saved['absent_since'] == now[0]
    assert 'closed' not in saved and 'realized_pnl' not in saved
    assert state['closed'] == [] and state['loss_streak'] == 0
    assert exchange.writes == []


def test_external_equity_changes_do_not_consume_daily_loss_but_managed_loss_does(live):
    book, exchange, _ = live
    exchange.equity = '500'  # Loss/withdrawal outside this App.
    assert book.reconcile()
    state = book.snapshot()
    assert state['daily']['locked_reason'] is None
    assert D(state['discipline']['daily_loss_remaining']) == 170
    book.preview(ticket())
    _, _, _ = enter(book)
    with book.transaction() as (_, state):
        state['positions'][0]['pnl'] = '-175'
    state = book.snapshot()
    assert state['daily']['locked_reason'] == '当日净值亏损停手'
    assert D(state['discipline']['daily_loss_remaining']) == 0
    # Whole-account external floating PnL must not leak into managed statistics.
    assert book.statistics()['account']['unrealized_pnl'] == '0.00'


def test_midday_upgrade_keeps_managed_losses_and_counters_but_removes_external_loss_lock(live):
    book, exchange, _ = live
    with book.transaction() as (_, state):
        state['daily'].pop('app_start_pnl')
        state['daily']['entries'] = 2
        state['daily']['locked_reason'] = '当日净值亏损停手'
        state['loss_streak'] = 1
        state['closed'].append(dict(id='managed-loss', closed=book.clock()-5, pnl='-20', remaining='0'))
    exchange.equity = '500'
    assert book.reconcile()
    state = book.snapshot()
    assert state['daily']['entries'] == 2 and state['loss_streak'] == 1
    assert state['daily']['locked_reason'] is None
    assert D(state['discipline']['daily_loss_remaining']) == 150
    restarted = LiveBook(book.path, exchange, book.clock)
    assert restarted.reconcile()
    assert D(restarted.snapshot()['discipline']['daily_loss_remaining']) == 150


def test_new_day_marks_managed_carry_and_preserves_independent_cooldown(live):
    book, exchange, now = live
    enter(book)
    now[0] += 86400
    quote(book, '101')
    assert book.reconcile()
    first = book.snapshot()
    assert D(first['discipline']['daily_loss_remaining']) == 170
    quote(book, '100')
    later = book.snapshot()
    quantity = D(later['positions'][0]['remaining'])
    assert D(later['discipline']['daily_loss_remaining']) == 170 - quantity


def test_app_close_client_id_is_not_misclassified_as_external(live):
    book, exchange, _ = live
    _, identity, _ = enter(book)
    key = uuid.uuid4().hex
    book.close(key, identity)
    payload = next(payload for _, payload in exchange.writes if payload.get('ordType') == 'market')
    exchange.foreign = [dict(payload, ordId='own-close', state='live')]
    assert book.reconcile()
    assert not observed(book)['current']


def test_external_activity_cannot_change_confirmed_managed_protection(live):
    book, exchange, _ = live
    enter(book)
    before = copy.deepcopy(exchange.algos)
    exchange.positions.append(foreign_position())
    exchange.equity = '800'
    writes = copy.deepcopy(exchange.writes)
    assert book.reconcile()
    assert exchange.writes == writes and exchange.algos == before
    state = book.snapshot()
    assert state['positions'][0]['protection'] == 'exchange-verified'
    assert state['blockers'] == []
    assert state['daily']['entries'] == 1


def test_external_non_usdt_pnl_is_not_mislabeled_and_unknown_contract_uses_usd(live):
    book, exchange, _ = live
    exchange.positions = [dict(foreign_position(), instId='BTC-USD-SWAP', upl='0.01'),
                          dict(foreign_position(), instId='NEW-USDT-SWAP', posId='new-position', notionalUsd='12.5')]
    assert book.reconcile()
    rows = observed(book)['current']
    inverse = next(r for r in rows if r['symbol'] == 'BTC-USD-SWAP')
    assert inverse['pnl'] is None and inverse['notional'] is None
    unknown = next(r for r in rows if r['symbol'] == 'NEW-USDT-SWAP')
    assert unknown['notional'] == '12.5' and unknown['value_currency'] == 'USD'

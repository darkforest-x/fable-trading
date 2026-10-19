"""Behavioral evidence for isolated paper enforcement, not live approval."""
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.policy import Policy, Rejected, dec


class Clock:
    def __init__(self):
        self.value = 1792310400.0
    def __call__(self):
        return self.value
    def advance(self, seconds):
        self.value += seconds


def market(book, price='100', symbols=('BTC', 'ETH', 'SOL')):
    instruments = {f'{s}-USDT-SWAP': dict(symbol=f'{s}-USDT-SWAP', step='0.01',
        minimum='0.01', tick='0.01', max_leverage=100) for s in symbols}
    quotes = {s: dict(last=str(price), bid=str(price), ask=str(price), time=book.clock(), source='test fixture') for s in instruments}
    book.update_market(instruments, quotes)


def ticket(symbol='BTC', side='long', kind='market', **changes):
    result = dict(symbol=f'{symbol}-USDT-SWAP', side=side, kind=kind,
                  price='100', stop='95' if side == 'long' else '105', fraction='1', leverage=3)
    result.update(changes)
    return result


def enter(book, request=None, key=None):
    return book.enter(key or uuid.uuid4().hex, request or ticket(), 1)


@pytest.fixture
def book(tmp_path):
    b = Book(tmp_path / 'paper.sqlite3', Clock())
    # Legacy risk-budget cases deliberately exclude the new collateral cap.
    b.configure(Policy(major_margin_fraction=None, alt_margin_fraction=None, major_allowance_scope="shared"))
    market(b)
    return b


def test_initial_policy_never_implicitly_enables_live(book):
    state = book.snapshot()
    assert state['mode'] == 'paper'
    assert state['policy']['risk_fraction'] == '0.05'
    assert state['policy']['major_leverage'] == 100
    assert state['policy']['alt_leverage'] == 20
    assert state['policy']['daily_loss_fraction'] == '0.17'


def test_unconfigured_and_stale_feed_reject(tmp_path, book):
    unconfigured = Book(tmp_path / 'empty.sqlite3', Clock())
    with pytest.raises(Rejected, match='规则尚未配置'):
        enter(unconfigured)
    book.clock.advance(16)
    with pytest.raises(Rejected, match='过期'):
        enter(book)


def test_client_cannot_override_size_or_equity(book):
    for key in ('quantity', 'equity', 'risk', 'mode'):
        with pytest.raises(Rejected, match='服务端计算'):
            book.preview({**ticket(), key: '99999'})
    plan = book.preview(ticket())['plan']
    assert dec(plan['risk']) <= 250
    assert dec(plan['price_risk']) < dec(plan['risk'])


def test_higher_leverage_does_not_increase_quantity(book):
    one = book.preview(ticket(leverage=1))['plan']
    three = book.preview(ticket(leverage=3))['plan']
    assert one['quantity'] == three['quantity']
    hundred = book.preview(ticket(leverage=100))['plan']
    assert hundred['quantity'] == one['quantity']
    assert hundred['margin_mode'] == 'cross'
    with pytest.raises(Rejected, match='超过'):
        book.preview(ticket(symbol='SOL', leverage=21))


def test_invalid_ticket_names_field_and_does_not_consume_entry(book):
    client = TestClient(create_app(book, 'x' * 36, market_enabled=False))
    headers = {'Authorization': 'Bearer ' + 'x' * 36}
    for request, detail in [(ticket(stop=''), '请填写初始止损价格'),
                            (ticket(stop='NaN'), '初始止损价格格式不正确'),
                            (ticket(kind='limit', price=''), '请填写委托价格')]:
        response = client.post('/v1/preview', json=request, headers=headers)
        assert response.status_code == 409
        assert detail in response.json()['detail']
    assert book.snapshot()['daily']['entries'] == 0
    assert not book.snapshot()['positions']


def test_isolated_rejection_advice_is_feasible_and_cross_keeps_own_guard(tmp_path):
    b = Book(tmp_path / 'isolated.sqlite3', Clock())
    b.configure(replace(Policy(), margin_mode='isolated'))
    market(b)
    request = ticket(kind='limit', price='100', stop='95', leverage=100)
    with pytest.raises(Rejected, match='19x 或以下'):
        b.preview(request)
    assert b.preview({**request, 'leverage': 19})['plan']['margin_mode'] == 'isolated'
    with pytest.raises(Rejected, match='19x 或以下'):
        b.preview({**request, 'fraction': '0.1'})
    assert b.snapshot()['daily']['entries'] == 0


def test_idempotency_restart_and_payload_conflict(book):
    key = uuid.uuid4().hex
    first = enter(book, key=key)
    reopened = Book(book.path, book.clock)
    assert enter(reopened, key=key) == first
    assert reopened.snapshot()['daily']['entries'] == 1
    assert len(reopened.snapshot()['positions']) == 1
    with pytest.raises(Rejected, match='不同指令'):
        enter(reopened, ticket('ETH'), key)


def test_concurrent_entry_race_serializes(book):
    barrier = threading.Barrier(8)
    def submit(_):
        other = Book(book.path, book.clock)
        barrier.wait()
        try:
            return enter(other)
        except Rejected:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert sum(r is not None for r in results) == 1
    assert book.snapshot()['daily']['entries'] == 1


def test_zero_fill_pending_cancellation_refunds_daily_count(book):
    for symbol in ('BTC', 'ETH', 'SOL'):
        enter(book, ticket(symbol, kind='limit', price='99', stop='94'))
    assert len(book.snapshot()['pending']) == 3
    for order in book.snapshot()['pending']:
        book.cancel(uuid.uuid4().hex, order['id'])
    assert book.snapshot()['daily']['entries'] == 0
    assert enter(book)['status'] == 'filled'


@pytest.mark.parametrize('side,first,third,fifth', [('long', '106', '117', '128'), ('short', '94', '83', '72')])
def test_r_stages_fixed_quantity_restart_and_no_double_execution(book, side, first, third, fifth):
    enter(book, ticket(side=side))
    initial = book.snapshot()['positions'][0]
    market(book, first)
    pos = book.snapshot()['positions'][0]
    assert pos['breakeven'] is True
    assert pos['risk'] == initial['risk']
    assert pos['remaining'] == pos['quantity']
    assert (dec(pos['stop']) - dec(pos['entry'])) * pos['direction'] > 0
    market(book, third)
    pos = book.snapshot()['positions'][0]
    expected = dec(initial['quantity']) - (dec(initial['quantity']) * Decimal('.6') // Decimal('.01')) * Decimal('.01')
    assert dec(pos['remaining']) == expected
    assert pos['stages'] == [0]
    assert book.snapshot()['cooldown_until'] == 0
    reopened = Book(book.path, book.clock)
    market(reopened, third)
    assert reopened.snapshot()['positions'][0]['remaining'] == pos['remaining']
    market(reopened, fifth)
    assert reopened.snapshot()['positions'] == []
    assert dec(reopened.snapshot()['closed'][0]['pnl']) > 0
    assert reopened.snapshot()['cooldown_until'] == 0
    assert [e['body']['reason'] for e in reopened.events() if e['kind'] == 'paper_reduce'] == ['tp_5R', 'tp_3R']


@pytest.mark.parametrize('side,price', [('long', '140'), ('short', '60')])
def test_gap_over_both_targets_reduces_exactly_original_quantity(book, side, price):
    enter(book, ticket(side=side))
    quantity = dec(book.snapshot()['positions'][0]['quantity'])
    market(book, price)
    reductions = [e['body'] for e in book.events() if e['kind'] == 'paper_reduce']
    assert sum(dec(e['quantity']) for e in reductions) == quantity
    assert len(reductions) == 2
    assert book.snapshot()['positions'] == []


def test_legacy_loss_cooldown_survives_restart_and_midnight(book):
    with book.transaction() as (_, state):
        state['policy']['cooldown_after_losses'] = 1
    enter(book)
    market(book, '90')
    state = book.snapshot()
    assert len(state['positions']) == 0
    assert state['cooldown_until'] == book.clock() + 86400
    book.clock.advance(3600)
    reopened = Book(book.path, book.clock)
    market(reopened)
    with pytest.raises(Rejected, match='冷静期'):
        enter(reopened)
    book.clock.advance(86401)
    market(reopened)
    assert enter(reopened)['status'] == 'filled'


def test_stops_never_loosen_and_close_bypasses_entry_lock(book):
    enter(book)
    pos = book.snapshot()['positions'][0]
    with pytest.raises(Rejected, match='只能收紧'):
        book.tighten(uuid.uuid4().hex, pos['id'], '94')
    book.tighten(uuid.uuid4().hex, pos['id'], '96')
    for s in ('ETH', 'SOL'):
        enter(book, ticket(s, kind='limit', price='90', stop='85'))
    assert book.snapshot()['blockers']
    assert book.close(uuid.uuid4().hex, pos['id'])['status'] == 'closed'


def test_daily_equity_loss_cancels_pending_and_is_sticky(book):
    # Use an isolated engineering fixture to reach the guard without a price gap.
    with book.transaction() as (_, state):
        state['policy']['daily_loss_fraction'] = '0.005'
    enter(book)
    enter(book, ticket('ETH', kind='limit', price='90', stop='85'))
    market(book, '99')
    state = book.snapshot()
    assert state['daily']['locked_reason'] == '当日净值亏损停手'
    assert state['pending'] == []
    market(book, '100')
    assert book.snapshot()['daily']['locked_reason'] == '当日净值亏损停手'
    assert book.close(uuid.uuid4().hex, state['positions'][0]['id'])['status'] == 'closed'


def test_policy_delay_does_not_clear_existing_state(book):
    enter(book, ticket(kind='limit', price='90', stop='85'))
    book.configure(replace(Policy(), max_entries=8))
    assert book.snapshot()['policy']['max_entries'] == 3
    with pytest.raises(Rejected, match='已有待生效'):
        book.configure(replace(Policy(), max_entries=9))
    book.clock.advance(86401)
    market(book)
    assert book.snapshot()['policy']['max_entries'] == 8
    assert book.snapshot()['policy_revision'] == 2


def test_api_auth_strict_body_and_no_risk_bypass_routes(book):
    token = 'test-only-token-never-a-live-secret-12345'
    with TestClient(create_app(book, token, market_enabled=False)) as client:
        assert client.get('/health').json()['live_enabled'] is False
        assert client.get('/v1/state').status_code == 401
        client.headers['Authorization'] = f'Bearer {token}'
        assert client.get('/v1/state').status_code == 200
        assert client.post('/v1/preview', json={**ticket(), 'quantity': 100}).status_code == 422
        for route in ('/v1/policy', '/v1/quotes', '/v1/balance', '/v1/reset', '/v1/live'):
            assert client.post(route, json={}).status_code == 404
        body = dict(request_id=uuid.uuid4().hex, policy_revision=1, ticket=ticket())
        first = client.post('/v1/orders', json=body)
        assert first.status_code == 200
        assert client.post('/v1/orders', json=body).json() == first.json()


def test_invalid_numbers_do_not_mutate_book(book):
    for value in ('NaN', 'Infinity', '-1', '0', ''):
        with pytest.raises(Rejected):
            enter(book, ticket(stop=value))
    assert book.snapshot()['daily']['entries'] == 0
    assert book.snapshot()['positions'] == []


@pytest.mark.parametrize('side,trigger', [('long', '106'), ('short', '94')])
def test_cost_breakeven_covers_modeled_fees_and_slippage(book, side, trigger):
    enter(book, ticket(side=side))
    market(book, trigger)
    stop = book.snapshot()['positions'][0]['stop']
    market(book, stop)
    assert book.snapshot()['positions'] == []
    assert dec(book.snapshot()['closed'][-1]['pnl']) >= 0
    assert book.snapshot()['cooldown_until'] == 0


def test_gap_loss_can_exceed_budget_and_triggers_daily_lock(book):
    for symbol in ('BTC', 'ETH', 'SOL'):
        enter(book, ticket(symbol))
    market(book, '70')
    state = book.snapshot()
    assert state['positions'] == []
    assert state['loss_streak'] == 3
    assert state['daily']['locked_reason'] is not None
    assert state['cooldown_until'] == book.clock() + 86400
    assert dec(state['equity']) < dec(state['daily']['start_equity']) * Decimal('.83')


def test_partial_profit_does_not_count_as_loss_or_start_cooldown(book):
    enter(book)
    market(book, '117')
    state = book.snapshot()
    assert state['positions'] and state['cooldown_until'] == 0
    assert state['loss_streak'] == 0


def test_pending_cross_midnight_consumes_new_days_entry_count(book):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    book.clock.value = datetime(2026, 10, 18, 23, 59, 50, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
    market(book)
    enter(book, ticket(kind='limit', price='99', stop='94'))
    book.clock.advance(20)
    market(book, '98')
    state = book.snapshot()
    assert state['daily']['key'] == '2026-10-19'
    assert state['daily']['entries'] == 1
    assert len(state['positions']) == 1


def test_pending_fill_rechecks_current_equity_budget(book):
    enter(book, ticket(kind='limit', price='99', stop='94'))
    # An independently reconciled equity change while the reservation waits.
    with book.transaction() as (_, state):
        state['cash'] = '4800'
    market(book, '99')
    assert book.snapshot()['positions'] == []
    assert book.snapshot()['pending'] == []
    assert '风险预算' in book.events()[0]['body']['reason']


def test_daily_loss_reason_cannot_make_streak_lock_permanent(book):
    for symbol in ('BTC', 'ETH', 'SOL'):
        enter(book, ticket(symbol))
    market(book, '70')
    book.clock.advance(86401)
    market(book)
    state = book.snapshot()
    assert state['loss_streak'] == 0
    assert state['daily']['locked_reason'] is None
    assert state['blockers'] == []


def gross_exposure(state):
    policy = Policy.parse(state['policy'])
    return sum((dec(p['remaining']) * dec(state['quotes'][p['symbol']]['last']) /
                policy.exposure_cap(p['symbol']) for p in state['positions']), Decimal(0))


@pytest.mark.parametrize('symbol,leverage,cap', [('BTC',100,10), ('ETH',100,10), ('SOL',20,5)])
def test_cross_margin_sizes_to_account_cap_after_costs(book, symbol, leverage, cap):
    result = enter(book, ticket(symbol, stop='100', leverage=leverage))
    state = book.snapshot()
    assert result['plan']['exposure_limited'] is True
    assert gross_exposure(state) <= dec(state['equity'])
    assert dec(result['plan']['notional']) <= dec(state['equity']) * cap
    assert dec(result['plan']['risk']) <= 250
    assert state['positions'][0]['margin_mode'] == 'cross'


def test_major_and_alt_share_capacity_without_netting(book):
    enter(book, ticket('BTC', stop='100', fraction='.3', leverage=100))
    enter(book, ticket('SOL', side='short', stop='100', leverage=20))
    state = book.snapshot()
    assert len(state['positions']) == 2
    assert gross_exposure(state) <= dec(state['equity'])
    assert dec(state['positions'][1]['risk']) < dec(state['equity']) * Decimal('.05')


def test_pending_reserves_capacity_and_rechecks_at_fill(book):
    enter(book, ticket(kind='limit', price='99', stop='98.99', leverage=100))
    with pytest.raises(Rejected, match='最小委托量'):
        book.preview(ticket('ETH', stop='100', leverage=100))
    with book.transaction() as (_, state):
        state['cash'] = '4600'  # Below exposure capacity, still within risk/daily budgets.
    market(book, '99')
    state = book.snapshot()
    assert not state['positions'] and not state['pending']
    assert any('总名义仓位' in e['body'].get('reason', '') for e in book.events())


def test_concurrent_different_symbols_share_capacity(book):
    barrier = threading.Barrier(2)
    def submit(symbol):
        other = Book(book.path, book.clock)
        barrier.wait()
        try:
            return enter(other, ticket(symbol, stop='100', leverage=100))
        except Rejected:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ('BTC', 'ETH')))
    assert any(results)
    state = book.snapshot()
    assert gross_exposure(state) <= dec(state['equity'])


def test_legacy_policy_requires_delay_and_flat_activation(book):
    with book.transaction() as (_, state):
        state['policy']['risk_fraction'] = '.02'
        for key in ('margin_mode','major_exposure_multiple','alt_exposure_multiple','cooldown_after_losses'):
            del state['policy'][key]
    assert book.snapshot()['policy']['margin_mode'] == 'isolated'
    with pytest.raises(Rejected, match='杠杆过高'):
        book.preview(ticket(leverage=100))
    enter(book)
    book.configure(Policy())
    book.clock.advance(86401)
    market(book)
    assert book.snapshot()['policy']['risk_fraction'] == '.02'
    book.close(uuid.uuid4().hex, book.snapshot()['positions'][0]['id'])
    market(book)
    state = book.snapshot()
    assert state['policy']['margin_mode'] == 'cross'
    assert state['policy']['risk_fraction'] == '0.05'
    assert state['cooldown_until'] > book.clock()


def test_statistics_empty_and_partial_pnl_not_counted_as_closed(book):
    empty = book.statistics(0)
    assert empty['closed']['win_rate'] is None
    assert empty['closed']['payoff_ratio'] is None
    assert empty['curve'] == []
    enter(book)
    market(book, '117')
    stats = book.statistics(0)
    assert stats['closed']['count'] == 0
    assert dec(stats['fees']) > 0
    assert dec(stats['account']['realized_pnl']) > 0
    assert dec(stats['account']['initial_equity']) == 5000
    assert dec(stats['account']['net_pnl']) == dec(stats['account']['realized_pnl']) + dec(stats['account']['unrealized_pnl'])
    market(book, '128')
    stats = book.statistics(0)
    trade = book.snapshot()['closed'][0]
    assert stats['closed']['count'] == 1
    assert dec(stats['closed']['win_rate']) == 1
    assert dec(stats['closed']['average_net_r']) == dec(trade['pnl']) / dec(trade['risk'])
    assert stats['curve'][-1]['pnl'] == stats['closed']['net_pnl']
    assert stats['symbols'][0]['count'] == 1
    assert stats['sides'][0]['count'] == 1
    book.clock.advance(8*86400)
    recent = book.statistics(7)
    assert recent['closed']['count'] == 0 and dec(recent['fees']) == 0
    assert book.statistics(30)['closed']['count'] == 1
    assert recent['account'] == stats['account']


def test_statistics_uses_full_history_and_execution_fee_timing(book):
    with book.transaction() as (_, state):
        state['closed'] = [dict(id=str(i), symbol='BTC-USDT-SWAP', side='long',
                                pnl='10' if i%2 else '-5', risk='5', closed=book.clock()-60)
                           for i in range(40)]
        state['cash'] = '5100'
    assert len(book.snapshot()['closed']) == 30
    stats = book.statistics(7)
    assert stats['closed']['count'] == 40
    assert dec(stats['closed']['net_pnl']) == 100
    assert dec(stats['closed']['payoff_ratio']) == 2
    assert dec(stats['closed']['average_net_r']) == Decimal('.5')
    assert stats['account']['initial_equity'] == '5000'


def test_statistics_route_authenticated_and_period_validated(book):
    with TestClient(create_app(book, 'test-only-statistics-token-1234567890', market_enabled=False)) as client:
        assert client.get('/v1/statistics').status_code == 401
        client.headers['Authorization'] = 'Bearer test-only-statistics-token-1234567890'
        assert client.get('/v1/statistics?days=7').json()['period_days'] == 7
        assert client.get('/v1/statistics?days=9').status_code == 409

"""Paper scale-ins keep one lifecycle while resetting its risk baseline."""
import uuid
from decimal import Decimal as D

import pytest

from yoyo.discipline_app.book import Book
from yoyo.discipline_app.policy import Policy, Rejected
from yoyo.discipline_app.scaling import merged_position, remaining_risk


class Clock:
    def __init__(self):
        self.value = 1792310400.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def market(book, price='100'):
    instruments = {f'{symbol}-USDT-SWAP': dict(symbol=f'{symbol}-USDT-SWAP', step='0.01',
        minimum='0.01', tick='0.01', max_leverage=100) for symbol in ('BTC', 'ETH', 'SOL')}
    quotes = {symbol: dict(last=str(price), bid=str(price), ask=str(price), time=book.clock(), source='test fixture')
              for symbol in instruments}
    book.update_market(instruments, quotes)


def ticket(symbol='BTC', side='long', kind='market', **changes):
    result = dict(symbol=f'{symbol}-USDT-SWAP', side=side, kind=kind,
        price='100', stop='95' if side == 'long' else '105', fraction='1', leverage=3)
    result.update(changes)
    return result


def _book(tmp_path, **policy_changes):
    book = Book(tmp_path / 'scale-in.sqlite3', Clock(), initial_equity='1000')
    book.configure(Policy(**policy_changes))
    market(book)
    return book


def _enter(book, request):
    return book.enter(uuid.uuid4().hex, request, 1)


def test_same_direction_scale_merges_current_remainder_and_resets_baseline(tmp_path):
    book = _book(tmp_path, max_positions=1)
    first = _enter(book, ticket(fraction='.03', leverage=10, sizing_mode='margin'))
    original = book.snapshot()['positions'][0]
    book.close(uuid.uuid4().hex, original['id'], '.5')
    reduced = book.snapshot()['positions'][0]
    prior_pnl = D(reduced['pnl'])
    previous_remaining = D(reduced['remaining'])
    previous_entry = D(reduced['entry'])

    market(book, '101')
    add_request = ticket(stop='95', fraction='.10', leverage=10, sizing_mode='margin')
    preview = book.preview(add_request)['plan']
    assert preview['add_to'] == original['id']
    assert preview['stop'] == reduced['stop'] == '95'
    assert preview['quantity'] != first['plan']['quantity']

    result = _enter(book, add_request)
    assert result['status'] == 'filled'
    state = book.snapshot()
    assert len(state['positions']) == 1
    merged = state['positions'][0]
    added_quantity = D(result['plan']['quantity'])
    added_entry = D(result['plan']['entry'])
    expected_quantity = previous_remaining + added_quantity
    expected_entry = (previous_remaining * previous_entry + added_quantity * added_entry) / expected_quantity

    assert merged['id'] == original['id']
    assert D(merged['quantity']) == expected_quantity
    assert D(merged['remaining']) == expected_quantity
    assert D(merged['entry']) == expected_entry
    assert merged['stop'] == merged['original_stop'] == reduced['stop']
    assert merged['leverage'] == reduced['leverage'] == 10
    assert merged['breakeven'] is False and merged['stages'] == []
    assert 'current_r' not in merged
    assert len(merged['baseline_history']) == 1
    assert D(merged['risk']) == remaining_risk(merged, book.policy(state))
    assert D(merged['risk']) <= D(state['equity']) * D(state['policy']['risk_fraction'])
    assert D(merged['margin']) <= D(state['equity']) * D('.10')
    expected_pnl = prior_pnl - added_quantity * added_entry * D(state['policy']['fee_rate'])
    assert D(merged['pnl']) == expected_pnl
    assert state['daily']['entries'] == 2
    assert not state['blockers']


def test_opposite_side_and_same_symbol_pending_still_block_scale_in(tmp_path):
    book = _book(tmp_path)
    opened = _enter(book, ticket(fraction='.03', leverage=10, sizing_mode='margin'))
    with pytest.raises(Rejected, match='反向持仓'):
        book.preview(ticket(side='short', fraction='.03', leverage=10, sizing_mode='margin'))

    pending = _enter(book, ticket(kind='limit', price='99', fraction='.03', leverage=10, sizing_mode='margin'))
    assert pending['status'] == 'pending'
    with pytest.raises(Rejected, match='已有挂单'):
        book.preview(ticket(fraction='.03', leverage=10, sizing_mode='margin'))
    assert opened['status'] == 'filled'


def test_pending_scale_binds_parent_snapshot_and_rejection_refunds_once(tmp_path):
    book = _book(tmp_path)
    opened = _enter(book, ticket(fraction='.03', leverage=10, sizing_mode='margin'))
    parent = book.snapshot()['positions'][0]
    with pytest.raises(Rejected, match='必须与现有持仓一致'):
        book.preview(ticket(kind='limit', price='99', stop='90', fraction='.05',
                            leverage=99, sizing_mode='margin'))
    pending = _enter(book, ticket(kind='limit', price='99', stop='95', fraction='.05',
                                   leverage=10, sizing_mode='margin'))
    assert pending['status'] == 'pending'
    saved_order = next(x for x in book.snapshot()['pending'] if x['id'] == pending['id'])
    assert saved_order['plan']['add_to'] == opened['id']
    assert saved_order['plan']['parent_snapshot']['stop'] == parent['stop']
    assert book.snapshot()['daily']['entries'] == 2

    book.tighten(uuid.uuid4().hex, parent['id'], '96')
    market(book, '98')
    state = book.snapshot()
    assert state['pending'] == []
    assert len(state['positions']) == 1
    assert state['daily']['entries'] == 1
    assert any(event['kind'] == 'pending_cancelled_by_guard' for event in book.events())

    with book.transaction() as (db, mutable):
        assert book.refund_entry(db, mutable, saved_order) is False
    assert book.snapshot()['daily']['entries'] == 1


def test_cancel_refunds_only_original_beijing_day(tmp_path):
    book = _book(tmp_path)
    pending = _enter(book, ticket(kind='limit', price='99'))
    order = next(x for x in book.snapshot()['pending'] if x['id'] == pending['id'])
    assert book.snapshot()['daily']['entries'] == 1
    book.cancel(uuid.uuid4().hex, pending['id'])
    assert book.snapshot()['daily']['entries'] == 0
    assert len([e for e in book.events() if e['kind'] == 'entry_quota_refunded']) == 1

    later = _enter(book, ticket(kind='limit', price='99'))
    book.clock.advance(86400)
    assert book.snapshot()['daily']['entries'] == 0
    book.cancel(uuid.uuid4().hex, later['id'])
    assert book.snapshot()['daily']['entries'] == 0
    assert len([e for e in book.events() if e['kind'] == 'entry_quota_refunded']) == 1
    assert order['entry_day'] != book.snapshot()['daily']['key']


@pytest.mark.parametrize('direction,entry,stop,add_entry', [
    (1, '100', '95', '102'),
    (-1, '100', '105', '98'),
])
def test_merge_helper_uses_remaining_quantity_and_signed_risk(direction, entry, stop, add_entry):
    policy = Policy()
    pos = dict(id='root', direction=direction, side='long' if direction == 1 else 'short',
        quantity='10', remaining='4', entry=entry, stop=stop, original_stop=stop,
        risk='52', margin='100', leverage=10, entry_fee='1', pnl='-4', stages=[0],
        breakeven=True, current_r='3')
    old_risk = remaining_risk(pos, policy)
    result = merged_position(pos, '2', add_entry, '.2', policy)
    expected_entry = (D('4') * D(entry) + D('2') * D(add_entry)) / D('6')
    assert D(result['entry']) == expected_entry
    assert result['quantity'] == result['remaining'] == '6'
    assert D(result['risk']) == D('6') * ((expected_entry - D(stop)) * direction +
        expected_entry * (D('2') * D(policy.fee_rate) + D(policy.slippage_rate)))
    assert result['risk'] != pos['risk']
    assert result['stop'] == result['original_stop'] == stop
    assert result['breakeven'] is False and result['stages'] == []
    assert result['baseline_history'][0]['remaining'] == '4'
    assert pos['pnl'] == '-4'
    assert old_risk > 0


def test_merge_helper_fails_closed_when_stop_is_not_loss_side_of_new_average():
    pos = dict(direction=1, quantity='10', remaining='2', entry='100', stop='103',
               original_stop='95', risk='50', margin='100', leverage=10, entry_fee='1')
    with pytest.raises(Rejected, match='止损基线无效'):
        merged_position(pos, '1', '101', '.1', Policy())

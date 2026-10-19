"""Partial-close behavior for the paper and fake-OKX books."""
import copy
import hashlib
import uuid
from decimal import Decimal

import pytest

from yoyo.discipline_app.book import Book, encoded
from yoyo.discipline_app.closing import close_quantity
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.okx_execution import ExchangeRejected, ExchangeUncertain
from yoyo.discipline_app.policy import Policy, Rejected, dec, floor_step


class Clock:
    def __init__(self):
        self.value = 1792310400.0

    def __call__(self):
        return self.value


def market(book, price='100', minimum='0.01'):
    instruments = {'BTC-USDT-SWAP': dict(symbol='BTC-USDT-SWAP', step='0.01',
        minimum=minimum, tick='0.01', max_leverage=100)}
    quote = dict(last=str(price), bid=str(price), ask=str(price),
        time=book.clock(), source='test fixture')
    book.update_market(instruments, {'BTC-USDT-SWAP': quote})


def paper_book(tmp_path, minimum='0.01'):
    book = Book(tmp_path / 'paper.sqlite3', Clock())
    book.configure(Policy(major_margin_fraction=None, alt_margin_fraction=None))
    market(book, minimum=minimum)
    return book


def enter_paper(book):
    request = dict(symbol='BTC-USDT-SWAP', side='long', kind='market', price='100',
        stop='95', fraction='1', leverage=3)
    return book.enter(uuid.uuid4().hex, request, 1)['id']


class FakeExchange:
    """Stateful OKX-shaped fake; writes only to local dictionaries."""
    environment = 'okx_live'
    write_enabled = True

    def __init__(self, clock):
        self.clock = clock
        self.orders = {}
        self.algos = {}
        self.positions = []
        self.history = []
        self.writes = []
        self.leverage = '100'
        self.timeout_next_close = False

    def get(self, path, **params):
        if path.endswith('/config'):
            return [dict(acctLv='2', posMode='long_short_mode',
                perm='read_only,trade', uid='partial-close-fixture')]
        if path.endswith('/balance'):
            return [dict(details=[dict(ccy='USDT', eq='1000', cashBal='1000', availEq='1000')])]
        if path.endswith('/positions'):
            return copy.deepcopy(self.positions)
        if path.endswith('/positions-history'):
            return copy.deepcopy(self.history)
        if path.endswith('/orders-pending'):
            return []
        if path.endswith('/orders-algo-pending'):
            rows = [row for row in self.algos.values() if row['state'] == 'live']
            return copy.deepcopy(rows if params['ordType'] == 'conditional' else [])
        if path.endswith('/trade-fee'):
            return [dict(feeGroup=[dict(groupId='2', taker='-0.0005')])]
        if path.endswith('/leverage-info'):
            return [dict(lever=self.leverage)]
        if path.endswith('/order'):
            row = next((item for item in self.orders.values()
                if item.get('clOrdId') == params.get('clOrdId')
                or item.get('ordId') == params.get('ordId')), None)
            if row is None:
                raise ExchangeRejected('51603')
            return [copy.deepcopy(row)]
        if path.endswith('/order-algo'):
            row = next((item for item in self.algos.values()
                if item.get('algoClOrdId') == params.get('algoClOrdId')
                or item.get('algoId') == params.get('algoId')), None)
            if row is None:
                raise ExchangeRejected('51603')
            return [copy.deepcopy(row)]
        raise AssertionError(path)

    def post(self, path, payload):
        self.writes.append((path, copy.deepcopy(payload)))
        if path.endswith('/set-leverage'):
            self.leverage = payload['lever']
            return [dict(sCode='0', lever=self.leverage)]
        if path.endswith('/order'):
            order = dict(payload, ordId=str(len(self.orders) + 1), state='filled',
                accFillSz=payload['sz'], avgPx='100', fee='-0.05', feeCcy='USDT',
                cTime=str(int(self.clock() * 1000)))
            self.orders[payload['clOrdId']] = order
            if payload.get('attachAlgoOrds'):
                self.positions.append(dict(instId=payload['instId'], posSide=payload['posSide'],
                    pos=payload['sz'], mgnMode='cross', avgPx='100', posId='partial-pos-1',
                    realizedPnl='-0.05'))
                for child in payload['attachAlgoOrds']:
                    algo = dict(child, algoId=str(len(self.algos) + 1),
                        algoClOrdId=child['attachAlgoClOrdId'], state='live',
                        instId=payload['instId'], posSide=payload['posSide'],
                        side='sell' if payload['side'] == 'buy' else 'buy',
                        sz=child.get('sz', payload['sz']))
                    self.algos[algo['algoClOrdId']] = algo
            else:
                raw = next(row for row in self.positions
                    if row['instId'] == payload['instId'] and row['posSide'] == payload['posSide'])
                raw['pos'] = str(dec(raw['pos']) - dec(payload['sz']))
                raw['realizedPnl'] = str(dec(raw['realizedPnl']) + Decimal('1'))
                if dec(raw['pos']) == 0:
                    self.positions.remove(raw)
                if self.timeout_next_close:
                    self.timeout_next_close = False
                    raise ExchangeUncertain('timeout after exchange fill')
            return [dict(sCode='0', ordId=order['ordId'])]
        if path.endswith('/amend-algos'):
            algo = next(row for row in self.algos.values() if row['algoId'] == payload['algoId'])
            for new, old in (('newSlTriggerPx', 'slTriggerPx'), ('newTpTriggerPx', 'tpTriggerPx')):
                if new in payload:
                    algo[old] = payload[new]
            return [dict(sCode='0', algoId=algo['algoId'])]
        if path.endswith('/cancel-algos'):
            for item in payload:
                next(row for row in self.algos.values() if row['algoId'] == item['algoId'])['state'] = 'canceled'
            return [dict(sCode='0', algoId=item['algoId']) for item in payload]
        raise AssertionError(path)


def live_book(tmp_path):
    now = Clock()
    exchange = FakeExchange(now)
    book = LiveBook(tmp_path / 'live.sqlite3', exchange, now)
    instruments = {'BTC-USDT-SWAP': dict(symbol='BTC-USDT-SWAP', step='.01',
        minimum='.01', tick='.01', contract_value='.01', fee_group='2', max_leverage=100)}
    quotes = {'BTC-USDT-SWAP': dict(last='100', bid='100', ask='100', time=now())}
    book.update_market(instruments, quotes)
    assert book.reconcile()
    request = dict(symbol='BTC-USDT-SWAP', side='long', kind='market', price='',
        stop='95', fraction='.2', leverage=100)
    preview = book.preview(request)
    position_id = uuid.uuid4().hex
    book.enter(position_id, request, preview['policy_revision'], preview['preview_id'], True)
    return book, exchange, position_id


def test_partial_close_uses_percentage_of_current_remaining_quantity(tmp_path):
    book = paper_book(tmp_path)
    identity = enter_paper(book)
    initial = dec(book.snapshot()['positions'][0]['remaining'])

    market(book, '110')
    first = book.close(uuid.uuid4().hex, identity, '.25')
    first_quantity = floor_step(initial * Decimal('.25'), Decimal('.01'))
    assert dec(first['quantity']) == first_quantity
    assert dec(first['remaining']) == initial - first_quantity

    current = dec(first['remaining'])
    second = book.close(uuid.uuid4().hex, identity, '.5')
    second_quantity = floor_step(current * Decimal('.5'), Decimal('.01'))
    assert dec(second['quantity']) == second_quantity
    assert dec(second['remaining']) == current - second_quantity


@pytest.mark.parametrize(('fraction', 'message'), [
    ('.02', '低于最小数量'),
    ('.92', '余仓低于最小数量'),
])
def test_partial_close_rejects_minimum_and_dust_remainder(fraction, message):
    instrument = dict(step='.01', minimum='.05')
    with pytest.raises(Rejected, match=message):
        close_quantity('.50', instrument, fraction)


def test_same_key_retries_normalized_fraction_and_rejects_conflict(tmp_path):
    book = paper_book(tmp_path)
    identity = enter_paper(book)
    market(book, '110')
    key = uuid.uuid4().hex

    first = book.close(key, identity, '.2500')
    remaining = book.snapshot()['positions'][0]['remaining']
    event_count = len(book.events())
    assert book.close(key, identity, Decimal('.25')) == first
    assert book.snapshot()['positions'][0]['remaining'] == remaining
    assert len(book.events()) == event_count
    with pytest.raises(Rejected, match='不同指令'):
        book.close(key, identity, '.5')


def test_full_close_keeps_legacy_receipt_fingerprint(tmp_path):
    book = paper_book(tmp_path)
    identity = enter_paper(book)
    market(book, '110')
    key = uuid.uuid4().hex
    result = book.close(key, identity)

    legacy_payload = dict(action='close', id=identity)
    legacy_fingerprint = hashlib.sha256(encoded(legacy_payload).encode()).hexdigest()
    with book.connect() as db:
        stored = db.execute('SELECT fingerprint FROM receipts WHERE key=?', (key,)).fetchone()[0]
    assert stored == legacy_fingerprint
    assert result['status'] == 'closed'
    assert book.close(key, identity, '1.0') == result


def test_profitable_partial_close_does_not_award_full_close_entry_credit(tmp_path):
    book = paper_book(tmp_path)
    identity = enter_paper(book)
    market(book, '120')
    book.close(uuid.uuid4().hex, identity, '.25')

    state = book.snapshot()
    assert state['positions']
    assert state['closed'] == []
    assert state['discipline']['entries_bonus'] == 0


def test_live_partial_close_timeout_retry_uses_one_outbox_order(tmp_path):
    book, exchange, identity = live_book(tmp_path)
    initial = dec(book.snapshot()['positions'][0]['remaining'])
    key = uuid.uuid4().hex
    exchange.timeout_next_close = True

    with pytest.raises(ExchangeUncertain):
        book.close(key, identity, '.25')

    partial_orders = [payload for path, payload in exchange.writes
        if path.endswith('/order') and payload.get('clOrdId') == book.outbox.inspect(key)['payload']['clOrdId']]
    close_quantity_base = floor_step(initial * Decimal('.25'), Decimal('.01'))
    assert len(partial_orders) == 1
    assert dec(partial_orders[0]['sz']) * Decimal('.01') == close_quantity_base

    receipt = book.close(key, identity, '.2500')
    expected_remaining = initial - close_quantity_base
    assert receipt['status'] == 'close_submitted'
    assert dec(book.snapshot()['positions'][0]['remaining']) == expected_remaining
    with pytest.raises(Rejected, match='不能修改平仓比例'):
        book.close(key, identity, '.5')
    final_orders = [payload for path, payload in exchange.writes
        if path.endswith('/order') and payload.get('clOrdId') == book.outbox.inspect(key)['payload']['clOrdId']]
    assert len(final_orders) == 1

"""Offline sizing, policy migration, tier and shared-leverage evidence."""
import uuid
from decimal import Decimal as D

import pytest

from test_live_book import Exchange, quote, ticket
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.policy import Policy, Rejected, dec


class Clock:
    def __init__(self):
        self.value = 1792332000.0

    def __call__(self):
        return self.value


def paper_exposure(tmp_path, equity='5000'):
    book = Book(tmp_path / 'exposure.sqlite3', Clock(), initial_equity=equity)
    book.configure(Policy().with_exposure_sizing())
    symbols = ('BTC', 'ETH', 'SOL', 'DOGE')
    instruments = {
        f'{symbol}-USDT-SWAP': dict(symbol=f'{symbol}-USDT-SWAP', step='.01',
            minimum='.01', tick='.01', max_leverage=100)
        for symbol in symbols
    }
    quotes = {f'{symbol}-USDT-SWAP': dict(last='100', bid='100', ask='100', time=book.clock())
              for symbol in symbols}
    book.update_market(instruments, quotes)
    return book


def exposure_ticket(symbol='BTC', *, fraction='.5', stop='99.99', leverage=3,
                    sizing_mode='exposure', side='long'):
    return ticket(symbol=f'{symbol}-USDT-SWAP', side=side, fraction=fraction, stop=stop,
                  leverage=leverage, sizing_mode=sizing_mode)


def test_old_policy_schema_stays_margin_and_new_policy_changes_only_sizing_fields(tmp_path):
    legacy_document = Policy().document()
    legacy_document.pop('entry_sizing')
    legacy = Policy.parse(legacy_document)
    assert legacy.entry_sizing == 'margin'
    assert legacy.alt_margin_fraction == '0.05'
    assert legacy.alt_leverage == 20

    new = legacy.with_exposure_sizing()
    assert new.entry_sizing == 'exposure'
    assert new.major_margin_fraction is new.alt_margin_fraction is None
    assert new.major_exposure_multiple == '10'
    assert new.alt_exposure_multiple == '5'
    assert new.major_leverage == 100
    assert new.alt_leverage == 20  # legacy JSON field, ignored by new sizing
    assert new.risk_fraction == legacy.risk_fraction

    book = Book(tmp_path / 'old.sqlite3', Clock())
    book.configure(legacy)
    with book.transaction() as (_, state):
        assert book.policy(state).entry_sizing == 'margin'


def test_fraction_is_share_of_raw_remaining_exposure_and_cannot_use_legacy_modes(tmp_path):
    book = paper_exposure(tmp_path)
    request = exposure_ticket(fraction='.5', stop='99.99', leverage=3)
    result = book.preview(request)['plan']

    assert result['sizing_mode'] == 'exposure'
    assert result['leverage'] == 100  # client hint cannot select low leverage
    assert D(result['notional']) == pytest.approx(D('25000'), abs=D('2'))
    assert D(result['risk']) < D(result['budget'])

    with pytest.raises(Rejected, match='更新 App'):
        book.preview({k: v for k, v in request.items() if k != 'sizing_mode'})
    for mode in ('risk', 'margin'):
        with pytest.raises(Rejected, match='剩余名义额度'):
            book.preview(dict(request, sizing_mode=mode))
    with pytest.raises(Rejected):
        book.preview(dict(request, fraction='0'))
    with pytest.raises(Rejected, match='比例无效'):
        book.preview(dict(request, fraction='1.01'))


def test_alt_allowance_is_shared_but_each_major_has_its_own_pool(tmp_path):
    book = paper_exposure(tmp_path)
    with book.transaction() as (_, state):
        state['positions'].append(dict(id='held-alt', symbol='SOL-USDT-SWAP', side='long',
            direction=1, quantity='100', remaining='100', entry='100', original_stop='99',
            stop='99', risk='110', margin='20', leverage=5, entry_fee='0', pnl='0',
            breakeven=False, stages=[], opened=book.clock(), policy=state['policy'],
            policy_revision=state['policy_revision'], protection='server-paper-only'))

    alt = book.preview(exposure_ticket('DOGE', fraction='.5'))['plan']
    # Alt pool has 25k gross room, less the held SOL 10k: half of 15k remains.
    assert D(alt['notional']) == pytest.approx(D('7500'), abs=D('2'))
    btc = book.preview(exposure_ticket('BTC', fraction='.5'))['plan']
    eth = book.preview(exposure_ticket('ETH', fraction='.5'))['plan']
    assert D(btc['notional']) == pytest.approx(D('25000'), abs=D('2'))
    assert D(eth['notional']) == pytest.approx(D('25000'), abs=D('2'))


def live_exposure(tmp_path, name='exposure-live.sqlite3'):
    now = Clock()
    exchange = Exchange(now)
    book = LiveBook(tmp_path / name, exchange, now,
                    initial_policy=Policy().with_exposure_sizing())
    quote(book)
    assert book.reconcile()
    return book, exchange


def test_live_alt_uses_fractional_contract_tier_and_auto_stop_uses_final_size(tmp_path):
    book, exchange = live_exposure(tmp_path)
    exchange.position_tiers['SOL-USDT'] = [
        dict(instFamily='SOL-USDT', minSz='0', maxSz='100', maxLever='100'),
        dict(instFamily='SOL-USDT', minSz='100.0001', maxSz='1000000', maxLever='66.66'),
    ]
    request = exposure_ticket('SOL', fraction='1', stop='99.99', leverage=100)
    plan = book.preview(request)['plan']
    assert plan['leverage'] == pytest.approx(66.66)
    assert plan['quantity'] and D(plan['margin']) < D(plan['notional'])

    exchange.available = '50'
    auto = book.auto_stop(dict(request, stop=''))
    assert D(auto['plan']['quantity']) < D(plan['quantity'])
    assert dec(auto['plan']['leverage']) == D('66.66')
    realized_risk = D(auto['plan']['quantity']) * (
        D(auto['plan']['entry']) - D(auto['stop'])
        + D(auto['plan']['entry']) * D('.002'))
    assert D('49') < realized_risk <= D('50')


def test_inherited_fractional_leverage_is_preserved_without_set_leverage(tmp_path):
    book, exchange = live_exposure(tmp_path)
    exchange.leverage = '66.66'
    exchange.positions = [dict(instId='BTC-USDT-SWAP', posSide='long', pos='2',
        posId='external-position', mgnMode='cross', avgPx='100', realizedPnl='0')]
    assert book.reconcile()
    request = exposure_ticket('BTC', fraction='.1', stop='99.99', leverage=3)
    preview = book.preview(request)
    assert preview['plan']['leverage'] == pytest.approx(66.66)

    exchange.cancel_entry = True
    receipt = book.enter(uuid.uuid4().hex, request, preview['policy_revision'],
                         preview['preview_id'], True)
    assert receipt['status'] == 'cancelled'
    assert not any(path.endswith('/set-leverage') for path, _ in exchange.writes)
    assert exchange.leverage == '66.66'


def test_shared_tier_quantity_uses_side_positions_and_only_unfilled_order_amount(tmp_path):
    book, exchange = live_exposure(tmp_path)
    position = dict(instId='BTC-USDT-SWAP', posSide='long', pos='2',
                    mgnMode='cross', avgPx='100')
    opening = dict(instId='BTC-USDT-SWAP', tdMode='cross', posSide='long',
                   side='buy', sz='100', accFillSz='40')
    book._shared_leverage([position], [opening], [])
    with book.read_transaction() as (_, state):
        held = state['shared_quantities']['BTC-USDT-SWAP']
        assert D(held['long']) == D('.62')
        assert D(held['short']) == 0
        assert state['shared_quantity_errors'] == []


def test_close_only_algo_is_known_zero_but_malformed_open_order_is_unknown(tmp_path):
    book, _ = live_exposure(tmp_path)
    close_only = dict(instId='BTC-USDT-SWAP', tdMode='cross', posSide='long',
                      side='sell', reduceOnly='true', sz='100')
    book._shared_leverage([], [], [close_only])
    with book.read_transaction() as (_, state):
        assert state['shared_quantities']['BTC-USDT-SWAP'] == {'long': '0', 'short': '0'}
        assert state['shared_quantity_errors'] == []

    malformed_open = dict(instId='BTC-USDT-SWAP', tdMode='cross', posSide='long', side='buy')
    book._shared_leverage([], [malformed_open], [close_only])
    with book.read_transaction() as (_, state):
        assert state['shared_quantity_errors'] == ['BTC-USDT-SWAP']
    direction_unknown = dict(instId='BTC-USDT-SWAP', tdMode='cross', posSide='long',
                            sz='100', accFillSz='0')
    book._shared_leverage([], [direction_unknown], [close_only])
    with book.read_transaction() as (_, state):
        assert state['shared_quantity_errors'] == ['BTC-USDT-SWAP']
    with book.transaction() as (_, state):
        state['shared_leverage']['BTC-USDT-SWAP'] = 100
        state['shared_quantity_errors'] = ['BTC-USDT-SWAP']
    with book.read_transaction() as (_, state):
        with pytest.raises(Rejected, match='数量未核对'):
            book.preview_in(state, exposure_ticket('BTC', fraction='.1'))

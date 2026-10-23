"""Fixed display capital survives account changes without changing risk gates."""
import copy
from decimal import Decimal as D

import pytest

from test_live_book import live, Exchange
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.statistics_basis import set_fixed_basis


def test_owner_basis_changes_only_display_metadata_and_survives_restart(live):
    book, exchange, now = live
    with book.read_transaction() as (_, state):
        before = copy.deepcopy(state)
        reference = state['account_reference']
    basis = set_fixed_basis(book.path, '1000.123456789', reference, 'Synthetic owner-confirmed capital', now[0])
    with book.read_transaction() as (_, state):
        assert state == dict(before, version=before['version']+1, statistics_return_basis=basis)
    assert set_fixed_basis(book.path, '1000.123456789', reference, 'Repeated command') == basis
    with book.read_transaction() as (db, state):
        assert state['version'] == before['version']+1
        assert db.execute("SELECT count(*) FROM journal WHERE kind='statistics_basis_owner_confirmed'").fetchone()[0] == 1
    restarted = LiveBook(book.path, exchange, book.clock)
    assert restarted.statistics()['account']['initial_equity'] == '1000.123456789'
    assert D(restarted.statistics()['account']['return_fraction']) == 0
    assert 'statistics_return_basis' not in restarted.snapshot()
    assert exchange.writes == []


@pytest.mark.parametrize('days', [0, 7, 30])
def test_fixed_return_uses_full_pnl_and_ignores_transfers_and_daily_baseline(live, days):
    book, exchange, now = live
    with book.transaction() as (_, state):
        state['closed'] = [
            dict(id='old', symbol='BTC-USDT-SWAP', side='long',
                 pnl='-12.00', risk='5', closed=now[0]-40*86400)]
        state['positions'] = [
            dict(symbol='ETH-USDT-SWAP', entry='98', remaining='2', direction=1, pnl='-1.50')]
        state['account_equity'] = '10025'
        state['cash'] = '10000'
        state['daily']['start_equity'] = '25000'
        reference = state['account_reference']
    set_fixed_basis(book.path, '1000', reference, 'Synthetic capital', now[0])
    account = book.statistics(days)['account']
    assert D(account['net_pnl']) == D('-9.50')  # -12 - 1.5 + 4 floating.
    assert D(account['return_fraction']) == D('-.0095')
    assert account['return_method'] == 'app_managed_fixed_capital'
    # Next-day capital and whole-account changes do not rebase the percentage.
    with book.transaction() as (_, state):
        state['account_equity'] = '7'
        state['cash'] = '5'
        state['daily']['start_equity'] = '7'
    assert book.statistics(days)['account']['return_fraction'] == account['return_fraction']
    assert exchange.writes == []


def test_missing_or_mismatched_basis_does_not_leak_between_accounts(live, tmp_path):
    book, exchange, now = live
    with book.read_transaction() as (_, state):
        reference = state['account_reference']
    set_fixed_basis(book.path, '1000', reference, 'Synthetic primary capital', now[0])
    other = LiveBook(tmp_path/'other.sqlite3', Exchange(book.clock), book.clock)
    assert other.statistics()['account']['return_fraction'] is None
    with book.transaction() as (_, state):
        state['statistics_return_basis']['account_reference'] = 'different-account'
    assert book.statistics()['account']['return_fraction'] is None
    assert book.statistics()['account']['initial_equity'] is None


@pytest.mark.parametrize('amount', ['0', '-1', 'NaN', 'Infinity'])
def test_invalid_basis_is_rejected(live, amount):
    book, _, _ = live
    with book.read_transaction() as (_, state):
        before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        set_fixed_basis(book.path, amount, before['account_reference'], 'Synthetic')
    with book.read_transaction() as (_, state):
        assert state == before


def test_account_mismatch_and_basis_replacement_are_rejected(live):
    book, _, _ = live
    with book.read_transaction() as (_, state):
        reference = state['account_reference']
    with pytest.raises(ValueError, match='mismatch'):
        set_fixed_basis(book.path, '1000', 'wrong-account', 'Synthetic')
    set_fixed_basis(book.path, '1000', reference, 'Synthetic')
    with pytest.raises(ValueError, match='replaced'):
        set_fixed_basis(book.path, '2000', reference, 'Synthetic change')
    assert book.statistics()['account']['initial_equity'] == '1000'

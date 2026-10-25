"""Zero equity is readable but can never authorize a new entry."""
import pytest
from test_live_book import live, ticket
from yoyo.discipline_app.policy import Rejected


@pytest.mark.parametrize('omitted', [False, True])
def test_empty_balance_replaces_stale_value_and_preserves_entry_block(live, omitted):
    book, exchange, now = live
    before = book.snapshot()
    get = exchange.get
    exchange.equity = '0'
    if omitted:
        exchange.get = lambda path, **params: [dict(details=[])] if path.endswith('/balance') else get(path, **params)
    now[0] += 10
    assert book.reconcile()
    state = book.snapshot()
    assert state['equity'] == '0' and state['available'] == '0'
    assert state['private_at'] == now[0] and not state['private_error']
    assert state['daily'] == before['daily']
    assert '没有可用 USDT' in state['blockers'][0]
    assert book.statistics()['account']['equity'] == '0'
    with pytest.raises(Rejected, match='没有可用 USDT'):
        book.preview(ticket())
    assert exchange.writes == []


@pytest.mark.parametrize('response', [[], [{}], [dict(details=None)], [dict(details=[dict(ccy='USDT')])]])
def test_malformed_balance_does_not_become_a_zero_observation(live, response):
    book, exchange, now = live
    before = book.snapshot()
    get = exchange.get
    exchange.get = lambda path, **params: response if path.endswith('/balance') else get(path, **params)
    now[0] += 10
    assert not book.reconcile()
    state = book.snapshot()
    assert state['equity'] == before['equity'] and state['private_at'] == before['private_at']
    assert state['private_error'] and exchange.writes == []

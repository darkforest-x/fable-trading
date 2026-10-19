"""Mobile restart recovery must read the same close without exchange writes."""
import uuid
import pytest
from test_partial_close import live_book
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.okx_execution import ExchangeUncertain
from yoyo.discipline_app.policy import Rejected


def test_timeout_recovery_after_restart_is_read_only(tmp_path):
    book, exchange, position = live_book(tmp_path)
    key = uuid.uuid4().hex
    exchange.timeout_next_close = True
    with pytest.raises(ExchangeUncertain):
        book.close(key, position, '.25')
    writes = len(exchange.writes)
    restarted = LiveBook(book.path, exchange, book.clock)
    assert restarted.close_status(key)['status'] == 'filled'
    assert restarted.close_status(key)['position_id'] == position
    assert len(exchange.writes) == writes
    assert restarted.outbox.inspect(key)['phase'] == 'attempted'


def test_missing_close_is_fenced_against_delayed_post(tmp_path):
    book, exchange, position = live_book(tmp_path)
    key = uuid.uuid4().hex
    writes = len(exchange.writes)
    assert book.close_status(key)['status'] == 'not_found'
    assert book.abandon_unreceived_close(key)['status'] == 'not_received'
    with pytest.raises(Rejected, match='未送达'):
        book.close(key, position, '.25')
    assert len(exchange.writes) == writes


def test_fence_never_abandons_accepted_close(tmp_path):
    book, exchange, position = live_book(tmp_path)
    key = uuid.uuid4().hex
    book.close(key, position, '.25')
    writes = len(exchange.writes)
    assert book.abandon_unreceived_close(key)['status'] == 'filled'
    assert len(exchange.writes) == writes
    assert 'close_fractions' not in book.snapshot()
    assert 'close_abandoned' not in book.snapshot()


def test_status_does_not_mistake_acknowledgement_for_fill(tmp_path):
    book, exchange, position = live_book(tmp_path)
    key = uuid.uuid4().hex
    book.close(key, position, '.25')
    row = exchange.orders[book.outbox.inspect(key)['payload']['clOrdId']]
    row['state'] = 'live'
    writes = len(exchange.writes)
    assert book.close_status(key)['status'] == 'checking'
    row['state'] = 'canceled'
    result = book.close_status(key)
    assert result['status'] == 'cancelled' and result['partially_filled']
    assert len(exchange.writes) == writes


def test_prepared_close_reports_manual_retry_without_dispatch(tmp_path, monkeypatch):
    book, exchange, position = live_book(tmp_path)
    key = uuid.uuid4().hex
    monkeypatch.setattr(book.outbox, 'dispatch', lambda *_: (_ for _ in ()).throw(RuntimeError('process interrupted')))
    with pytest.raises(RuntimeError):
        book.close(key, position, '.25')
    writes = len(exchange.writes)
    result = book.close_status(key)
    assert result == dict(id=key, position_id=position, status='ready')
    assert len(exchange.writes) == writes

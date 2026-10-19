"""Cache must observe external commits without freezing time-based projections."""
from yoyo.monitor.signal_cache import SignalReadCache
from yoyo.monitor.signal_analytics import ledger
from yoyo.monitor.store import Store
from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL, BARK_ARM_KEY

NOW, HOUR = 1_789_272_000_000, 3_600_000


def event(n, *, close=None, status='active', r=1):
    close = NOW - HOUR if close is None else close
    return dict(protocol=SIGNAL_PROTOCOL, kind=SIGNAL_KIND, confirmation='raw',
                source='live', side='long', direction='long', symbol=f'C{n}-USDT-SWAP',
                timeframe='1H', timeframe_min=60, bar_close_ms=close,
                bar_open_ms=close-HOUR, price=100, risk=10, initial_stop=90,
                is_closed=True, performance=dict(status=status, current_r=r,
                    exit_r=None if status == 'active' else r))


def setup(tmp_path):
    store = Store(tmp_path / 'monitor.sqlite3')
    store.set_meta(BARK_ARM_KEY, {'activated_ms': NOW - 60*24*HOUR})
    store.set_meta('display_policy:spike-v1:15m', {'activated_ms': NOW - 60*24*HOUR})
    store.set_meta('display_policy:spike-v1-short', {'activated_ms': NOW - 60*24*HOUR})
    return store


def test_cache_hits_and_external_commits_invalidate(tmp_path, monkeypatch):
    store = setup(tmp_path)
    row = event(1, status='active', r=2)
    store.upsert_event(row)
    cache = SignalReadCache(store)
    calls = []
    original = store.list_events
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'list_events', counted)
    try:
        first = ledger(cache, now=NOW)
        first['items'][0]['performance']['current_r'] = 999
        assert ledger(cache, now=NOW)['stats']['floating_r'] == 2
        assert len(calls) == 1
        # Independent connection, as in the isolated scanner / notification process.
        writer = Store(store.path)
        writer.update_event_payload(store.event_id(row), {'performance': {'status': 'loss', 'exit_r': -1}})
        assert ledger(cache, now=NOW)['stats']['realized_r'] == -1
        writer.upsert_event(event(2))
        assert ledger(cache, now=NOW)['total'] == 2
        with writer.connect() as db:
            db.execute('INSERT OR REPLACE INTO outbox VALUES (?,?,?,?,?,?,?)',
                       (store.event_id(row), 'sent', 1, NOW, NOW, 123, None))
        assert next(x for x in ledger(cache, now=NOW)['items'] if x['id'] == store.event_id(row))['notification_status'] == 'sent'
        assert len(calls) == 4
    finally:
        cache.close()


def test_cached_rows_still_recompute_future_freshness_and_calendar(tmp_path):
    store = setup(tmp_path)
    store.upsert_event(event(1, close=NOW + 1))
    cache = SignalReadCache(store)
    try:
        assert ledger(cache, now=NOW)['total'] == 0
        assert ledger(cache, now=NOW+1)['items'][0]['is_fresh']
        assert not ledger(cache, now=NOW+HOUR)['items'][0]['is_fresh']
        for kwargs in ({}, {'period':'today'}, {'period':'week'}, {'outcome':'closed'}, {'side':'short'}, {'sort':'r_desc','limit':1}):
            assert ledger(cache, now=NOW+HOUR, **kwargs) == ledger(store, now=NOW+HOUR, **kwargs)
    finally:
        cache.close()


def test_cache_is_bounded_and_oversized_cohort_bypasses(tmp_path):
    store = setup(tmp_path)
    store.upsert_event(event(1))
    cache = SignalReadCache(store, max_entries=1, max_bytes=1)
    try:
        assert ledger(cache, now=NOW)['total'] == 1
        assert not cache._rows
        cache.max_bytes = 1_000_000
        ledger(cache, now=NOW)
        ledger(cache, now=NOW, source='replay')
        assert len(cache._rows) == 1
        assert cache._bytes <= cache.max_bytes
    finally:
        cache.close()

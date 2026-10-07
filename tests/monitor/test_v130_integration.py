"""V13 scanner adapter, durable replay and read API without external sends."""
from types import SimpleNamespace

import pandas as pd
from fastapi.testclient import TestClient

from test_v130_signals import fixture
from yoyo.monitor.notification_center import NotificationCenter
from yoyo.monitor.server import create_app
from yoyo.monitor.store import Store
from yoyo.monitor.v130_policy import arm_v130, SIGNAL_KIND, SIGNAL_PROTOCOL
from yoyo.monitor.v130_worker import observe


def inputs(extra=()):
    built, side, anchors = fixture(extra)
    parent = dict(events=anchors, chart=[], state=dict(phase='ready', ready=True))
    context = dict(built=built, evidence=SimpleNamespace(side=pd.Series(side)))
    return parent, context


def test_adapter_persists_final_close_then_restart_only_updates_performance(tmp_path):
    parent, context = inputs()
    final = int(context['built'].index[-1].value // 1_000_000) + 900_000
    store = Store(tmp_path / 'monitor.sqlite3')
    NotificationCenter(tmp_path)
    arm_v130(store, final - 1)
    observe(store, parent, context, symbol='ETH-USDT-SWAP', tick=.1, clock=lambda: final+1)
    first = store.list_events(kind=SIGNAL_KIND, protocol=SIGNAL_PROTOCOL, summary=False)[0]
    assert first['performance']['reason'] == 'awaiting_next_closed_bar'
    assert store.notification_status('telegram')['pending'] == 1
    assert store.notification_status('bark')['pending'] == 1
    parent, context = inputs([(11.2, 11.4, 7.9, 9.)])
    restarted = Store(store.path)
    observe(restarted, parent, context, symbol='ETH-USDT-SWAP', tick=.1, clock=lambda: final+900_001)
    assert restarted.event_count(kind=SIGNAL_KIND, protocol=SIGNAL_PROTOCOL) == 1
    updated = restarted.list_events(kind=SIGNAL_KIND, protocol=SIGNAL_PROTOCOL, summary=False)[0]
    assert updated['performance']['exit_price'] == 8.
    assert updated['detected_at_ms'] == first['detected_at_ms']
    assert restarted.notification_status('telegram')['pending'] == 1


def test_cold_start_never_backfills_recent_confirmation(tmp_path):
    parent, context = inputs()
    final = int(context['built'].index[-1].value // 1_000_000) + 900_000
    store = Store(tmp_path / 'monitor.sqlite3')
    result = observe(store, parent, context, symbol='ETH-USDT-SWAP', tick=.1, clock=lambda: final+1)
    assert result['events'] == []
    assert store.event_count() == 0


def test_api_exposes_v13_periods_and_same_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr('yoyo.monitor.telegram.credentials', lambda: None)
    monkeypatch.setattr('yoyo.monitor.bark.credentials', lambda *a: None)
    app = create_app(tmp_path, start_monitor=False)
    parent, context = inputs()
    final = int(context['built'].index[-1].value // 1_000_000) + 900_000
    app.state.monitor.client.clock = lambda: final+1
    store = app.state.monitor.store
    arm_v130(store, final-1)
    observe(store, parent, context, symbol='ETH-USDT-SWAP', tick=.1, clock=lambda: final+1)
    with TestClient(app) as client:
        payload = client.get('/api/v13/signals').json()
        assert payload['timeframes'] == ['15m', '30m', '1H', '4H'] and payload['signals_24h'] == 1
        assert not payload['orders_enabled'] and not payload['chart_parity_verified']
        assert len(payload['items']) == 1 and payload['items'][0]['is_fresh']
        assert payload['items'][0]['notification_status'] == 'pending'
        assert 'V13 回踩' in client.get('/').text
        assert client.get('/api/v13/signals?symbol=BTC-USDT-SWAP').json()['items'] == []
        assert client.get('/api/notifications/events?topic=spike_v130').json()['total'] == 2


def test_service_delivers_independent_v13_arm_without_legacy_arm(tmp_path, monkeypatch):
    from yoyo.monitor.service import Monitor
    monkeypatch.setattr('yoyo.monitor.telegram.credentials', lambda: None)
    monkeypatch.setattr('yoyo.monitor.bark.credentials', lambda *a: None)
    store = Store(tmp_path / 'monitor.sqlite3')
    arm_v130(store, 1)
    monitor = Monitor(store)
    calls = []

    class SingleIteration:
        stopped = False
        def is_set(self): return self.stopped
        def wait(self, seconds): self.stopped = True

    monitor.stop_event = SingleIteration()
    monitor.telegram = SimpleNamespace(deliver_once=lambda now: calls.append(now) or True)
    monitor._deliver_channel('telegram')
    assert len(calls) == 1


def test_signal_center_ledger_reads_the_v131_cohort_with_its_own_projection(tmp_path):
    import pytest
    from yoyo.monitor.signal_analytics import ledger
    parent, context = inputs([(11.2, 11.4, 7.9, 9.)])
    final = int(context['built'].index[3].value // 1_000_000) + 900_000
    store = Store(tmp_path / 'monitor.sqlite3')
    with pytest.raises(RuntimeError, match='V13.1'):
        ledger(store, now=final + 1, confirmation='retest')
    with pytest.raises(ValueError):
        ledger(store, now=final + 1, source='replay', confirmation='retest')
    store.set_meta('migration:spike-v9-reset-v1', {'activated_ms': 0})
    arm_v130(store, final - 1)
    observe(store, parent, context, symbol='ETH-USDT-SWAP', tick=.1, clock=lambda: final + 900_001)
    data = ledger(store, now=final + 900_001, confirmation='retest')
    assert data['total'] == 1 and data['stats']['loss'] == 1
    item = data['items'][0]
    assert item['strategy_version'] == 'spike-v13.1-retest-monitor-20261007-v1'
    assert item['performance']['exit_reason'] == 'initial_stop' and item['trail_atr'] == 4.
    assert [row['total'] for row in data['by_timeframe']] == [1, 0, 0, 0]
    assert ledger(store, now=final + 900_001, confirmation='retest', timeframe='1H')['total'] == 0

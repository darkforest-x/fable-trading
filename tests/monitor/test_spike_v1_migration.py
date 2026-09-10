import pandas as pd
from yoyo.monitor import SIGNAL_PROTOCOL, SIGNAL_KIND, TIMEFRAMES
from yoyo.monitor.notification_policy import delivery_error
from yoyo.monitor.signals import analyze
from yoyo.monitor.store import Store


def bars(n=360, step=TIMEFRAMES['30m']):
    return [dict(t=i*step,o=100.,h=101.,l=99.,c=100.,v=1.) for i in range(n)]


def test_v1_adapter_is_long_only_and_closed_bar_only():
    result = analyze(bars(), [], '30m', tick=.01)
    assert result['protocol']['direction'] == 'long_only'
    assert all(e['direction'] == 'long' and e['is_closed'] for e in result['events'])


def test_v1_migration_deletes_only_obsolete_monitor_journal(tmp_path):
    store = Store(tmp_path / 'm.sqlite')
    old = dict(protocol='imacd-tv-visible-start-monitor-v3',symbol='BTC-USDT-SWAP',timeframe='1H',kind='tv_start',side='long',bar_close_ms=1,detected_at_ms=1)
    other = dict(protocol='other',symbol='BTC-USDT-SWAP',timeframe='1H',kind='x',side='long',bar_close_ms=2,detected_at_ms=2)
    store.upsert_event(old, bark_notify=True); store.upsert_event(other)
    receipt = store.migrate_v1_protocol(3)
    assert receipt['obsolete_event_rows'] == 1
    assert store.event_count() == 1 and store.list_events()[0]['protocol'] == 'other'

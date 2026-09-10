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


def test_v1_migration_removes_the_two_remaining_imacd_protocols_without_erasing_v1_cutover(tmp_path):
    store = Store(tmp_path / 'm.sqlite')
    def event(protocol, close):
        return dict(protocol=protocol, symbol='BTC-USDT-SWAP', timeframe='1H', kind='entry', side='long',
                    bar_close_ms=close, detected_at_ms=close)
    store.upsert_event(event('imacd-zero-axis-monitor-v2', 1), bark_notify=True)
    store.upsert_event(event('imacd-pine-v2.2-default-monitor-v1', 2), notify=True)
    v1 = dict(protocol=SIGNAL_PROTOCOL, kind=SIGNAL_KIND, source='live', confirmation='raw', direction='long',
              symbol='BTC-USDT-SWAP', timeframe='1H', timeframe_min=60, side='long', bar_open_ms=2,
              bar_close_ms=3, signal_close_time=3, is_closed=True, price=100., risk=1., initial_stop=99.,
              source_sha256='a' * 64, entry_reference='next_open', executable_entry_time=None, detected_at_ms=3)
    store.upsert_event(v1)
    store.set_meta('notification_policy:v1_bark_arm', {'activated_ms': 3})
    receipt = store.migrate_v1_protocol(4)
    assert receipt['obsolete_event_rows'] == 2
    assert store.list_events() == [dict(v1, id=store.event_id(v1), notification_status='history', bark_notification_status='history')]
    assert store.get_meta('notification_policy:v1_bark_arm')['activated_ms'] == 3

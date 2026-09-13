"""Full-cohort signal statistics: synthetic observations, no exchange access."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL, MODEL_KIND, MODEL_PROTOCOL, SHORT_SIGNAL_PROTOCOL
from yoyo.monitor.signal_analytics import ledger, summarize, period_start
from yoyo.monitor.store import Store

TZ = ZoneInfo('Asia/Shanghai')
NOW = int(datetime(2026, 9, 13, 12, tzinfo=TZ).timestamp() * 1000)
HOUR = 3_600_000


def event(n, *, close=None, side='long', status='active', r=1.0, source='live', tf='1H'):
    close = NOW - HOUR if close is None else close
    p = dict(status=status, current_r=r, exit_r=None if status == 'active' else r, peak_r=99)
    return dict(protocol=SHORT_SIGNAL_PROTOCOL if side == 'short' else SIGNAL_PROTOCOL,
                kind=SIGNAL_KIND, confirmation='raw', source=source, side=side, direction=side,
                symbol=f'C{n}-USDT-SWAP', timeframe=tf, timeframe_min={'1H':60,'15m':15,'30m':30,'4H':240}[tf],
                bar_close_ms=close, bar_open_ms=close-HOUR, price=100, risk=10, initial_stop=90,
                is_closed=True, performance=p)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'monitor.sqlite3')
    s.set_meta('notification_policy:v1_bark_arm', {'activated_ms':NOW - 60*24*HOUR})
    s.set_meta('display_policy:spike-v1:15m', {'activated_ms':NOW - 60*24*HOUR})
    s.set_meta('display_policy:spike-v1-short', {'activated_ms':NOW - 60*24*HOUR})
    return s


def test_complete_stats_and_r_sort_precede_pagination(store):
    # Insert the maximum-R item at the oldest end of a >2,000-row collection.
    for i in range(2003):
        store.upsert_event(event(i, close=NOW-(i+1)*1000, status='profit', r=i))
    result = ledger(store, now=NOW, sort='r_desc', limit=2)
    assert result['total'] == 2003
    assert result['stats']['realized_r'] == sum(range(2003))
    assert [r['performance']['exit_r'] for r in result['items']] == [2002,2001]
    next_page = ledger(store, now=NOW, sort='r_desc', offset=2, limit=2)
    assert [r['performance']['exit_r'] for r in next_page['items']] == [2000,1999]
    assert set(r['id'] for r in next_page['items']).isdisjoint(r['id'] for r in result['items'])
    assert store.bark_status()['pending'] == 0


def test_period_boundaries_use_original_beijing_close_and_monday(store):
    today = period_start(NOW,'today'); week = period_start(NOW,'week')
    assert datetime.fromtimestamp(week/1000,TZ).isoformat() == '2026-09-07T00:00:00+08:00'
    for i,t in enumerate([today-1,today,week-1,week,NOW+1]): store.upsert_event(event(i,close=t))
    assert ledger(store,now=NOW,period='today')['total'] == 1
    assert ledger(store,now=NOW,period='week')['total'] == 3
    assert ledger(store,now=NOW)['total'] == 4


def test_yolo_dedup_uses_latest_raw_performance_and_original_day(store):
    raw = event(1, close=period_start(NOW,'today')-HOUR, status='active', r=2)
    store.upsert_event(raw)
    original = store.get_event(store.event_id(raw))
    yolo = dict(raw, protocol=MODEL_PROTOCOL,kind=MODEL_KIND,confirmation='yolo',
                source_event_id=original['id'],indicator=original,bar_close_ms=NOW-HOUR)
    store.upsert_event(yolo)
    store.upsert_event(dict(yolo,bar_close_ms=NOW-HOUR+1))
    store.update_event_payload(original['id'], {'performance':dict(status='profit',exit_r=3,current_r=3,peak_r=100)})
    assert ledger(store,now=NOW,confirmation='yolo',period='today')['total'] == 0
    result = ledger(store,now=NOW,confirmation='yolo')
    assert result['total'] == 1
    assert result['stats']['realized_r'] == 3
    assert result['stats']['floating_r'] is None
    assert result['items'][0]['performance']['exit_r'] == 3
    assert ledger(store,now=NOW)['total'] == 1


def test_source_side_status_timeframe_search_and_missing_values(store):
    for row in [event(1,side='short',status='profit',r=3,tf='15m'),
                event(2,status='loss',r=-1,tf='30m'),event(3,status='active',r=-.4),
                event(4,status='breakeven',r=0,tf='4H'),event(5,status='profit',r=None),
                event(6,status='unknown',r=None),event(7,source='replay',r=90)]:
        store.upsert_event(row)
    result = ledger(store,now=NOW)
    assert result['total'] == 6
    assert result['stats']['realized_r'] == 2
    assert result['stats']['floating_r'] == -.4
    assert result['stats']['win_rate'] == pytest.approx(1/3)
    assert result['stats']['missing_r'] == 2
    assert sum(r['total'] for r in result['by_timeframe']) == 6
    assert ledger(store,now=NOW,side='short',timeframe='15m',search='C1')['total'] == 1
    assert ledger(store,now=NOW,outcome='loss')['total'] == 1
    assert ledger(store,now=NOW,source='replay')['total'] == 1
    for sort in ['r_asc','r_desc']:
        ordered=ledger(store,now=NOW,sort=sort)['items']
        assert ordered[-1]['sort_r'] is None and ordered[-2]['sort_r'] is None
    assert summarize([{'performance':{'status':'profit','current_r':99,'exit_r':float('nan')}}])['realized_r'] is None
    assert summarize([{'performance':{'status':'active','current_r':True}}])['floating_r'] is None


def test_new_short_cutover_does_not_reclassify_as_fresh_live(store):
    store.set_meta('display_policy:spike-v1-short',{'activated_ms':NOW-HOUR})
    store.upsert_event(event(1,side='short',close=NOW-HOUR))
    store.upsert_event(event(2,side='short',close=NOW-HOUR+1))
    assert ledger(store,now=NOW,side='short')['total']==1
    warm=ledger(store,now=NOW,side='short',source='warmup')
    assert warm['total']==1 and warm['items'][0]['is_fresh'] is False


def test_api_validates_query_and_gateway_allows_only_read(tmp_path):
    from fastapi.testclient import TestClient
    from yoyo.monitor.server import create_app
    from yoyo.monitor.desktop_client import _read_path
    app = create_app(runtime=tmp_path,start_monitor=False)
    client=TestClient(app)
    assert client.get('/api/signal-ledger?source=replay&period=wrong').status_code==400
    assert client.get('/api/signal-ledger?source=replay&limit=2001').status_code==422
    response=client.get('/api/signal-ledger?source=replay')
    assert response.status_code==200 and response.json()['total']==0
    assert _read_path('/api/signal-ledger?period=today')=='/api/signal-ledger'
    assert _read_path('/api/signal-ledger/../secrets') is None


def test_orphan_yolo_does_not_use_stale_embedded_r(store):
    original=event(100,status='profit',r=30)
    proof=dict(original,protocol=MODEL_PROTOCOL,kind=MODEL_KIND,confirmation='yolo',
               source_event_id=store.event_id(original),indicator=original,bar_close_ms=NOW)
    store.upsert_event(proof)
    result=ledger(store,now=NOW,confirmation='yolo')
    assert result['total']==1 and result['stats']['unknown']==1
    assert result['stats']['realized_r'] is None


def test_combined_legacy_copy_is_not_second_signal_or_current_yolo_proof(store):
    raw=event(1,status='profit',r=4)
    store.upsert_event(raw)
    store.upsert_event(dict(raw,confirmation='raw_yolo'))
    assert ledger(store,now=NOW)['total']==1
    assert ledger(store,now=NOW)['stats']['realized_r']==4
    assert ledger(store,now=NOW,confirmation='yolo')['total']==0


def test_sort_time_and_stats_time_both_use_original_signal_and_conflicting_r_is_unknown(store):
    for i,origin,confirm in [(1,NOW-3*HOUR,NOW),(2,NOW-2*HOUR,NOW-HOUR)]:
        raw=event(i,close=origin)
        store.upsert_event(raw)
        store.upsert_event(dict(raw,protocol=MODEL_PROTOCOL,kind=MODEL_KIND,confirmation='yolo',
                                indicator=raw,source_event_id=store.event_id(raw),bar_close_ms=confirm))
    result=ledger(store,now=NOW,confirmation='yolo',sort='newest')
    assert [r['symbol'] for r in result['items']]==['C2-USDT-SWAP','C1-USDT-SWAP']
    assert summarize([{'performance':{'status':'profit','exit_r':-1}},
                      {'performance':{'status':'breakeven','exit_r':.2}}])['unknown']==2


def test_ledger_alias_keeps_existing_windows_gateway_compatible(tmp_path):
    from fastapi.testclient import TestClient
    from yoyo.monitor.server import create_app
    from yoyo.monitor.desktop_client import _read_path
    client=TestClient(create_app(runtime=tmp_path,start_monitor=False))
    path='/api/signals?view=ledger&source=replay&period=today&sort=r_desc&limit=24'
    result=client.get(path)
    assert result.status_code==200 and result.json()['stats']['total']==0
    assert _read_path(path)=='/api/signals'
    assert client.get(path+'&offset=-1').status_code==400

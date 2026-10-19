"""SPIKE exclusions cannot reach exchange writes or affect manual MA entries."""
import pytest

from yoyo.okx_execution.manual import SOURCE
from yoyo.okx_execution.manual_engine import ManualEngine
from yoyo.okx_execution.preparation import RetryableRead
from yoyo.okx_execution.source import Source
from test_engine import setup, trade
from test_execution_source import event, populate


@pytest.mark.parametrize('tf,side,base', [('15m','long','AXL'),('15m','short','DOGE'),
                                       ('1H','long','ACH'),('1H','short','ARC')])
def test_preparation_submission_and_durable_retry_cannot_send_blocked_entry(tmp_path,tf,side,base):
    t=trade(timeframe=tf,side=side)
    t['symbol']=t['event']['symbol']=base+'-USDT-SWAP'
    t,exchange,store,engine=setup(tmp_path,t)
    with pytest.raises(ValueError,match='excluded'):
        engine.prepare(t['event'],{})
    engine.enter(t)
    assert store.trades()[0]['state']=='skipped' and exchange.posts==[]
    engine.preparation_failed(t['event'],RetryableRead('old temporary read','quote_stale'))
    engine.tick()
    assert store.trades()[0]['state']=='skipped' and exchange.posts==[]


def test_source_filters_scope_and_keeps_immutable_history(tmp_path):
    rows=[]
    for identity,tf,base,side in [('blocked','15m','DOGE','short'),('opposite','15m','DOGE','long'),
                                ('other_tf','1H','DOGE','short'),('hourly','1H','ACH','long')]:
        r=event(tf,base+'-USDT-SWAP')
        r.update(id=identity,side=side,direction=side,initial_stop=98. if side=='long' else 102.)
        rows.append(r)
    populate(tmp_path,rows)
    source=Source(tmp_path)
    assert {r['id'] for r in source.events(0,100_000_000)}=={'opposite','other_tf'}
    import sqlite3
    with sqlite3.connect(tmp_path/'monitor.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0]==4


def test_existing_filtered_position_still_managed_and_manual_entry_scope_is_unchanged(tmp_path,monkeypatch):
    t=trade();t.update(symbol='AXL-USDT-SWAP',state='active');t['event']['symbol']=t['symbol']
    t,exchange,store,engine=setup(tmp_path,t)
    store.save(t)
    managed=[]
    monkeypatch.setattr(engine,'manage',lambda row:managed.append(row['id']))
    engine.tick(entries=False)
    assert managed==[t['id']] and exchange.posts==[]
    manual_engine=ManualEngine(engine.config,exchange,engine.source,store)
    event=dict(t['event'],source=SOURCE)
    assert manual_engine.entry_exclusion(event) is None
    assert manual_engine.entry_exclusion(dict(t,event=event)) is None
    assert manual_engine.entry_exclusion(t['event'])=='owner_excluded_timeframe_side'

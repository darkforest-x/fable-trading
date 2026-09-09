"""Display eligibility is independent of Bark permission, for both stages.

Synthetic bars and temporary journals only; no model weights or real senders.
"""
from types import SimpleNamespace

import pytest

from model_fixture import model_event
from yoyo.monitor import (BARK_TIMEFRAMES, DIRECT_POLICY, DIRECT_TIMEFRAMES,
                          HIGHER_TIMEFRAME, MODEL_KIND, MODEL_PROTOCOL, MONITORED_TIMEFRAMES,
                          SIGNAL_KIND, TIMEFRAMES, TV_INTERVALS)
from yoyo.monitor.bark import BarkWorker
from yoyo.monitor.model_gate import pending_proof
from yoyo.monitor.notification_policy import delivery_error
from yoyo.monitor.service import Monitor
from yoyo.monitor.store import Store


def activate(store):
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        store.activate_bark_policy(0, protocol=protocol, retire_obsolete=False)
        for tf in MONITORED_TIMEFRAMES:
            store.activate_timeframe_policy(tf, 0, protocol=protocol)


def test_six_display_periods_and_three_bark_periods_are_separate():
    assert MONITORED_TIMEFRAMES == DIRECT_TIMEFRAMES == ('5m','15m','30m','1H','4H','1Dutc')
    assert BARK_TIMEFRAMES == ('1H','4H','1Dutc')
    assert TIMEFRAMES['5m'] == 300_000 and HIGHER_TIMEFRAME['5m'] == '1H'
    assert TV_INTERVALS['5m'] == '5'


@pytest.mark.parametrize('tf', ['5m','15m','30m'])
@pytest.mark.parametrize('side', ['long','short'])
def test_both_stages_remain_visible_without_enqueuing_bark(tmp_path, monkeypatch, tf, side):
    store=Store(tmp_path/'test.db');activate(store)
    event=model_event(timeframe=tf, side=side, wait=0)
    raw=event['indicator'];now=event['bar_close_ms']+1000
    # Exercise the production scan entry and model commit paths separately.
    monitor=Monitor(store, client=SimpleNamespace(clock=lambda:now))
    monitor.bark.creds='synthetic'
    assert monitor.record_arrow(raw, [], now)
    assert monitor.model_gate.register(raw)
    saved=store.list_candidates()[0]
    monitor.model_gate._commit(saved,event,[])
    assert len(store.list_events(kind=MODEL_KIND)) == 1
    assert len(store.list_events(kind=SIGNAL_KIND,direct_only=True,timeframe=tf)) == 1
    assert store.list_candidates()[0]['model']['status'] == 'confirmed'
    assert store.bark_status()['pending'] == store.telegram_status()['pending'] == 0
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM bark_outbox').fetchone()[0] == 0
    assert not monitor.record_arrow(raw, [], now+1)
    monitor.model_gate._commit(saved,event,[])
    assert len(store.list_events()) == 2
    assert store.bark_status()['pending'] == 0


@pytest.mark.parametrize('tf', ['5m','15m','30m'])
@pytest.mark.parametrize('stage', ['raw','model'])
def test_sender_rejects_old_or_bypass_queue_after_muting(tmp_path, tf, stage):
    store=Store(tmp_path/'test.db');activate(store)
    event=model_event(timeframe=tf,wait=0)
    event=event['indicator'] if stage=='raw' else event
    store.upsert_event(event,bark_notify=True)
    worker=BarkWorker(store,'synthetic-device',lambda *a,**k:pytest.fail('muted Bark reached network'))
    assert worker.deliver_once(event['bar_close_ms']+1000)
    with store.connect() as db:
        row=db.execute('SELECT status,error FROM bark_outbox').fetchone()
    assert tuple(row)==('skipped','bark_timeframe_muted_by_owner')
    assert not worker.deliver_once(event['bar_close_ms']+1001)


@pytest.mark.parametrize('tf', BARK_TIMEFRAMES)
def test_three_bark_periods_keep_both_delivery_stages(tmp_path, tf):
    store=Store(tmp_path/'test.db');activate(store)
    event=model_event(timeframe=tf,wait=0)
    calls=[]
    def send(*args,**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200,json=lambda:{'code':200,'timestamp':1})
    worker=BarkWorker(store,'synthetic-device',send)
    for e in (event['indicator'],event):
        assert delivery_error(store,e,e['bar_close_ms']+1000,'bark') is None
        store.upsert_event(e,bark_notify=True)
        assert worker.deliver_once(e['bar_close_ms']+1000)
    assert len(calls)==2 and store.bark_status()['sent']==2


def test_mute_migration_preserves_display_model_work_cutovers_and_terminal_receipts(tmp_path):
    store=Store(tmp_path/'test.db');activate(store)
    pending=[]
    for tf in ('5m','15m','30m','1H'):
        for index,status in enumerate(('pending','sent','unknown','failed','skipped','sending')):
            event=model_event(timeframe=tf,wait=0,original_changes={'symbol':f'T{index}-USDT-SWAP'})
            for e in (event['indicator'],event):
                store.upsert_event(e,bark_notify=True)
                if status!='pending':store.finish_bark(store.event_id(e),status,server_timestamp=1)
                elif tf in ('5m','15m','30m'):pending.append(store.event_id(e))
            store.register_candidate(event['indicator'],pending_proof(event['indicator']))
    # Retain previously withdrawn 5m terminal proofs; muting must not revive them.
    old=model_event(timeframe='5m',wait=0,original_changes={'symbol':'OLD-USDT-SWAP'})['indicator']
    store.register_candidate(old,pending_proof(old))
    store.update_candidate(store.event_id(old),dict(pending_proof(old),status='disabled'))
    store.recover_outbox()
    def snapshot():
        with store.connect() as db:
            return {table:[tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY 1')]
                    for table in ('events','meta','model_candidates','outbox','bark_outbox')}
    before=snapshot()
    assert store.retire_muted_bark_timeframes()==6
    after=snapshot()
    assert store.retire_muted_bark_timeframes()==0
    assert snapshot()==after
    for table in ('events','meta','model_candidates','outbox'):assert before[table]==after[table]
    oldrows={r[0]:r for r in before['bark_outbox']}
    for row in after['bark_outbox']:
        if row[0] in pending:
            assert row[1]=='skipped' and row[5]=='bark_timeframe_muted_by_owner'
        else:assert row==oldrows[row[0]]
    assert len(store.list_candidates(timeframe='15m',pending_only=True))==6
    assert next(e for e in store.list_candidates() if e['symbol']=='OLD-USDT-SWAP')['model']['status']=='disabled'

"""Calendar sorting, loss-streak cooldowns and user-triggered stop controls."""
import uuid
from decimal import Decimal as D
from dataclasses import replace
import pytest
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.policy import Policy, Rejected
from yoyo.discipline_app.market import today_change
from test_discipline_service import Clock, market, ticket
from test_live_book import live, enter, quote


def test_three_losses_start_cooldown_and_keep_it_over_midnight(tmp_path):
    b=Book(tmp_path/'new.sqlite3', Clock(), initial_equity='1000');b.configure(Policy());market(b)
    for i in range(3):
        request=ticket(fraction='.02',leverage=10,sizing_mode='margin')
        result=b.enter(uuid.uuid4().hex,request,1)
        b.close(uuid.uuid4().hex,result['id'])
        s=b.snapshot()
        assert s['loss_streak']==i+1
        assert s['discipline']['entries_remaining']==2-i
        assert s['discipline']['losses_remaining']==2-i
        assert (s['cooldown_until']>b.clock())==(i==2)
    until=s['cooldown_until'];b.clock.advance(3600);market(b)
    assert Book(b.path,b.clock).snapshot()['cooldown_until']==until


def test_winner_resets_streak_and_migration_preserves_valid_cooldown(tmp_path):
    b=Book(tmp_path/'old.sqlite3', Clock());b.configure(Policy(cooldown_after_losses=1));market(b)
    with b.transaction() as (_,s):
        s['loss_streak']=1;s['cooldown_until']=b.clock()+86400
        s['closed']=[dict(pnl='-1',closed=b.clock())]
    b.activate_streak_cooldown();s=b.snapshot()
    assert s['cooldown_until']==0 and s['loss_streak']==1
    with b.transaction() as (_,s):
        b.settle_discipline(s,'1',b.clock());assert s['loss_streak']==0
        s['policy']['cooldown_after_losses']=1
        s['closed']=[dict(pnl=p,closed=b.clock()+i) for i,p in enumerate(['-1','-1','-1','1'])]
        s['cooldown_until']=b.clock()+86402
    b.activate_streak_cooldown();assert b.snapshot()['cooldown_until']==b.clock()+86402


@pytest.mark.parametrize('side',['long','short'])
def test_auto_stop_is_cash_r_with_rounding_and_no_order(tmp_path,side):
    b=Book(tmp_path/'auto.sqlite3',Clock(),initial_equity='1000');b.configure(Policy());market(b)
    r=b.auto_stop(ticket(side=side,stop='',fraction='.03',leverage=100,sizing_mode='margin'))
    assert D(r['plan']['risk'])<=50 and D(r['plan']['risk'])>49
    assert (D(r['plan']['entry'])-D(r['stop']))*(1 if side=='long' else -1)>0
    assert b.snapshot()['daily']['entries']==0 and not b.snapshot()['positions']
    with pytest.raises(Rejected):b.auto_stop(ticket(side=side,stop='',fraction='.11',leverage=100,sizing_mode='margin'))
    b.clock.advance(16)
    with pytest.raises(Rejected,match='过期'):b.auto_stop(ticket(side=side,stop='',fraction='.03',leverage=100,sizing_mode='margin'))


@pytest.mark.parametrize('side,mark',[('long','101'),('short','99')])
def test_entry_stop_keeps_tp_and_is_idempotent(live,side,mark):
    from test_live_book import ticket as lt
    b,e,_=live;enter(b,lt(side=side,stop='95' if side=='long' else '105'))
    pos=b.snapshot()['positions'][0]
    with pytest.raises(Rejected,match='尚未越过'):b.entry_stop(uuid.uuid4().hex,pos['id'])
    quote(b,mark)
    tps=[dict(a) for a in e.algos.values() if 'tpTriggerPx' in a]
    b.entry_stop(uuid.uuid4().hex,pos['id'])
    assert b.snapshot()['positions'][0]['stop']=='100'
    assert tps==[a for a in e.algos.values() if 'tpTriggerPx' in a]
    count=len(e.writes);b.entry_stop(uuid.uuid4().hex,pos['id']);assert len(e.writes)==count
    quote(b,'107' if side=='long' else '93');b.reconcile()
    better=b.snapshot()['positions'][0]['stop'];b.entry_stop(uuid.uuid4().hex,pos['id'])
    assert b.snapshot()['positions'][0]['stop']==better


def test_calendar_change_is_not_rolling_24h():
    assert D(today_change(dict(last='110',sodUtc8='100',open24h='200')))==10
    assert today_change(dict(last='110',sodUtc8='0',open24h='100')) is None
    assert today_change(dict(last='110')) is None


def test_entry_stop_timeout_recovers_from_exchange_without_resubmission(live):
    from yoyo.discipline_app.okx_execution import ExchangeUncertain
    b,e,_=live;enter(b);quote(b,'101');pos=b.snapshot()['positions'][0]
    original=e.post
    def timeout(path,body):
        result=original(path,body)
        if path.endswith('/amend-algos') and 'newSlTriggerPx' in body:
            raise ExchangeUncertain('synthetic timeout after applied stop')
        return result
    e.post=timeout
    with pytest.raises(ExchangeUncertain):b.entry_stop(uuid.uuid4().hex,pos['id'])
    count=len(e.writes)
    e.post=original
    assert b.reconcile()
    assert b.entry_stop(uuid.uuid4().hex,pos['id'])['status']=='already_protected'
    assert len(e.writes)==count and not b.outbox.unresolved()


def test_live_entry_stop_api_requires_explicit_action(live):
    from fastapi.testclient import TestClient
    from yoyo.discipline_app.api import create_app
    b,e,_=live;enter(b);quote(b,'101');pos=b.snapshot()['positions'][0];count=len(e.writes)
    client=TestClient(create_app(b,'x'*36,market_enabled=False))
    path=f"/v1/positions/{pos['id']}/entry-stop"
    assert client.post(path,json={'request_id':uuid.uuid4().hex}).status_code==401
    assert client.post(path,headers={'Authorization':'Bearer '+'x'*36},json={'request_id':uuid.uuid4().hex}).status_code==409
    assert len(e.writes)==count

"""Hermetic Pro live lifecycle tests. No test contacts an exchange or enables it."""
import copy
import json
import time
from decimal import Decimal
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yoyo.monitor.pro.live_store import LiveStore
from yoyo.monitor.pro.live_models import Order, Settings, SourceConfig
from yoyo.monitor.pro.live_execution import Executor, split
from yoyo.monitor.pro.live_worker import Worker
from yoyo.monitor.pro.live_api import install
from yoyo.monitor.pro.gateway import origin_guard
from yoyo.monitor.pro.strategy_sources import evaluate, Sources
from yoyo.okx_execution.client import APIError, TransportError

SYMBOL='BTC-USDT-SWAP'
NOW=int(time.time()*1000)
INSTR=dict(instId=SYMBOL,instType='SWAP',ctType='linear',settleCcy='USDT',state='live',tickSz='0.1',lotSz='1',ctVal='1',minSz='1',maxMktSz='1000',maxLmtSz='1000',lever='100',instFamily='BTC-USDT')

def spec(**kw):
    args=dict(request_id='a'*32,symbol=SYMBOL,side='long',kind='market',quantity='1',leverage=2,stop='90',rr=3,lifetime_seconds=3600,max_slippage_bps=100)
    return Order(**(args|kw))

def session(**kw):return dict(id='s1',enabled=True,activated_ms=NOW-1000,expires_ms=NOW+3600000,account_uid='pro-only',risk_budget_usdt='100',max_plans=10,follow_ids=[],revision=0,**kw)

class Public:
    def __init__(self):self.ask='100';self.bid='99';self.now=NOW
    def quote(self,s):return dict(instId=s,askPx=self.ask,bidPx=self.bid,ts=str(self.now))
    def stop(self,s,side,tick):return {'price':'90' if side=='long' else '110'}

class Venue:
    def __init__(self):self.uid='pro-only';self.orders={};self.algos={};self.posts=[];self.unknown=False;self.leverage='2';self.external=[]
    def get(self,path,**q):
        if path.endswith('/config'):return [dict(uid=self.uid,posMode='long_short_mode')]
        if path.endswith('/instruments'):return [INSTR.copy()]
        if path.endswith('/position-tiers'):return [dict(minSz='0',maxSz='10000',maxLever='100')]
        if path.endswith('/leverage-info'):return [dict(lever=self.leverage)]
        if path.endswith('/order'):
            row=self.orders.get(q.get('clOrdId') or q.get('ordId'))
            if row:return [copy.deepcopy(row)]
            raise APIError('51603')
        if path.endswith('/order-algo'):
            row=self.algos.get(q['algoClOrdId'])
            if row:return [copy.deepcopy(row)]
            raise APIError('51603')
        raise AssertionError(path)
    def post(self,path,body):
        self.posts.append((path,copy.deepcopy(body)))
        if path.endswith('/set-leverage'):self.leverage=body['lever']
        elif path.endswith('/cancel-order'):self.orders[body['clOrdId']]['state']='canceled'
        elif path.endswith('/order'):
            self.orders[body['clOrdId']]={**body,'ordId':body['clOrdId'],'state':'live','accFillSz':'0','avgPx':'100'}
        elif path.endswith('/order-algo'):
            self.algos[body['algoClOrdId']]={**body,'algoId':body['algoClOrdId'],'state':'live'}
        elif path.endswith('/amend-algos'):
            self.algos[body['algoId']]['tpTriggerPx']=body['newTpTriggerPx']
        elif path.endswith('/cancel-algos'):
            self.algos[body[0]['algoId']]['state']='canceled'
        else:raise AssertionError(path)
        if self.unknown:raise TransportError('unknown')
        return [{'sCode':'0'}]
    def account(self,c=None):
        sums={}
        for r in self.orders.values():
            sign=1 if (r['side']=='buy')==(r['posSide']=='long') else -1
            k=(r['instId'],r['posSide']);sums[k]=sums.get(k,Decimal(0))+sign*Decimal(r['accFillSz'])
        return dict(uid=self.uid,total_equity_usdt='1000',available='1000',positions=self.external+[dict(instId=k[0],posSide=k[1],pos=str(v)) for k,v in sums.items() if v],orders=[r for r in self.orders.values() if r['state'] in ('live','partially_filled')],algos=[r for r in self.algos.values() if r['state']=='live'])
    def fill(self,c,qty=None,partial=False,attached=True):
        r=self.orders[c['entry_id']];r.update(state='partially_filled' if partial else 'filled',accFillSz=qty or r['sz'])
        if not partial and attached:
            a=r['attachAlgoOrds'][0]
            self.algos[c['algo_id']]=dict(algoId=c['algo_id'],algoClOrdId=c['algo_id'],instId=r['instId'],side='sell' if r['posSide']=='long' else 'buy',posSide=r['posSide'],sz=r['accFillSz'],state='live',**a)

@pytest.fixture
def rig(tmp_path):
    store=LiveStore(tmp_path,clock=lambda:NOW);store.set_meta('session',session());v=Venue();pub=Public();now=[NOW]
    ex=Executor(store,v,pub,clock=lambda:now[0],account_reader=v.account)
    return SimpleNamespace(store=store,v=v,pub=pub,now=now,ex=ex)

def approved(r,**kw):
    p=r.ex.preview(spec(**kw));return r.store.approve(p['id'],session(),now=NOW)
def tick(r,p,count=1):
    for _ in range(count):p=r.store.get(p['id']);r.ex.step(p)
    return r.store.get(p['id'])


def test_disabled_worker_never_constructs_private_client(tmp_path):
    s=LiveStore(tmp_path);calls=[]
    Worker(s,client_factory=lambda *a,**k:calls.append(k)).tick()
    assert calls==[] and s.rows()==[]

def test_preview_cannot_authorize_and_approval_is_idempotent(rig):
    r=rig;p=r.ex.preview(spec());r.ex.step(p);assert not r.v.posts
    r.store.pause()
    with pytest.raises(ValueError,match='暂停'):r.store.approve(p['id'],session(),now=NOW)
    r.store.set_meta('session',session());p=r.store.approve(p['id'],session(),now=NOW)
    assert r.store.approve(p['id'],session(),now=NOW)['id']==p['id']

@pytest.mark.parametrize('kind',['market','limit','iceberg','twap','post_only'])
def test_venue_receipts_before_protection_and_no_duplicate_entry(rig,kind):
    r=rig;p=approved(r,kind=kind,limit_price='100' if kind in ('limit','iceberg','post_only') else None)
    p=tick(r,p,3);assert len(r.v.posts)==1 and r.v.posts[0][1]['ordType']==('post_only' if kind in ('iceberg','post_only') else 'limit' if kind=='limit' else 'market')
    r.v.fill(p['children'][0]);p=tick(r,p,2)
    assert p['state']=='active' and p['children'][0]['protection_verified']
    tick(r,p,3);assert len(r.v.posts)==1

def test_unknown_entry_is_reconciled_not_reposted(rig):
    r=rig;p=approved(r);p=tick(r,p,2);r.v.unknown=True;p=tick(r,p)
    assert p['children'][0]['writes']['entry']['state']=='unknown'
    r.v.unknown=False;r.v.fill(p['children'][0]);p=tick(r,p,3)
    assert p['state']=='active' and len(r.v.posts)==1

def test_unknown_entry_without_receipt_stays_unknown(rig):
    r=rig;p=approved(r);p=tick(r,p,3);r.v.orders.clear();tick(r,p,4)
    assert len(r.v.posts)==1 and r.store.get(p['id'])['children'][0]['state']=='entry_pending'

def test_partial_fill_cancels_parent_then_protects_actual_quantity(rig):
    r=rig;p=approved(r,kind='limit',quantity='2',limit_price='100');p=tick(r,p,3)
    r.v.fill(p['children'][0],qty='1',partial=True);p=tick(r,p,4)
    assert [x[0].rsplit('/',1)[1] for x in r.v.posts]==['order','cancel-order','order-algo']
    assert r.v.posts[-1][1]['sz']=='1' and p['children'][0]['state']=='active'

def test_twap_waits_for_previous_protection_and_interval(rig):
    r=rig;p=approved(r,kind='twap',quantity='2',slices=2,interval_seconds=60);p=tick(r,p,3)
    p=tick(r,p);assert len(p['children'])==1
    r.v.fill(p['children'][0]);p=tick(r,p,2);assert len(p['children'])==1
    r.now[0]+=60000;p=tick(r,p);assert len(p['children'])==2
    p=tick(r,p,2);assert len(r.v.posts)==2 and all(x[1]['sz']=='1' for x in r.v.posts)

def test_pause_cancels_entry_but_retains_filled_protection(rig):
    r=rig;p=approved(r,kind='limit',limit_price='100');p=tick(r,p,3);r.store.pause();p=tick(r,p,2)
    assert p['state']=='canceled' and len(r.v.posts)==2

def test_pause_before_leverage_sends_no_exchange_write(rig):
    r=rig;r.v.leverage='10';p=approved(r);p=tick(r,p);r.store.pause();tick(r,p,3)
    assert not r.v.posts

def test_protection_timeout_closes_only_owned_size(rig):
    r=rig;p=approved(r);p=tick(r,p,3);r.v.fill(p['children'][0],attached=False);p=tick(r,p)
    r.now[0]+=16000;p=tick(r,p,2)
    close=r.v.posts[-1][1];assert close['side']=='sell' and close['sz']=='1'
    r.v.orders[close['clOrdId']].update(state='filled',accFillSz='1');p=tick(r,p,2)
    assert p['state']=='closed'

def test_owner_close_preserves_oco_until_exit_receipt(rig):
    r=rig;p=approved(r);p=tick(r,p,3);r.v.fill(p['children'][0]);p=tick(r,p,2)
    r.store.request(p['id'],'close');p=tick(r,p,2)
    assert r.v.algos[p['children'][0]['algo_id']]['state']=='live'
    close=r.v.posts[-1][1];r.v.orders[close['clOrdId']].update(state='filled',accFillSz='1');p=tick(r,p,3)
    assert p['state']=='closed' and r.v.algos[p['children'][0]['algo_id']]['state']=='canceled'

def test_external_position_and_uid_change_block_writes(rig):
    r=rig;p=approved(r);r.v.external=[dict(instId=SYMBOL,posSide='long',pos='1')]
    with pytest.raises(ValueError,match='不一致'):tick(r,p)
    r.v.external=[];r.v.uid='somebody-else'
    with pytest.raises(ValueError,match='身份'):tick(r,p)
    assert not r.v.posts

def test_trigger_and_expiry(rig):
    r=rig;p=approved(r,kind='trigger',trigger_price='101');p=tick(r,p,3);assert not p['children']
    r.pub.ask='101';p=tick(r,p,3);assert len(r.v.posts)==1

def test_plan_total_risk_and_split_rounding(rig):
    with pytest.raises(ValueError,match='3%'):rig.ex.preview(spec(kind='twap',quantity='4',slices=2))
    assert split(Decimal('10'),3,Decimal('1'),Decimal('1'))==[Decimal(3),Decimal(3),Decimal(4)]
    with pytest.raises(ValueError):split(Decimal('2'),3,Decimal('1'),Decimal('1'))

def test_risk_reservation_counts_all_previously_approved_plans(rig):
    r=rig;s=session();s['risk_budget_usdt']='11';r.store.set_meta('session',s);p=approved(r)
    r.store.request(p['id'],'cancel');tick(r,p)
    second=r.ex.preview(spec(request_id='b'*32))
    with pytest.raises(ValueError,match='预算'):r.store.approve(second['id'],s,now=NOW)

def source_config(kind='orderflow',**kw):
    return SourceConfig(id='flow',kind=kind,enabled=True,symbols=[SYMBOL],timeframe='15m',imbalance=.7,min_trades=3,**kw).model_dump()

def test_source_record_dedup_and_production_eligibility(tmp_path):
    s=LiveStore(tmp_path);src=Sources(s,Public(),clock=lambda:NOW)
    c=source_config();event=src.record(c,'v1',SYMBOL,NOW,dict(side='long',execution_eligible=False))
    src.record(c,'v1',SYMBOL,NOW,dict(side='long',execution_eligible=False))
    assert len(s.events())==1 and event['execution_eligible'] is False and event['production_claim'] is False

def test_flow_and_maker_use_actual_sample_and_quote():
    c=source_config();trades=[dict(side='buy',sz='2',ts=str(NOW-2)),dict(side='buy',sz='2',ts=str(NOW-1)),dict(side='sell',sz='1',ts=str(NOW))]
    assert evaluate(c,trades=trades)[0]['side']=='long'
    assert not evaluate(c,trades=trades[:2])
    m=source_config('maker',min_spread_bps=1,max_spread_bps=20)
    proposals=evaluate(m,quote={'bidPx':'100','askPx':'100.1','ts':str(NOW)})
    assert [p['side'] for p in proposals]==['long','short'] and all(p['kind']=='post_only' for p in proposals)
    assert not evaluate(m,quote={'bidPx':'100','askPx':'101','ts':str(NOW)})

def test_follow_ignores_historical_or_unqualified_events_and_dedups(rig):
    r=rig;c=source_config();follow=dict(id='f',source_id='flow',enabled=True,side='both',quantity='1',leverage=2,rr=3,lifetime_seconds=60,max_slippage_bps=100)
    settings=Settings(revision=0,sources=[c],follows=[follow]).model_dump();r.store.settings(settings)
    s=session();s.update(revision=1,follow_ids=['f'],activated_ms=NOW-100);r.store.set_meta('session',s)
    import hashlib
    version=hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest()[:16];src=Sources(r.store,r.pub,clock=lambda:NOW)
    for t,eligible in [(NOW-200,True),(NOW-50,False),(NOW-10,True)]:src.record(c,version,SYMBOL,t,dict(side='long',execution_eligible=eligible))
    worker=Worker(r.store,public=r.pub,clock=lambda:NOW);worker.follow(r.ex);worker.follow(r.ex)
    assert len(r.store.rows())==1 and r.store.rows()[0]['state']=='queued'
    assert r.store.rows()[0]['authorization']=='owner_follow_session'

def test_settings_revision_atomically_revokes_session(rig):
    r=rig;r.store.settings(dict(revision=0,sources=[],follows=[]))
    assert not r.store.meta('session')['enabled']
    with pytest.raises(ValueError,match='其他页面'):r.store.settings(dict(revision=0,sources=[],follows=[]))

def test_activation_cannot_reuse_preview_or_changed_config(rig):
    r=rig;r.store.set_meta('activation',dict(id='a',created_ms=NOW-1,preview_expires_ms=NOW+60000,revision=0,account_uid='pro-only',hours=1))
    r.store.settings(dict(revision=0,sources=[],follows=[]))
    with pytest.raises(ValueError,match='已变化'):r.store.activate('a','pro-only',NOW)
    r.store.set_meta('activation',dict(id='a',created_ms=NOW-1,preview_expires_ms=NOW+60000,revision=1,account_uid='pro-only',hours=1))
    activated=r.store.activate('a','pro-only',NOW)
    assert r.store.activate('a','pro-only',NOW+1000)==activated
    r.store.pause();assert not r.store.activate('a','pro-only',NOW+1000)['enabled']

def test_api_read_paths_never_construct_write_client(tmp_path):
    app=FastAPI();calls=[]
    def factory(*args,**kw):calls.append(kw);raise ValueError('not configured')
    install(app,origin_guard,directory=tmp_path,client_factory=factory,public=Public())
    with TestClient(app) as c:
        assert not c.get('/api/pro/live/status').json()['session']['enabled']
        assert c.get('/api/pro/sources').status_code==200
        assert c.get('/api/pro/live/plans').json()['items']==[]
        assert calls==[]
        assert c.post('/api/pro/live/preflight').status_code==403
        assert c.post('/api/pro/live/preflight',headers={'Origin':'http://testserver'}).status_code==409
        assert calls==[{'writes':False}]
        assert c.post('/api/pro/live/activation/xx/confirm',headers={'Origin':'http://testserver'},json={'acknowledgement':'wrong'}).status_code==422
        assert c.post('/api/pro/live/pause',headers={'Origin':'http://testserver'},json={'acknowledgement':'PAUSE-PRO-LIVE'}).status_code==200
        assert not c.get('/api/pro/live/status').json()['session']['enabled']


def test_exchange_clock_skew_uses_official_time_not_relaxed_freshness():
    from yoyo.monitor.pro.live_venue import PublicData
    class Book:
        stale=False
        def get(self,path,**kw):
            if path.endswith('/time'):return [{'ts':str(NOW+2000)}]
            assert path.endswith('/books')
            return [dict(ts=str(NOW-10000 if self.stale else NOW+1900),bids=[['100','1']],asks=[['100.1','1']])]
    client=Book();p=PublicData(client,clock=lambda:NOW)
    assert p.quote(SYMBOL)['bidPx']=='100'
    client.stale=True
    with pytest.raises(ValueError,match='过期'):p.quote(SYMBOL)


def test_public_candles_reject_gap_and_ignore_future_or_developing_bars():
    from yoyo.monitor.pro.live_venue import PublicData
    step=900000;cutoff=NOW//step*step
    class Candles:
        gap=False
        def get(self,path,**kw):
            if path.endswith('/time'):return [{'ts':str(NOW)}]
            assert path.endswith('/candles')
            rows=[[str(cutoff-(1000-i)*step),'100','101','99','100','10','0','0','1'] for i in range(1000)]
            if self.gap:rows.pop(500)
            rows+=[[str(cutoff),'999','999','999','999','100','0','0','0'],[str(cutoff+step),'999','999','999','999','100','0','0','1']]
            return [r for r in reversed(rows) if not kw.get('after') or int(r[0])<int(kw['after'])][:300]
    c=Candles();p=PublicData(c,clock=lambda:NOW);rows=p.candles(SYMBOL,'15m')
    assert len(rows)==881 and rows[-1]['close']==100 and rows[-1]['t']==cutoff-step
    c.gap=True
    with pytest.raises(ValueError,match='缺失'):PublicData(c,clock=lambda:NOW).candles(SYMBOL,'15m')


def test_definitively_canceled_partial_exit_uses_new_id_for_only_remaining(rig):
    r=rig;p=approved(r,quantity='2');p=tick(r,p,3);r.v.fill(p['children'][0]);p=tick(r,p,2)
    r.store.request(p['id'],'close');p=tick(r,p,2);first=r.v.posts[-1][1]
    r.v.orders[first['clOrdId']].update(state='canceled',accFillSz='1');p=tick(r,p,2);second=r.v.posts[-1][1]
    assert second['clOrdId']!=first['clOrdId'] and second['sz']=='1'
    r.v.orders[second['clOrdId']].update(state='filled',accFillSz='1');p=tick(r,p,3)
    assert p['state']=='closed' and len(p['children'][0]['exit_receipts'])==2


def test_unknown_close_is_never_reposted(rig):
    r=rig;p=approved(r);p=tick(r,p,3);r.v.fill(p['children'][0]);p=tick(r,p,2)
    r.store.request(p['id'],'close');r.v.unknown=True;p=tick(r,p,2)
    close=r.v.posts[-1][1];r.v.orders.pop(close['clOrdId']);r.v.unknown=False;tick(r,p,4)
    assert len(r.v.posts)==2


def test_authorization_requires_exact_review_and_source_eligibility(rig,monkeypatch):
    import yoyo.monitor.pro.live_api as module
    r=rig;app=FastAPI();calls=[]
    def factory(*args,**kwargs):calls.append(kwargs);return r.v
    monkeypatch.setattr(module,'Executor',lambda s,c,p:Executor(s,c,p,account_reader=c.account))
    install(app,origin_guard,directory=r.store.directory,client_factory=factory,public=r.pub)
    r.store.pause();r.store.set_meta('worker',dict(heartbeat_ms=int(time.time()*1000)))
    headers={'Origin':'http://testserver'}
    with TestClient(app) as c:
        body=dict(revision=0,account_uid='pro-only',risk_budget_usdt='20',max_plans=2,hours=1,follow_ids=[])
        d=c.post('/api/pro/live/activation/preview',json=body,headers=headers);assert d.status_code==200
        assert not c.get('/api/pro/live/status').json()['session']['enabled']
        identity=d.json()['id'];res=c.post('/api/pro/live/activation/'+identity+'/confirm',json={'acknowledgement':'ENABLE-PRO-LIVE'},headers=headers)
        assert res.status_code==200 and res.json()['enabled']
        assert all(x=={'writes':False} for x in calls) and not r.v.posts
        c.put('/api/pro/live/settings',json=dict(revision=0,sources=[source_config('ma')],follows=[dict(id='ma',source_id='flow',enabled=True,side='both',quantity='1',leverage=2,rr=3,lifetime_seconds=60,max_slippage_bps=20)]),headers=headers)
        body.update(revision=1,follow_ids=['ma']);res=c.post('/api/pro/live/activation/preview',json=body,headers=headers)
        assert res.status_code==409 and '准入' in res.json()['detail']
        assert not c.get('/api/pro/live/status').json()['session']['enabled']


def test_divergence_and_trend_return_evidence_from_closed_rows():
    c=source_config('divergence',lookback=3)
    rows=[dict(t=NOW-4,high=101,low=99,close=100,volume=10),dict(t=NOW-3,high=103,low=100,close=102,volume=20),dict(t=NOW-2,high=102,low=100,close=101,volume=20),dict(t=NOW-1,high=104,low=99,close=100,volume=30)]
    result=evaluate(c,rows)
    assert len(result)==1 and result[0]['side']=='short' and result[0]['evidence']['lookback']==3
    rows=[dict(close=100,ema20=101),dict(t=NOW,close=103,ema20=102,sma20=105,sma60=102,sma120=100)]
    assert evaluate(source_config('trend'),rows)[0]['side']=='long'
    rows[-1]['close']=101
    assert not evaluate(source_config('trend'),rows)

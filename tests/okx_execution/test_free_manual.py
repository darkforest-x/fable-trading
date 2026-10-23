"""Synthetic exchange only: confirmation, crash recovery and account isolation."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yoyo.okx_execution.client import TransportError, APIError
from yoyo.okx_execution.config import Config
from yoyo.okx_execution.manual_api import Service, install
from yoyo.okx_execution.manual_engine import ManualEngine
from yoyo.okx_execution.store import Store
from yoyo.okx_execution.free_manual import Tickets, make_plan, consume, REVISION

SYMBOL='BTC-USDT-SWAP'
TERMS=dict(ticket_id='a'*32,symbol=SYMBOL,side='long',order_type='market',notional_usdt='100',leverage='20',limit_price=None,take_profit='120',stop_loss='90')


class Venue:
    def __init__(self, clock):
        self.clock=clock;self.posts=[];self.positions=[];self.price='100';self.lever='20'
        self.order=None;self.algo=None;self.fail=None;self.accept=True;self.order_state='filled'
    def get(self,path,**query):
        if path.endswith('/config'):return [dict(uid='owner',posMode='long_short_mode',acctLv='2')]
        if path.endswith('/balance'):return [dict(totalEq='1000',details=[dict(ccy='USDT',eq='1000',eqUsd='1000',availEq='1000',imr='0')])]
        if path.endswith('/positions'):return deepcopy(self.positions)
        if path.endswith('/orders-pending') or path.endswith('/orders-algo-pending'):return []
        if path.endswith('/instruments'):return [dict(instId=SYMBOL,instFamily='BTC-USDT',groupId='4',instType='SWAP',state='live',ctType='linear',settleCcy='USDT',ctValCcy='BTC',ctVal='.01',tickSz='.1',lotSz='1',minSz='1',maxMktSz='10000',maxLmtSz='100000',lever='100')]
        if path.endswith('/position-tiers'):return [dict(minSz='0',maxSz='10000',maxLever='75')]
        if path.endswith('/ticker'):return [dict(instId=SYMBOL,askPx=self.price,bidPx=self.price,ts=str(self.clock()))]
        if path.endswith('/trade-fee'):return [dict(instType='SWAP',maker='',taker='',feeGroup=[dict(groupId='4',maker='-.0002',taker='-.0005')])]
        if path.endswith('/leverage-info'):return [dict(instId=SYMBOL,mgnMode='cross',lever=self.lever)]
        if path.endswith('/order-algo'):
            if self.algo:return [deepcopy(self.algo)]
            raise APIError('51603')
        if path.endswith('/order'):
            if self.order:return [deepcopy(self.order)]
            raise APIError('51603')
        raise AssertionError(path)
    def post(self,path,body):
        self.posts.append((path,deepcopy(body)))
        if self.accept:
            if path.endswith('/set-leverage'):self.lever=body['lever']
            if path.endswith('/order'):
                self.order=dict(body,ordId='entry1',state=self.order_state,accFillSz=body['sz'] if self.order_state=='filled' else '0',avgPx=self.price)
                if body.get('attachAlgoOrds'):
                    a=body['attachAlgoOrds'][0];self.algo=dict(a,instId=SYMBOL,algoClOrdId=a['attachAlgoClOrdId'],state='live',posSide=body['posSide'],side='sell' if body['side']=='buy' else 'buy',sz=body['sz'],tdMode='cross')
        if self.fail==path:raise TransportError('lost reply')
        return [dict(ordId='entry1',sCode='0')]


@pytest.fixture
def h(tmp_path):
    now=[1792764000000];clock=lambda:now[0]
    venue=Venue(clock);store=Store(tmp_path)
    store.set_meta('account_uid','owner');store.set_meta('manual_account_policy',dict(version=1,mode='manual_only_shared',account_uid='owner'));store.set_meta('free_manual_revision',REVISION)
    engine=ManualEngine(Config(),venue,None,store,clock=clock)
    return SimpleNamespace(now=now,engine=engine,venue=venue,store=store,book=Tickets(tmp_path),runtime=tmp_path)


def preview(h,**kw):return h.book.preview(make_plan(h.engine,dict(TERMS,**kw)))
def confirmed(h,**kw):
    d=preview(h,**kw);return h.book.confirm(d['id'],h.now[0],'owner')


def test_preview_reads_only_and_has_actual_contracts_and_fees(h):
    d=preview(h)
    assert d['size']=='1E+2' or float(d['size'])==100
    assert float(d['base_quantity'])==1 and float(d['margin'])==5
    assert float(d['fee_reserve'])==.05 and d['order_body']['attachAlgoOrds'][0]['tpTriggerPx']=='120'
    assert h.venue.posts==[]
    consume(h.engine,h.book,entries=True);assert h.venue.posts==[]


def test_fee_group_must_match_instrument_and_rebates_are_not_charged(h):
    get=h.venue.get
    group=dict(groupId='other',maker='0.0002',taker='-0.0005')
    h.venue.get=lambda path,**q: [dict(instType='SWAP',feeGroup=[group])] if path.endswith('/trade-fee') else get(path,**q)
    with pytest.raises(ValueError,match='手续费组'):preview(h)
    group['groupId']='4';value=preview(h)
    assert float(value['maker_fee'])==-.0002 and float(value['fee_reserve'])==.05
    assert not h.venue.posts


@pytest.mark.parametrize('terms',[
    dict(leverage='76'),dict(notional_usdt='0'),dict(notional_usdt='0.01'),dict(notional_usdt='100000000'),
    dict(stop_loss='110'),dict(take_profit='90'),dict(stop_loss='90.05'),
    dict(order_type='limit',limit_price='95.05'),dict(side='short',take_profit='120',stop_loss='90'),
])
def test_invalid_order_terms_never_authorize_writes(h,terms):
    with pytest.raises(ValueError):preview(h,**terms)
    assert not h.venue.posts


def test_existing_owner_position_and_strategy_lot_are_not_modified(h):
    h.venue.positions=[dict(instId=SYMBOL,pos='1')]
    with pytest.raises(ValueError,match='已有持仓'):preview(h)
    h.venue.positions=[];h.store.save(dict(id='strategy',symbol=SYMBOL,state='active'))
    with pytest.raises(ValueError,match='均线策略'):preview(h)
    assert not h.venue.posts


def test_atomic_confirmation_is_idempotent_and_symbol_is_reserved(h):
    d=preview(h)
    with ThreadPoolExecutor(4) as pool:values=list(pool.map(lambda _:h.book.confirm(d['id'],h.now[0],'owner'),range(12)))
    assert {v['id'] for v in values}=={d['id']}
    other=preview(h,ticket_id='b'*32)
    with pytest.raises(ValueError,match='已有待核对'):h.book.confirm(other['id'],h.now[0],'owner')
    consume(h.engine,h.book,entries=True);consume(h.engine,h.book,entries=True)
    assert len([p for p,b in h.venue.posts if p.endswith('/order')])==1
    assert h.book.get(d['id'])['state']=='filled'
    assert h.book.get(d['id'])['protection']=='verified'


def test_limits_and_optional_exits_retain_owner_values(h):
    h.venue.order_state='live';d=confirmed(h,side='short',order_type='limit',limit_price='105',take_profit=None,stop_loss=None)
    consume(h.engine,h.book,entries=True)
    body=h.venue.posts[-1][1]
    assert body['ordType']=='limit' and body['px']=='105' and body['posSide']=='short'
    assert 'attachAlgoOrds' not in body and h.book.get(d['id'])['state']=='live'
    consume(h.engine,h.book,entries=True);assert len(h.venue.posts)==2


@pytest.mark.parametrize('accepted',[True,False])
def test_timeout_and_restart_never_resend_an_entry(h,accepted):
    d=confirmed(h);h.venue.fail='/api/v5/trade/order';h.venue.accept=accepted
    consume(h.engine,h.book,entries=True)
    restored=Tickets(h.runtime)
    for _ in range(4):consume(h.engine,restored,entries=True)
    assert len([p for p,b in h.venue.posts if p.endswith('/order')])==1
    assert restored.get(d['id'])['state']==('filled' if accepted else 'sending')


def test_leverage_timeout_reconciles_without_repeating_mutation(h):
    d=confirmed(h);h.venue.fail='/api/v5/account/set-leverage'
    consume(h.engine,h.book,entries=True);assert h.book.get(d['id'])['state']=='leverage_pending'
    consume(h.engine,h.book,entries=True)
    assert [p for p,b in h.venue.posts]==['/api/v5/account/set-leverage','/api/v5/trade/order']


def test_manage_only_expiry_and_price_increase_do_not_send(h):
    d=confirmed(h);consume(h.engine,h.book,entries=False);assert not h.venue.posts
    h.venue.price='101';consume(h.engine,h.book,entries=True)
    assert h.book.get(d['id'])['state']=='rejected' and not h.venue.posts
    h.venue.price='100';d=confirmed(h);h.now[0]+=60001;consume(h.engine,h.book,entries=True)
    assert h.book.get(d['id'])['state']=='expired' and not h.venue.posts


def test_partial_limit_fill_does_not_claim_active_protection(h):
    h.venue.order_state='live';d=confirmed(h,order_type='limit',limit_price='95')
    consume(h.engine,h.book,entries=True)
    h.venue.order.update(state='partially_filled',accFillSz='10');consume(h.engine,h.book,entries=True)
    value=h.book.get(d['id']);assert value['protection']=='waiting_full_fill' and value['state']=='partially_filled'
    h.venue.order.update(state='filled',accFillSz=value['size']);h.venue.algo=None
    consume(h.engine,h.book,entries=True);assert h.book.get(d['id'])['state']=='protection_pending'
    assert len(h.venue.posts)==2


def test_limit_receipt_with_changed_price_remains_reserved(h):
    h.venue.order_state='live';d=confirmed(h,order_type='limit',limit_price='95')
    consume(h.engine,h.book,entries=True)
    h.venue.order['px']='96';consume(h.engine,h.book,entries=True)
    assert h.book.get(d['id'])['state']=='unknown'
    other=preview(h,ticket_id='b'*32)
    with pytest.raises(ValueError,match='已有待核对'):h.book.confirm(other['id'],h.now[0],'owner')
    assert len(h.venue.posts)==2


def test_same_origin_api_preview_confirm_and_route_auth(h):
    service=Service(SimpleNamespace(),h.runtime,engine=h.engine,clock=h.engine.clock,healthy=lambda:None)
    app=FastAPI();install(app,SimpleNamespace(),service=service)
    headers={'Origin':'http://testserver','X-Fable-Manual':'owner-confirmation','Sec-Fetch-Site':'same-origin'}
    with TestClient(app) as c:
        assert c.post('/api/manual-live/free/preview',json=TERMS).status_code==403
        assert c.post('/api/manual-live/free/preview',json=dict(TERMS,account='wrong'),headers=headers).status_code==422
        d=c.post('/api/manual-live/free/preview',json=TERMS,headers=headers).json()
        assert 'account_uid' not in d and 'order_body' not in d and not h.venue.posts
        path='/api/manual-live/free/'+d['id']+'/confirm'
        assert c.post(path,json={'acknowledgement':'CONFIRM-LIVE-ORDER'},headers=headers).status_code==422
        assert c.post(path,json={'acknowledgement':'CONFIRM-FREE-LIVE-ORDER'},headers=headers).json()['state']=='confirmed'
        assert not h.venue.posts
        consume(h.engine,h.book,entries=True)
        assert c.get('/api/manual-live/free/'+d['id']).json()['state']=='filled'
        assert c.post(path,json={'acknowledgement':'CONFIRM-FREE-LIVE-ORDER'},headers=headers).json()['state']=='filled'
        assert len(c.get('/api/manual-live/free/recent').json()['items'])==1

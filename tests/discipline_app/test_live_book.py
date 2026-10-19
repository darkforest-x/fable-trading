"""Live orchestration acceptance against a stateful fake OKX; never real orders."""
import copy
from decimal import Decimal as D
import uuid
import pytest
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.okx_execution import ExchangeRejected, ExchangeUncertain
from yoyo.discipline_app.policy import Rejected


class Exchange:
    environment='okx_live'
    write_enabled=True
    def __init__(self,clock):
        self.clock=clock
        self.orders={}; self.algos={}; self.positions=[]; self.history=[]; self.writes=[]
        self.leverage='100'; self.equity='1000'; self.fee='-0.0005'; self.timeout=False
        self.reject=False; self.cancel_entry=False; self.foreign=[]
    def get(self,path,**params):
        if path.endswith('/config'): return [dict(acctLv='2',posMode='long_short_mode',perm='read_only,trade',uid='fixture-account')]
        if path.endswith('/balance'): return [dict(details=[dict(ccy='USDT',eq=self.equity,cashBal=self.equity,availEq=self.equity)])]
        if path.endswith('/positions'): return copy.deepcopy(self.positions)
        if path.endswith('/positions-history'): return copy.deepcopy(self.history)
        if path.endswith('/orders-pending'): return copy.deepcopy(self.foreign)
        if path.endswith('/orders-algo-pending'): return copy.deepcopy([x for x in self.algos.values() if x['state']=='live']) if params['ordType']=='conditional' else []
        if path.endswith('/trade-fee'): return [dict(taker='',feeGroup=[dict(groupId='2',taker=self.fee)])]
        if path.endswith('/leverage-info'): return [dict(lever=self.leverage)]
        if path.endswith('/order'):
            row=next((x for x in self.orders.values() if x['clOrdId']==params.get('clOrdId') or x['ordId']==params.get('ordId')),None)
            if not row: raise ExchangeRejected('51603')
            return [copy.deepcopy(row)]
        if path.endswith('/order-algo'):
            row=next((x for x in self.algos.values() if x['algoClOrdId']==params.get('algoClOrdId') or x['algoId']==params.get('algoId')),None)
            if not row: raise ExchangeRejected('51603')
            return [copy.deepcopy(row)]
        raise AssertionError(path)
    def post(self,path,payload):
        self.writes.append((path,copy.deepcopy(payload)))
        if path.endswith('/set-leverage'):
            self.leverage=payload['lever']; return [dict(sCode='0',lever=self.leverage)]
        if path.endswith('/order'):
            if self.reject: raise ExchangeRejected('51008')
            order=dict(payload,ordId=str(len(self.orders)+1),state='filled',accFillSz=payload['sz'],avgPx='100',fee='-0.05',feeCcy='USDT',cTime=str(int(self.clock()*1000)))
            if self.cancel_entry:
                order.update(state='canceled',accFillSz='0')
            elif payload.get('attachAlgoOrds'):
                self.positions.append(dict(instId=payload['instId'],posSide=payload['posSide'],pos=payload['sz'],mgnMode='cross',avgPx='100',posId='pos-1',realizedPnl='-0.05'))
                for child in payload['attachAlgoOrds']:
                    algo=dict(child,algoId=str(len(self.algos)+1),algoClOrdId=child['attachAlgoClOrdId'],state='live',instId=payload['instId'],posSide=payload['posSide'],side='sell' if payload['side']=='buy' else 'buy',sz=child.get('sz',payload['sz']))
                    self.algos[algo['algoClOrdId']]=algo
            self.orders[payload['clOrdId']]=order
            if self.timeout: raise ExchangeUncertain('timeout after exchange fill')
            return [dict(sCode='0',ordId=order['ordId'])]
        if path.endswith('/amend-algos'):
            algo=next(x for x in self.algos.values() if x['algoId']==payload['algoId'])
            for new,old in [('newSlTriggerPx','slTriggerPx'),('newTpTriggerPx','tpTriggerPx')]:
                if new in payload: algo[old]=payload[new]
            return [dict(sCode='0',algoId=algo['algoId'])]
        if path.endswith('/cancel-algos'):
            for item in payload:
                next(x for x in self.algos.values() if x['algoId']==item['algoId'])['state']='canceled'
            return [dict(sCode='0',algoId=x['algoId']) for x in payload]
        raise AssertionError(path)


@pytest.fixture
def live(tmp_path):
    now=[1792332000.0]
    clock=lambda:now[0]
    exchange=Exchange(clock)
    book=LiveBook(tmp_path/'live.sqlite3',exchange,clock)
    quote(book)
    assert book.reconcile()
    return book,exchange,now


def quote(book,price='100'):
    instruments={f'{s}-USDT-SWAP':dict(symbol=f'{s}-USDT-SWAP',step='.01',minimum='.01',tick='.01',contract_value='.01',fee_group='2',max_leverage=100) for s in ('BTC','ETH','SOL')}
    book.update_market(instruments,{s:dict(last=price,bid=price,ask=price,time=book.clock()) for s in instruments})


def ticket(**changes):
    return dict(dict(symbol='BTC-USDT-SWAP',side='long',kind='market',price='',stop='95',fraction='.2',leverage=100),**changes)


def enter(book,request=None,key=None):
    request=request or ticket()
    preview=book.preview(request)
    key=key or uuid.uuid4().hex
    return book.enter(key,request,preview['policy_revision'],preview['preview_id'],True),key,preview


def test_read_only_reconcile_never_writes_and_preview_requires_confirmation(live):
    book,exchange,_=live
    preview=book.preview(ticket())
    with pytest.raises(Rejected,match='明确确认'):
        book.enter(uuid.uuid4().hex,ticket(),1,preview['preview_id'])
    assert exchange.writes==[]


def test_complete_entry_receipt_native_protection_and_idempotent_retry(live):
    book,exchange,_=live
    receipt,key,preview=enter(book)
    assert receipt['status']=='filled'
    state=book.snapshot()
    assert state['mode']=='okx_live' and state['equity']=='1000'
    assert state['daily']['entries']==1
    assert state['positions'][0]['entry']=='100'
    assert state['positions'][0]['protection']=='exchange-verified'
    assert not state['private_error']
    before=len(exchange.writes)
    assert book.enter(key,ticket(),1,preview['preview_id'],True)['status']=='filled'
    assert len(exchange.writes)==before
    assert len([x for x in exchange.writes if x[0].endswith('/order')])==1
    assert len(exchange.algos)==3


def test_timeout_with_actual_fill_reconciles_without_duplicate(live):
    book,exchange,_=live
    exchange.timeout=True
    receipt,_,_=enter(book)
    assert receipt['status']=='filled'
    assert book.outbox.unresolved()==[]
    assert len([x for x in exchange.writes if x[0].endswith('/order')])==1


def test_invalid_fee_foreign_position_expired_preview_and_risk_all_reject(live):
    book,exchange,now=live
    exchange.fee='-0.001'
    with pytest.raises(Rejected,match='手续费'): book.preview(ticket())
    exchange.fee='-0.0005'
    exchange.positions=[dict(instId='ETH-USDT-SWAP',posSide='long',pos='1')]
    with pytest.raises(Rejected,match='未管理'): book.preview(ticket())
    exchange.positions=[]
    preview=book.preview(ticket())
    now[0]+=46;quote(book)
    with pytest.raises(Rejected,match='过期'): book.enter(uuid.uuid4().hex,ticket(),1,preview['preview_id'],True)
    assert exchange.writes==[]


def test_breakeven_needs_exchange_amend_readback(live):
    book,exchange,_=live
    enter(book)
    quote(book,'107')
    assert book.reconcile()
    pos=book.snapshot()['positions'][0]
    assert pos['breakeven'] is True and D(pos['stop'])>D(pos['entry'])
    assert any('newSlTriggerPx' in payload for _,payload in exchange.writes)
    before=len(exchange.writes)
    assert book.reconcile()
    assert len(exchange.writes)==before


def test_zero_fill_fok_cancel_refunds_quota_but_keeps_intent(live):
    book,exchange,_=live
    exchange.cancel_entry=True
    result,_,_=enter(book)
    assert result['status']=='cancelled'
    assert not book.snapshot()['positions'] and not book.snapshot()['pending']
    assert book.snapshot()['daily']['entries']==0


def test_closed_loss_uses_exchange_net_settlement_and_survives_restart(live):
    book,exchange,now=live
    _,key,_=enter(book)
    order=next(iter(exchange.orders.values()))
    now[0]+=10;quote(book)
    exchange.positions=[]
    exchange.history=[dict(posId='pos-1',posSide='long',uTime=str(int(now[0]*1000)),type='2',closeTotalPos=order['sz'],realizedPnl='-10.25',fee='-0.25')]
    for _ in range(4): book.reconcile()
    state=book.snapshot()
    assert state['closed'][0]['pnl']=='-10.25'
    assert state['loss_streak']==1 and state['cooldown_until']==0
    assert 'intents' not in state
    with book.transaction() as (_, internal):
        assert internal['intents'][key]['phase']=='closed'
    restarted=LiveBook(book.path,exchange,book.clock)
    assert restarted.snapshot()['cooldown_until']==state['cooldown_until']
    assert restarted.preview(ticket())['plan']


def test_missing_stop_keeps_entries_blocked_and_uses_one_emergency_close(live):
    book,exchange,now=live
    enter(book)
    stop=next(x for x in exchange.algos.values() if 'slTriggerPx' in x)
    stop['state']='canceled'
    now[0]+=21;quote(book)
    assert not book.reconcile()
    assert not book.reconcile()
    closes=[payload for _,payload in exchange.writes if payload.get('ordType')=='market']
    assert len(closes)==1 and closes[0]['side']=='sell' and closes[0]['posSide']=='long'
    assert book.snapshot()['blockers']


def test_daily_cap_and_single_use_preview_cannot_be_bypassed(live):
    book,exchange,_=live
    exchange.cancel_entry=True
    _,_,preview=enter(book)
    with pytest.raises(Rejected,match='预览'):
        book.enter(uuid.uuid4().hex,ticket(),1,preview['preview_id'],True)
    assert book.snapshot()['daily']['entries']==0
    with book.transaction() as (_,state):
        state['daily']['entries']=book.entry_limit(state)
    with pytest.raises(Rejected,match='次数'):
        book.preview(ticket())
    assert book.snapshot()['daily']['entries']==3


def test_api_requires_exact_live_confirmation_and_never_treats_paper_as_live(live):
    from fastapi.testclient import TestClient
    from yoyo.discipline_app.api import create_app
    book,exchange,_=live
    token='fixture-access-token-at-least-32-characters'
    with TestClient(create_app(book,token,market_enabled=False)) as client:
        assert client.get('/health').json()['mode']=='okx_live'
        assert client.post('/v1/preview',json=ticket()).status_code==401
        headers={'Authorization':'Bearer '+token}
        preview=client.post('/v1/preview',headers=headers,json=ticket()).json()
        body=dict(request_id=uuid.uuid4().hex,ticket=ticket(),policy_revision=1,preview_id=preview['preview_id'])
        assert client.post('/v1/orders',headers=headers,json=body).status_code==409
        body['confirmed_live']='true'
        assert client.post('/v1/orders',headers=headers,json=body).status_code==422
        assert not exchange.writes
        body['confirmed_live']=True
        receipt=client.post('/v1/orders',headers=headers,json=body)
        assert receipt.status_code==200 and receipt.json()['status']=='filled'


def test_close_timeout_can_be_reconciled_without_another_close(live):
    book,exchange,_=live
    _,identity,_=enter(book)
    key=uuid.uuid4().hex
    exchange.timeout=True
    with pytest.raises(ExchangeUncertain):book.close(key,identity)
    exchange.timeout=False
    book.close(key,identity)
    closes=[payload for _,payload in exchange.writes if payload.get('ordType')=='market']
    assert len(closes)==1


def test_split_take_profit_is_counted_only_after_fill_and_full_net_settlement(live):
    book,exchange,now=live
    enter(book)
    position=exchange.positions[0]
    take_profits=[row for row in exchange.algos.values() if 'tpTriggerPx' in row]
    first=take_profits[0]
    first.update(state='effective',ordIdList=['tp-order'])
    exchange.orders['tp-order']=dict(ordId='tp-order',clOrdId='tp-order',accFillSz='0')
    quote(book,'120')
    assert not book.reconcile()
    assert book.snapshot()['positions'][0]['stages']==[]
    exchange.orders['tp-order']['accFillSz']=first['sz']
    position['pos']=str(D(position['pos'])-D(first['sz']))
    assert book.reconcile()
    state=book.snapshot()
    assert state['positions'][0]['stages']==[0] and state['positions'][0]['breakeven']
    assert state['cooldown_until']==0 and state['closed']==[]
    now[0]+=5;quote(book,'130')
    original=next(x for x in exchange.orders.values() if x.get('attachAlgoOrds'))
    exchange.positions=[]
    exchange.history=[dict(posId='pos-1',posSide='long',uTime=str(int(now[0]*1000)),type='2',closeTotalPos=original['sz'],realizedPnl='38.05',fee='-0.3')]
    for _ in range(4):book.reconcile()
    state=book.snapshot()
    assert not state['positions'] and state['closed'][0]['pnl']=='38.05'
    assert state['loss_streak']==0 and state['cooldown_until']==0
    stats=book.statistics()
    assert stats['mode']=='okx_live' and stats['closed']['wins']==1
    assert stats['account']['return_fraction'] is None

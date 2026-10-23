"""Shared exchange positions, exact App fills, and inherited cross leverage.

All exchange effects are in-memory. No credentials or live account are used.
"""
import copy
import uuid
from decimal import Decimal as D
import pytest
from test_live_book import quote, ticket, enter
from test_managed_orders import ManagedExchange
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.owned_lots import lot_totals
from yoyo.discipline_app.policy import Rejected
from yoyo.discipline_app.okx_execution import ExchangeUncertain


class SharedExchange(ManagedExchange):
    def __init__(self, clock):
        super().__init__(clock)
        self.leverage='20'
        self.fills=[]
        self.hide_fills=False
        self.counter=0

    def outside(self, symbol='BTC-USDT-SWAP', side='long', mode='cross', size='50', price='90'):
        self.positions.append(dict(instId=symbol,posSide=side,mgnMode=mode,pos=size,
            avgPx=price,posId='outside-'+side+'-'+mode,realizedPnl='-999',liqPx='0'))

    def get(self, path, **params):
        if path.endswith('/fills-history'):
            rows=[copy.deepcopy(f) for f in self.fills if f['ordId']==params['ordId']]
            return [] if self.hide_fills else rows[::-1]
        return super().get(path,**params)

    def post(self, path, payload):
        if path.endswith('/order'):
            self.writes.append((path,copy.deepcopy(payload)))
            self.counter+=1
            row=dict(payload,ordId=str(self.counter),accFillSz='0',avgPx='',fee='0',
                     feeCcy='USDT',cTime=str(int(self.clock()*1000)),state='live')
            self.orders[row['clOrdId']]=row
            if row['ordType']!='limit':
                self.fill(row['clOrdId'],row['sz'])
            if self.timeout:
                self.timeout=False
                raise ExchangeUncertain('lost receipt')
            return [dict(sCode='0',ordId=row['ordId'])]
        if path.endswith('/amend-algos') and 'newSz' in payload:
            self.writes.append((path,copy.deepcopy(payload)))
            row=next(a for a in self.algos.values() if a['algoId']==payload['algoId'])
            row['sz']=payload['newSz']
            return [dict(sCode='0',algoId=row['algoId'])]
        return super().post(path,payload)

    def fill(self, cl_id, size, price='100'):
        row=self.orders[cl_id]
        previous=D(row['accFillSz']); total=D(size); delta=total-previous; price=D(price)
        assert delta>0
        self.counter+=1
        fee=-delta*D('.01')*price*D('.0005')
        fill=dict(instId=row['instId'],posSide=row['posSide'],side=row['side'],ordId=row['ordId'],
            clOrdId=cl_id,fillSz=str(delta),fillPx=str(price),fee=str(fee),feeCcy='USDT',
            billId=str(self.counter),tradeId=str(self.counter),fillTime=str(int(self.clock()*1000)+self.counter))
        self.fills.append(fill)
        row.update(accFillSz=str(total),avgPx=str((previous*D(row['avgPx'] or '0')+delta*price)/total),
            fee=str(D(row['fee'])+fee),state='filled' if total==D(row['sz']) else 'partially_filled')
        pos=next((p for p in self.positions if p['instId']==row['instId'] and p['posSide']==row['posSide'] and p['mgnMode']=='cross'),None)
        opening=row['side']==('buy' if row['posSide']=='long' else 'sell')
        if opening:
            if pos is None:
                self.outside(row['instId'],row['posSide'],size='0',price=str(price));pos=self.positions[-1]
            amount=D(pos['pos'])+delta
            pos.update(avgPx=str((D(pos['pos'])*D(pos['avgPx'])+delta*price)/amount),pos=str(amount))
        else:
            assert pos and D(pos['pos'])>=delta
            pos['pos']=str(D(pos['pos'])-delta)
        if row['state']=='filled':
            for spec in row.get('attachAlgoOrds',[]):
                self.algos[spec['attachAlgoClOrdId']]=dict(spec,algoClOrdId=spec['attachAlgoClOrdId'],
                    algoId='algo-'+str(len(self.algos)),state='live',instId=row['instId'],posSide=row['posSide'],
                    side='sell' if row['side']=='buy' else 'buy',sz=spec.get('sz',row['sz']))

    def trigger(self, identity, price='110'):
        algo=self.algos[identity]
        payload=dict(instId=algo['instId'],posSide=algo['posSide'],side=algo['side'],tdMode='cross',
                     ordType='limit',sz=algo['sz'],clOrdId='child-'+identity)
        self.post('/api/v5/trade/order',payload)
        self.fill(payload['clOrdId'],payload['sz'],price)
        algo.update(state='effective',ordId=self.orders[payload['clOrdId']]['ordId'])


@pytest.fixture
def shared(tmp_path):
    now=[1792332000.0]
    ex=SharedExchange(lambda:now[0]);ex.outside()
    book=LiveBook(tmp_path/'shared.db',ex,lambda:now[0]);quote(book)
    assert book.reconcile()
    return book,ex,now


def open_shared(book,**changes):
    return enter(book,ticket(leverage=20,**changes))


def test_same_side_external_lot_does_not_block_or_change_leverage(shared):
    book,ex,_=shared
    state=book.snapshot()
    assert 'external_activity' not in state and state['shared_leverage']['BTC-USDT-SWAP']==20
    receipt,key,_=open_shared(book)
    state=book.snapshot();pos=state['positions'][0]
    assert receipt['status']=='filled' and not state['blockers']
    assert D(pos['remaining'])==D(receipt['plan']['quantity'])
    assert D(pos['entry'])==100 and D(pos['pnl'])==D(ex.orders[next(iter(ex.orders))]['fee'])
    assert not any(path.endswith('set-leverage') for path,_ in ex.writes)
    assert D(ex.positions[0]['pos'])==50+D(pos['remaining'])/D('.01')
    assert pos['protection']=='exchange-verified'
    assert book.statistics()['account']['net_pnl']==pos['pnl']
    restarted=LiveBook(book.path,ex,book.clock);assert restarted.reconcile()
    assert restarted.snapshot()['positions'][0]['remaining']==pos['remaining']


def test_partial_and_full_close_only_manage_owned_size_and_fees(shared):
    book,ex,_=shared
    _,key,_=open_shared(book)
    original=book.snapshot()['positions'][0]
    book.close(uuid.uuid4().hex,key,'0.5')
    pos=book.snapshot()['positions'][0]
    assert D(pos['remaining'])<D(original['remaining']) and not book.snapshot()['private_error']
    live_stop=next(a for a in ex.algos.values() if a['state']=='live' and 'slTriggerPx' in a)
    assert D(live_stop['sz'])==D(pos['remaining'])/D('.01')
    assert sum(D(a['sz']) for a in ex.algos.values() if a['state']=='live' and 'tpTriggerPx' in a)<=D(live_stop['sz'])
    book.close(uuid.uuid4().hex,key)
    for _ in range(5): book.reconcile()
    state=book.snapshot()
    assert not state['positions'] and not state['blockers']
    assert D(ex.positions[0]['pos'])==50
    assert len(state['closed'])==1 and D(state['closed'][0]['pnl'])==sum(D(f['fee']) for f in ex.fills)
    assert not any(a['state']=='live' for a in ex.algos.values())


def test_gtc_partial_fill_and_scale_in_ignore_combined_exchange_average(shared):
    book,ex,_=shared
    _,root,_=open_shared(book,kind='limit',price='100')
    order=next(iter(ex.orders.values()));total=D(order['sz'])
    ex.fill(order['clOrdId'],str(total/2));assert book.reconcile()
    first=book.snapshot()['positions'][0]
    assert D(first['remaining'])==total/2*D('.01')
    ex.fill(order['clOrdId'],str(total),'99');assert book.reconcile()
    pos=book.snapshot()['positions'][0]
    assert D(pos['remaining'])==total*D('.01') and D(pos['entry'])==D('99.5')
    assert not book.snapshot()['private_error']
    result,child,_=open_shared(book,stop=pos['stop'])
    assert result['status']=='filled' and len(book.snapshot()['positions'])==1
    pos=book.snapshot()['positions'][0]
    assert D(pos['remaining'])==total*D('.01')+D(result['plan']['quantity'])
    assert D(ex.positions[0]['avgPx'])!=D(pos['entry'])
    assert book.reconcile() and not book.snapshot()['blockers']


def test_owned_stop_settles_without_waiting_for_external_position_to_close(shared):
    book,ex,_=shared
    open_shared(book)
    stop=next(k for k,a in ex.algos.items() if 'slTriggerPx' in a)
    ex.trigger(stop,'95')
    for _ in range(5): book.reconcile()
    state=book.snapshot();assert not state['positions'] and len(state['closed'])==1
    assert D(ex.positions[0]['pos'])==50 and not state['private_error']
    quantity=D(state['closed'][0]['quantity'])
    assert D(state['closed'][0]['pnl'])==-5*quantity+sum(D(f['fee']) for f in ex.fills)


def test_fill_lag_and_lost_ack_never_invent_quantity_or_repeat_entry(shared):
    book,ex,_=shared
    ex.hide_fills=True;ex.timeout=True
    result,key,preview=open_shared(book)
    assert book.snapshot()['private_error'] and not book.snapshot()['positions']
    count=len(ex.orders)
    ex.hide_fills=False;assert book.reconcile()
    book.enter(key,ticket(leverage=20),preview['policy_revision'],preview['preview_id'],True)
    assert len(ex.orders)==count and book.snapshot()['positions'][0]['protection']=='exchange-verified'


def test_leverage_change_after_preview_requires_reconfirmation_without_write(shared):
    book,ex,_=shared
    request=ticket(leverage=20);preview=book.preview(request);ex.leverage='10'
    with pytest.raises(Rejected,match='当前杠杆'):
        book.enter(uuid.uuid4().hex,request,preview['policy_revision'],preview['preview_id'],True)
    assert not ex.writes


def test_opposite_side_external_position_also_inherits_cross_leverage(shared):
    book,ex,_=shared
    ex.positions[0]['posSide']='short';assert book.reconcile()
    result,_,_=open_shared(book)
    assert result['status']=='filled' and not book.snapshot()['blockers']
    assert ex.positions[0]['pos']=='50' and not any(p.endswith('set-leverage') for p,_ in ex.writes)


def test_external_reduction_is_not_fabricated_as_app_realized_pnl(shared):
    book,ex,_=shared
    _,key,_=open_shared(book)
    before=book.snapshot()['positions'][0]
    ex.positions[0]['pos']='0'
    assert not book.reconcile()
    assert book.snapshot()['positions'][0]['pnl']==before['pnl']
    with pytest.raises(Rejected,match='自有数量'):
        book.close(uuid.uuid4().hex,key)


def test_legacy_flat_lot_recovers_from_own_closes_when_exchange_history_is_mixed(shared):
    book,ex,_=shared
    ex.positions=[];assert book.reconcile()
    _,key,_=enter(book)
    # Existing app versions used the combined position history for settlement.
    with book.transaction() as (_,state):
        state['intents'][key].pop('owned_accounting',None)
    book.close(uuid.uuid4().hex,key)
    for _ in range(5):book.reconcile()
    assert not book.snapshot()['positions'] and not book.snapshot()['private_error']
    assert D(book.snapshot()['closed'][0]['pnl'])==sum(D(f['fee']) for f in ex.fills)


def test_shared_leverage_above_policy_default_changes_no_risk_or_notional_cap(shared):
    book,ex,_=shared
    ex.positions=[];ex.outside(symbol='SOL-USDT-SWAP');ex.leverage='50'
    assert book.reconcile()
    request=ticket(symbol='SOL-USDT-SWAP',leverage=50)
    result=book.preview(request)['plan']
    assert D(result['risk'])<=D(result['budget'])
    assert book.snapshot()['policy']['alt_leverage']==20
    assert not ex.writes


def test_external_isolated_lot_does_not_lock_unrelated_cross_leverage(shared):
    book,ex,_=shared
    ex.positions[0]['mgnMode']='isolated'
    assert book.reconcile()
    assert 'BTC-USDT-SWAP' not in book.snapshot()['shared_leverage']
    result,_,_=enter(book)
    assert result['status']=='filled' and not book.snapshot()['private_error']
    assert ex.positions[0]['pos']=='50'


def test_retired_tp_fill_remains_in_owned_cost_after_later_scale_in(shared):
    book,ex,_=shared
    _,root,_=open_shared(book)
    first=book.snapshot()['positions'][0]
    target=next(k for k,a in ex.algos.items() if 'tpTriggerPx' in a)
    ex.trigger(target,'101');assert book.reconcile()
    reduced=book.snapshot()['positions'][0]
    assert D(reduced['remaining'])<D(first['remaining'])
    result,_,_=open_shared(book,stop=reduced['stop'])
    assert result['status']=='filled' and book.reconcile()
    pos=book.snapshot()['positions'][0]
    assert D(pos['remaining'])==D(reduced['remaining'])+D(result['plan']['quantity'])
    assert D(pos['pnl'])>D(reduced['pnl'])-1


def test_chronological_fills_rebase_only_remaining_owned_cost():
    fills=[]
    for n,(side,size,price) in enumerate([('buy','10','100'),('sell','5','110'),('buy','5','120'),('sell','10','130')],1):
        fills.append(dict(side=side,fillSz=size,fillPx=price,fee='-1',feeCcy='USDT',fillTime=str(n),tradeId=str(n),billId=str(n)))
    result=lot_totals(fills[::-1],D(1),'long')
    assert result['remaining']==0 and result['pnl']==246 and result['fees']==4


def test_legacy_exit_allocation_is_bounded_by_owned_quantity_and_fixed_cutover():
    fills=[]
    for n,(side,size,price) in enumerate([('buy','10','100'),('sell','8','110'),('sell','8','120'),('sell','10','90')],1):
        fills.append(dict(side=side,fillSz=size,fillPx=price,fee='-1',feeCcy='USDT',fillTime=str(n),tradeId=str(n),billId=str(n)))
    with pytest.raises(Rejected,match='超出自有数量'):
        lot_totals(fills,D(1),'long')
    result=lot_totals(fills,D(1),'long',4)
    assert result['remaining']==0 and result['pnl']==D('117.75') and result['fees']==D('2.25')
    assert result['closed']==3 and result['legacy_external_exit']==16
    with pytest.raises(Rejected,match='超出自有数量'):
        lot_totals(fills,D(1),'long',2)


def test_large_external_order_list_is_paginated_without_blocking_app(shared):
    book,ex,_=shared
    original=ex.get
    rows=[dict(instId='SOL-USDT-SWAP',posSide='long',tdMode='cross',ordId=str(200-n),
               clOrdId='external-'+str(n),sz='1',accFillSz='0',px='100') for n in range(101)]
    def get(path,**params):
        if path.endswith('/orders-pending'):
            return rows[100:] if params.get('after')==rows[99]['ordId'] else rows[:100]
        return original(path,**params)
    ex.get=get
    assert book.reconcile()
    assert not book.snapshot()['blockers'] and 'external_activity' not in book.snapshot()
    assert book.preview(ticket(leverage=20))['plan']

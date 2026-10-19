"""GTC and scale-in lifecycle acceptance with a local stateful exchange."""
import copy
import uuid
from decimal import Decimal as D
import pytest
from test_live_book import Exchange, quote, ticket, enter
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.okx_execution import ExchangeUncertain
from yoyo.discipline_app.policy import Rejected


class ManagedExchange(Exchange):
    def __init__(self, clock):
        super().__init__(clock)
        self.algo_timeout=False
    def post(self,path,payload):
        if path.endswith('/order') and payload.get('attachAlgoOrds'):
            previous=copy.deepcopy(self.positions)
            if payload['ordType']=='limit':
                self.writes.append((path,copy.deepcopy(payload)))
                row=dict(payload,ordId=str(len(self.orders)+1),state='live',accFillSz='0',avgPx='',fee='0',feeCcy='USDT',cTime=str(int(self.clock()*1000)))
                self.orders[row['clOrdId']]=row
                return [dict(sCode='0',ordId=row['ordId'])]
            result=super().post(path,payload)
            if previous:
                old=previous[0];new=self.positions[-1]
                old['avgPx']=str((D(old['avgPx'])*D(old['pos'])+D(new['avgPx'])*D(new['pos']))/(D(old['pos'])+D(new['pos'])))
                old['pos']=str(D(old['pos'])+D(new['pos']));old['realizedPnl']=str(D(old['realizedPnl'])+D(new['realizedPnl']))
                self.positions=[old]
            return result
        if path.endswith('/order-algo'):
            self.writes.append((path,copy.deepcopy(payload)))
            row=dict(payload,algoId=str(len(self.algos)+1),state='live')
            self.algos[row['algoClOrdId']]=row
            if self.algo_timeout:
                self.algo_timeout=False
                raise ExchangeUncertain('fixture timeout after stop accepted')
            return [dict(sCode='0',algoId=row['algoId'])]
        if path.endswith('/cancel-order'):
            self.writes.append((path,copy.deepcopy(payload)))
            row=self.orders[payload['clOrdId']]
            row['state']='canceled'
            return [dict(sCode='0',ordId=row['ordId'])]
        return super().post(path,payload)
    def fill(self,cl_id,size,price='100'):
        row=self.orders[cl_id];previous=D(row['accFillSz']);size=D(size);px=D(price)
        average=(previous*D(row['avgPx'] or '0')+(size-previous)*px)/size
        row.update(accFillSz=str(size),avgPx=str(average),fee=str(-size*D('.01')*px*D('.0005')),state='filled' if size==D(row['sz']) else 'partially_filled')
        if self.positions:
            pos=self.positions[0];total=D(pos['pos'])+(size-previous)
            pos['avgPx']=str((D(pos['avgPx'])*D(pos['pos'])+px*(size-previous))/total);pos['pos']=str(total)
        else:
            pos=dict(instId=row['instId'],posSide=row['posSide'],pos=str(size-previous),mgnMode='cross',avgPx=price,posId='pos-1',realizedPnl=row['fee']);self.positions=[pos]
        if row['state']=='filled':
            for spec in row['attachAlgoOrds']:
                self.algos[spec['attachAlgoClOrdId']]=dict(spec,algoClOrdId=spec['attachAlgoClOrdId'],algoId=str(len(self.algos)+1),state='live',instId=row['instId'],posSide=row['posSide'],side='sell',sz=spec.get('sz',row['sz']))


@pytest.fixture
def managed(tmp_path):
    now=[1792332000.0];clock=lambda:now[0]
    ex=ManagedExchange(clock);book=LiveBook(tmp_path/'book.db',ex,clock)
    quote(book);assert book.reconcile()
    return book,ex,now


def test_gtc_waits_and_zero_fill_cancel_refunds_once(managed):
    book,ex,_=managed
    result,key,_=enter(book,ticket(kind='limit',price='99'))
    assert result['status']=='pending'
    assert list(ex.orders.values())[0]['ordType']=='limit'
    assert book.snapshot()['daily']['entries']==1
    assert book.cancel(uuid.uuid4().hex,key)['status']=='cancelled'
    assert book.snapshot()['daily']['entries']==0
    book.cancel(uuid.uuid4().hex,key);book.reconcile()
    assert book.snapshot()['daily']['entries']==0
    assert len([x for x in ex.writes if x[0].endswith('cancel-order')])==1


def test_partial_gtc_has_stop_then_final_fill_rebases_once(managed):
    book,ex,_=managed
    _,key,_=enter(book,ticket(kind='limit',price='100'))
    order=next(iter(ex.orders.values()));half=D(order['sz'])//2
    ex.fill(order['clOrdId'],half)
    assert book.reconcile(),book.snapshot()['private_error']
    pos=book.snapshot()['positions'][0]
    assert D(pos['remaining'])==half*D('.01')
    assert pos['protection']=='exchange-verified'
    assert book.snapshot()['pending']
    assert book.snapshot()['daily']['entries']==1
    ex.fill(order['clOrdId'],order['sz'])
    assert book.reconcile(),book.snapshot()['private_error']
    assert len(book.snapshot()['positions'])==1
    assert not book.snapshot()['pending']
    assert D(book.snapshot()['positions'][0]['quantity'])==D(order['sz'])*D('.01')
    count=len(ex.writes);assert book.reconcile();assert len(ex.writes)==count


def test_scale_in_replaces_protection_only_after_new_stop_is_verified(managed):
    book,ex,_=managed
    _,root,_=enter(book)
    old=book.snapshot()['positions'][0]
    _,child,_=enter(book)
    state=book.snapshot()
    assert not state['private_error'],state['private_error']
    assert len(state['positions'])==1 and state['positions'][0]['id']==root
    assert D(state['positions'][0]['quantity'])>D(old['quantity'])
    assert state['positions'][0]['stop']==old['stop']
    assert D(state['positions'][0]['risk'])<=D(state['equity'])*D('.05')
    assert state['daily']['entries']==2
    new_stop=next(i for i,(path,payload) in enumerate(ex.writes) if path.endswith('order-algo') and 'slTriggerPx' in payload)
    cancel=next(i for i,(path,_) in enumerate(ex.writes) if path.endswith('cancel-algos'))
    assert new_stop<cancel
    count=len(ex.writes);assert book.reconcile();assert len(ex.writes)==count


def test_ambiguous_replacement_stop_is_not_sent_twice(managed):
    book,ex,_=managed
    enter(book);ex.algo_timeout=True
    result,_,_=enter(book)
    assert book.snapshot()['private_error']
    assert book.reconcile(),book.snapshot()['private_error']
    stops=[p for path,p in ex.writes if path.endswith('order-algo') and 'slTriggerPx' in p]
    assert len(stops)==1
    assert book.snapshot()['positions'][0]['protection']=='exchange-verified'


def test_partial_cancel_does_not_refund_opening(managed):
    book,ex,_=managed
    _,key,_=enter(book,ticket(kind='limit',price='100'))
    order=next(iter(ex.orders.values()));ex.fill(order['clOrdId'],'10')
    assert book.reconcile(),book.snapshot()['private_error']
    book.cancel(uuid.uuid4().hex,key)
    assert book.snapshot()['daily']['entries']==1
    assert len(book.snapshot()['positions'])==1


def test_restart_recovers_ambiguous_generation_and_keeps_one_position(managed):
    book,ex,now=managed
    _,root,_=enter(book);ex.algo_timeout=True
    _,child,_=enter(book)
    reopened=LiveBook(book.path,ex,lambda:now[0])
    assert reopened.reconcile(),reopened.snapshot()['private_error']
    assert len(reopened.snapshot()['positions'])==1
    assert reopened.snapshot()['positions'][0]['id']==root
    assert len([p for path,p in ex.writes if path.endswith('order-algo') and 'slTriggerPx' in p])==1


def test_merged_close_settles_once_with_total_entered_contracts(managed):
    book,ex,now=managed
    _,root,_=enter(book);enter(book)
    total=sum(D(o['sz']) for o in ex.orders.values() if o.get('attachAlgoOrds'))
    ex.positions=[]
    ex.history=[dict(instId='BTC-USDT-SWAP',posSide='long',posId='pos-1',type='2',uTime=str(int(now[0]*1000)),closeTotalPos=str(total),realizedPnl='3',fee='-.1')]
    for _ in range(5):book.reconcile()
    assert not book.snapshot()['positions']
    assert len(book.snapshot()['closed'])==1
    assert book.snapshot()['discipline']['entries_bonus']==1
    book.reconcile()
    assert book.snapshot()['discipline']['entries_bonus']==1


def test_partial_fill_stop_covers_remaining_limit_quantity(managed):
    book,ex,_=managed
    _,key,_=enter(book,ticket(kind='limit',price='100'))
    order=next(iter(ex.orders.values()));ex.fill(order['clOrdId'],'10')
    assert book.reconcile(),book.snapshot()['private_error']
    stop=next(row for row in ex.algos.values() if row.get('slTriggerPx') and row['state']=='live')
    assert D(stop['sz'])==D(order['sz'])
    pending=book.snapshot()['pending'][0]
    assert D(pending['plan']['quantity'])==(D(order['sz'])-10)*D('.01')


def test_fill_closed_before_first_sync_recovers_from_history_once(managed):
    book,ex,now=managed
    _,key,_=enter(book,ticket(kind='limit',price='100'))
    order=next(iter(ex.orders.values()));ex.fill(order['clOrdId'],order['sz'])
    ex.positions=[]
    ex.history=[dict(instId='BTC-USDT-SWAP',posSide='long',posId='pos-1',type='2',
        uTime=str(int(now[0]*1000)),closeTotalPos=order['sz'],openAvgPx='100',realizedPnl='-3',fee='-.1')]
    for _ in range(6):book.reconcile()
    state=book.snapshot()
    assert not state['positions'] and not state['pending']
    assert len(state['closed'])==1 and state['daily']['entries']==1
    closed=state['closed'][0]
    expected=D(closed['quantity'])*(D('100')-D(closed['stop'])+D('100')*D('.0015'))-D(order['fee'])
    assert D(closed['risk'])==expected
    assert not state['private_error']
    assert not any(path.endswith('order-algo') for path,_ in ex.writes)
    count=len(ex.writes);book.reconcile()
    assert len(ex.writes)==count and len(book.snapshot()['closed'])==1


def test_unrecorded_partial_fill_flat_cancels_remainder_before_history(managed):
    book,ex,now=managed
    _,key,_=enter(book,ticket(kind='limit',price='100'))
    order=next(iter(ex.orders.values()));ex.fill(order['clOrdId'],'10');ex.positions=[]
    assert not book.reconcile()
    assert order['state']=='canceled'
    with book.read_transaction() as (_,state):
        assert state['intents'][key]['observed_fill']['accFillSz']=='10'
    reopened=LiveBook(book.path,ex,lambda:now[0])
    ex.history=[dict(instId='BTC-USDT-SWAP',posSide='long',posId='pos-1',type='2',
        uTime=str(int(now[0]*1000)),closeTotalPos='10',openAvgPx='100',realizedPnl='-1',fee='-.1')]
    assert reopened.reconcile(),reopened.snapshot()['private_error']
    assert len(reopened.snapshot()['closed'])==1
    assert reopened.snapshot()['daily']['entries']==1
    assert len([x for x in ex.writes if x[0].endswith('cancel-order')])==1


def test_flat_scale_in_cannot_reopen_a_missing_parent(managed):
    book,ex,now=managed
    _,root,_=enter(book)
    _,child,_=enter(book,ticket(kind='limit',price='100'))
    order=list(ex.orders.values())[-1];ex.fill(order['clOrdId'],order['sz']);ex.positions=[]
    with book.transaction() as (_,state):
        state['positions']=[]
        state['intents'][root]['phase']='closed'
    assert not book.reconcile()
    assert not book.snapshot()['positions'] and not book.snapshot()['closed']
    with book.read_transaction() as (_,state):
        assert not state['intents'][child].get('applied_size')


def test_nonpositive_recovered_r_does_not_break_cash_statistics():
    from yoyo.discipline_app.statistics import trade_summary
    summary=trade_summary([dict(pnl='2',risk='0')])
    assert summary['net_pnl']=='2' and summary['average_net_r'] is None


def test_verified_limit_allows_another_symbol_with_shared_reservation(managed):
    book,ex,_=managed
    _,key,_=enter(book,ticket(kind='limit',price='100'))
    assert book.snapshot()['blockers']==[]
    second=ticket(symbol='ETH-USDT-SWAP',kind='limit',price='100')
    result,other,_=enter(book,second)
    assert result['status']=='pending' and key!=other
    assert len(book.snapshot()['pending'])==2
    assert book.snapshot()['daily']['entries']==2
    with book.read_transaction() as (_,state):
        used=sum(D(p['plan']['margin']) for p in state['pending'])
        assert used<=D(state['account_equity'])*D('.1')
    with pytest.raises(Rejected,match='同币种已有挂单'):
        book.preview(second)


@pytest.mark.parametrize('phase',['prepared','attempted'])
def test_unknown_limit_receipt_still_blocks_other_symbol(managed,phase):
    book,ex,_=managed
    _,key,_=enter(book,ticket(kind='limit',price='100'))
    with book.transaction() as (db,_):
        db.execute('UPDATE exchange_commands SET phase=? WHERE id=?',(phase,key))
    with book.transaction() as (_,state):
        with pytest.raises(Rejected,match='委托结果尚未核对'):
            book.preview_in(state,ticket(symbol='ETH-USDT-SWAP'))


def test_verified_limit_does_not_bypass_daily_or_position_limits(managed):
    book,ex,_=managed
    enter(book,ticket(kind='limit',price='100'))
    with book.transaction() as (_,state):
        state['daily']['entries']=book.entry_limit(state)
        with pytest.raises(Rejected,match='今日开仓次数'):
            book.preview_in(state,ticket(symbol='ETH-USDT-SWAP'))
        state['daily']['entries']=1
        state['policy']['max_positions']=1
        with pytest.raises(Rejected,match='持仓与挂单数量'):
            book.preview_in(state,ticket(symbol='ETH-USDT-SWAP'))


def test_legacy_verified_limit_does_not_bypass_exhausted_major_margin(managed):
    book,ex,_=managed
    with book.transaction() as (_,state):
        state['policy']['major_allowance_scope']='shared'
    enter(book,ticket(kind='limit',price='100'))
    with book.transaction() as (_,state):
        state['pending'][0]['plan']['margin']=str(book.equity(state)*D('.1'))
        with pytest.raises(Rejected):
            book.preview_in(state,ticket(symbol='ETH-USDT-SWAP'))


def test_partially_filled_limit_needs_verified_position_protection(managed):
    book,ex,_=managed
    enter(book,ticket(kind='limit',price='100'))
    order=next(iter(ex.orders.values()))
    ex.fill(order['clOrdId'],'10')
    assert book.reconcile()
    with book.transaction() as (_,state):
        assert state['positions'][0]['protection']=='exchange-verified'
        assert book.preview_in(state,ticket(symbol='ETH-USDT-SWAP'))['quantity']
        state['positions'][0]['protection']='pending'
        with pytest.raises(Rejected,match='持仓保护尚未核对'):
            book.preview_in(state,ticket(symbol='ETH-USDT-SWAP'))

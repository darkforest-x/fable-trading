"""Live orchestration acceptance against a stateful fake OKX; never real orders."""
import copy
from decimal import Decimal as D
import uuid
import pytest
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.okx_execution import ExchangeRejected, ExchangeUncertain
from yoyo.discipline_app.policy import Policy, Rejected, take_profit_targets


class Exchange:
    environment='okx_live'
    write_enabled=True
    def __init__(self,clock):
        self.clock=clock
        self.orders={}; self.algos={}; self.positions=[]; self.history=[]; self.writes=[]
        self.leverage='100'; self.equity='1000'; self.available=None; self.fee='-0.0005'; self.timeout=False
        self.execution_price='100'
        self.reject=False; self.cancel_entry=False; self.foreign=[]
        self.reject_amend_values=set()
        self.amend_timeout_values=set(); self.algo_read_failures=0
        self.position_tiers={}
    def get(self,path,**params):
        if path.endswith('/config'): return [dict(acctLv='2',posMode='long_short_mode',perm='read_only,trade',uid='fixture-account')]
        if path.endswith('/balance'):
            available = self.equity if self.available is None else self.available
            return [dict(details=[dict(ccy='USDT',eq=self.equity,cashBal=self.equity,availEq=available)])]
        if path.endswith('/positions'): return copy.deepcopy(self.positions)
        if path.endswith('/positions-history'): return copy.deepcopy(self.history)
        if path.endswith('/orders-pending'): return copy.deepcopy(self.foreign)
        if path.endswith('/orders-algo-pending'): return copy.deepcopy([x for x in self.algos.values() if x['state']=='live']) if params['ordType']=='conditional' else []
        if path.endswith('/trade-fee'): return [dict(taker='',feeGroup=[dict(groupId='2',taker=self.fee)])]
        if path.endswith('/leverage-info'): return [dict(lever=self.leverage)]
        if path.endswith('/position-tiers'):
            family=params['instFamily']
            return copy.deepcopy(self.position_tiers.get(family,[dict(instFamily=family,minSz='0',maxSz='999999999',maxLever='100')]))
        if path.endswith('/order'):
            row=next((x for x in self.orders.values() if x['clOrdId']==params.get('clOrdId') or x['ordId']==params.get('ordId')),None)
            if not row: raise ExchangeRejected('51603')
            return [copy.deepcopy(row)]
        if path.endswith('/order-algo'):
            if self.algo_read_failures:
                self.algo_read_failures -= 1
                raise ExchangeUncertain('fixture temporary algo read failure')
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
            order=dict(payload,ordId=str(len(self.orders)+1),state='filled',accFillSz=payload['sz'],avgPx=self.execution_price,fee='-0.05',feeCcy='USDT',cTime=str(int(self.clock()*1000)))
            if self.cancel_entry:
                order.update(state='canceled',accFillSz='0')
            elif payload.get('attachAlgoOrds'):
                self.positions.append(dict(instId=payload['instId'],posSide=payload['posSide'],pos=payload['sz'],mgnMode='cross',avgPx=self.execution_price,posId='pos-1',realizedPnl='-0.05'))
                for child in payload['attachAlgoOrds']:
                    algo=dict(child,algoId=str(len(self.algos)+1),algoClOrdId=child['attachAlgoClOrdId'],state='live',instId=payload['instId'],posSide=payload['posSide'],side='sell' if payload['side']=='buy' else 'buy',sz=child.get('sz',payload['sz']))
                    self.algos[algo['algoClOrdId']]=algo
            self.orders[payload['clOrdId']]=order
            if self.timeout: raise ExchangeUncertain('timeout after exchange fill')
            return [dict(sCode='0',ordId=order['ordId'])]
        if path.endswith('/amend-algos'):
            if payload.get('newTpTriggerPx') in self.reject_amend_values:
                self.reject_amend_values.remove(payload['newTpTriggerPx'])
                raise ExchangeRejected('51000')
            algo=next(x for x in self.algos.values() if x['algoId']==payload['algoId'])
            for new,old in [('newSlTriggerPx','slTriggerPx'),('newTpTriggerPx','tpTriggerPx')]:
                if new in payload: algo[old]=payload[new]
            if payload.get('newTpTriggerPx') in self.amend_timeout_values:
                self.amend_timeout_values.remove(payload['newTpTriggerPx'])
                raise ExchangeUncertain('fixture timeout after TP amend')
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
    book=LiveBook(tmp_path/'live.sqlite3',exchange,clock,initial_policy=Policy())
    quote(book)
    assert book.reconcile(), book.snapshot()['private_error']
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


def test_fok_rebuilds_automatic_targets_from_actual_fill_without_rejecting_quote_tick_drift(live):
    book,exchange,_=live
    request=ticket()
    preview=book.preview(request)
    reviewed=preview['plan']['take_profit_prices']
    exchange.execution_price='99.99'  # favorable one-tick fill inside the FOK limit

    receipt=book.enter(uuid.uuid4().hex,request,preview['policy_revision'],preview['preview_id'],True)

    assert receipt['status']=='filled'
    position=book.snapshot()['positions'][0]
    expected=take_profit_targets(position['entry'],position['quantity'],position['risk'],
        position['entry_fee'],Policy.parse(position['policy']),position['side'],'.01',
        position['take_profit_overrides'])
    assert position['risk'] == preview['plan']['risk']
    assert position['take_profit_prices'] == expected
    assert position['take_profit_prices']!=reviewed
    active_targets=sorted((row['tpTriggerPx'] for row in exchange.algos.values() if row.get('tpTriggerPx')),
        key=D)
    assert active_targets==sorted(expected,key=D)


def test_timeout_with_actual_fill_reconciles_without_duplicate(live):
    book,exchange,_=live
    exchange.timeout=True
    receipt,_,_=enter(book)
    assert receipt['status']=='filled'
    assert book.outbox.unresolved()==[]
    assert len([x for x in exchange.writes if x[0].endswith('/order')])==1


def test_fee_and_expired_preview_reject_but_other_contract_external_position_does_not(live):
    book,exchange,now=live
    exchange.fee='-0.001'
    with pytest.raises(Rejected,match='手续费'): book.preview(ticket())
    exchange.fee='-0.0005'
    exchange.positions=[dict(instId='ETH-USDT-SWAP',posSide='long',pos='1')]
    assert book.preview(ticket())['plan']
    assert book.preview(ticket(symbol='ETH-USDT-SWAP'))['plan']
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
    assert not state['closed'][0].get('emergency_close_submitted')
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
    exchange.positions = []
    exchange.history = [dict(posId='pos-1', posSide='long', uTime=str(int(now[0]*1000)),
        type='2', closeTotalPos=closes[0]['sz'], realizedPnl='-1', fee='-0.25')]
    for _ in range(4):
        book.reconcile()
    assert book.snapshot()['closed'][0]['emergency_close_submitted']


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


@pytest.mark.parametrize('side', ['long', 'short'])
def test_manual_tp2_change_preserves_tp1_stop_and_survives_reconcile(live, side):
    book, exchange, _ = live
    _, identity, _ = enter(book, ticket(side=side, stop='95' if side == 'long' else '105'))
    before = book.snapshot()['positions'][0]
    original = list(before['take_profit_prices'])
    direction = 1 if side == 'long' else -1
    requested = str(D(original[1]) + D(direction * 3))
    key = uuid.uuid4().hex

    result = book.change_protection(key, identity, take_profits=[None, requested])
    current = book.snapshot()['positions'][0]
    assert result['status'] == 'verified'
    assert current['take_profit_prices'] == [original[0], requested]
    assert current['stop'] == before['stop']
    tp_algos = sorted((row for row in exchange.algos.values() if 'tpTriggerPx' in row),
                      key=lambda row: float(row['tpTriggerPx']), reverse=side == 'short')
    assert [row['tpTriggerPx'] for row in tp_algos] == [original[0], requested]
    amend = next(payload for path, payload in reversed(exchange.writes) if path.endswith('/amend-algos'))
    assert amend['newTpTriggerPx'] == requested
    assert 'newSlTriggerPx' not in amend and 'newSz' not in amend
    write_count = len(exchange.writes)
    assert book.reconcile()
    assert len(exchange.writes) == write_count
    assert book.change_protection(key, identity, take_profits=[None, requested])['status'] == 'verified'
    assert len(exchange.writes) == write_count


@pytest.mark.parametrize(('side', 'new_stop'), [('long', '97'), ('short', '103')])
def test_manual_stop_only_keeps_both_take_profit_prices(live, side, new_stop):
    book, exchange, _ = live
    _, identity, _ = enter(book, ticket(side=side, stop='95' if side == 'long' else '105'))
    before = book.snapshot()['positions'][0]
    prices = list(before['take_profit_prices'])
    result = book.change_protection(uuid.uuid4().hex, identity, stop=new_stop)
    current = book.snapshot()['positions'][0]
    assert result['status'] == 'verified'
    assert current['stop'] == new_stop
    assert current['take_profit_prices'] == prices
    assert [a['tpTriggerPx'] for a in exchange.algos.values() if 'tpTriggerPx' in a] == prices
    assert next(a for a in exchange.algos.values() if 'slTriggerPx' in a)['slTriggerPx'] == new_stop


def test_completed_tp1_keeps_its_price_and_tp2_remains_editable(live):
    book, exchange, _ = live
    _, identity, _ = enter(book)
    original = list(book.snapshot()['positions'][0]['take_profit_prices'])
    first = next(row for row in exchange.algos.values()
        if row.get('tpTriggerPx') == original[0])
    first['state'] = 'effective'
    first['ordIdList'] = ['first-tp']
    exchange.orders['first-tp'] = dict(ordId='first-tp', clOrdId='first-tp', accFillSz=first['sz'])
    exchange.positions[0]['pos'] = str(D(exchange.positions[0]['pos']) - D(first['sz']))
    assert book.reconcile(), book.snapshot()['private_error']
    position = book.snapshot()['positions'][0]
    assert position['stages'] == [0]
    requested = str(D(original[1]) + D('3'))

    book.change_protection(uuid.uuid4().hex, identity, take_profits=[None, requested])
    position = book.snapshot()['positions'][0]
    assert position['take_profit_prices'] == [original[0], requested]
    assert position['take_profit_editable'] == [False, True]


def test_invalid_protection_changes_write_nothing(live):
    book, exchange, _ = live
    _, identity, _ = enter(book)
    before = len(exchange.writes)
    with pytest.raises(Rejected, match='收紧'):
        book.change_protection(uuid.uuid4().hex, identity, stop='94')
    with pytest.raises(Rejected, match='有利一侧'):
        book.change_protection(uuid.uuid4().hex, identity, take_profits=['99', None])
    with pytest.raises(Rejected, match='过大或精度过高'):
        book.change_protection(uuid.uuid4().hex, identity, take_profits=['1e9999', None])
    assert len(exchange.writes) == before


def test_newer_tighter_exchange_stop_survives_manual_submission(live, monkeypatch):
    book, exchange, _ = live
    _, identity, _ = enter(book)
    before = len(exchange.writes)
    resume = book._resume_protection_update

    def external_update_then_resume(position_id, request_id):
        next(row for row in exchange.algos.values() if row.get('slTriggerPx'))['slTriggerPx'] = '97'
        return resume(position_id, request_id)

    monkeypatch.setattr(book, '_resume_protection_update', external_update_then_resume)
    with pytest.raises(Rejected):
        book.change_protection(uuid.uuid4().hex, identity, stop='96')
    assert len(exchange.writes) == before
    assert book.reconcile()
    assert book.snapshot()['positions'][0]['stop'] == '97'


def test_ambiguous_tp_amend_waits_for_readback_without_close_or_repeat(live):
    book, exchange, now = live
    _, identity, _ = enter(book)
    old = list(book.snapshot()['positions'][0]['take_profit_prices'])
    requested = str(D(old[1]) + D('3'))
    key = uuid.uuid4().hex
    exchange.amend_timeout_values.add(requested)
    with pytest.raises(ExchangeUncertain):
        book.change_protection(key, identity, take_profits=[None, requested])
    before = len(exchange.writes)
    exchange.algo_read_failures = 1
    now[0] += 21
    quote(book)
    assert not book.reconcile()
    assert len(exchange.writes) == before
    assert not [payload for _, payload in exchange.writes if payload.get('ordType') == 'market']
    assert book.reconcile()
    position = book.snapshot()['positions'][0]
    assert position['take_profit_prices'] == [old[0], requested]
    assert position['protection_edit_pending'] is False
    assert len(exchange.writes) == before


def test_external_stop_cancellation_is_seen_even_while_tp_amendment_readback_times_out(live):
    book, exchange, now = live
    _, identity, _ = enter(book)
    requested = str(D(book.snapshot()['positions'][0]['take_profit_prices'][1]) + 3)
    exchange.amend_timeout_values.add(requested)
    with pytest.raises(ExchangeUncertain):
        book.change_protection(uuid.uuid4().hex, identity, take_profits=[None, requested])
    stop = next(row for row in exchange.algos.values() if row.get('slTriggerPx'))
    stop['state'] = 'canceled'
    exchange.algo_read_failures = 1
    now[0] += 21
    quote(book)
    assert not book.reconcile()
    closes = [row for _, row in exchange.writes if row.get('ordType') == 'market']
    assert len(closes) == 1 and closes[0]['sz'] == stop['sz']


def test_protection_api_requires_live_confirmation_and_get_is_read_only(live):
    from fastapi.testclient import TestClient
    from yoyo.discipline_app.api import create_app
    book, exchange, _ = live
    _, identity, _ = enter(book)
    prices = list(book.snapshot()['positions'][0]['take_profit_prices'])
    token = 'fixture-access-token-at-least-32-characters'
    with TestClient(create_app(book, token, market_enabled=False)) as client:
        headers = {'Authorization': 'Bearer ' + token}
        before = len(exchange.writes)
        state = client.get('/v1/state', headers=headers)
        assert state.status_code == 200
        assert state.json()['positions'][0]['take_profit_prices'] == prices
        assert len(exchange.writes) == before
        requested = str(D(prices[1]) + D('3'))
        body = dict(request_id=uuid.uuid4().hex, confirmed_live=False,
            take_profits=[None, requested])
        response = client.post(f'/v1/positions/{identity}/protection', headers=headers, json=body)
        assert response.status_code == 409
        assert len(exchange.writes) == before
        body.update(request_id=uuid.uuid4().hex, confirmed_live=True)
        response = client.post(f'/v1/positions/{identity}/protection', headers=headers, json=body)
        assert response.status_code == 200
        assert response.json()['take_profit_prices'] == [prices[0], requested]


def test_partial_tp_amend_rejection_reports_verified_fields(live):
    book, exchange, _ = live
    _, identity, _ = enter(book)
    before = book.snapshot()['positions'][0]
    writes_before = len(exchange.writes)
    first = str(D(before['take_profit_prices'][0]) + D('1'))
    second = str(D(before['take_profit_prices'][1]) + D('3'))
    exchange.reject_amend_values.add(first)

    result = book.change_protection(uuid.uuid4().hex, identity, take_profits=[first, second])
    position = book.snapshot()['positions'][0]
    assert result['status'] == 'partial'
    assert result['updated'] == ['tp1'] and result['rejected'] == ['tp0']
    assert position['take_profit_prices'] == [before['take_profit_prices'][0], second]
    assert position['take_profit_overrides'][0] is None
    assert position['take_profit_overrides'][1] == second
    amend_prices = [payload['newTpTriggerPx'] for path, payload in exchange.writes[writes_before:]
        if path.endswith('/amend-algos') and 'newTpTriggerPx' in payload]
    assert amend_prices == [second, first]
    assert position['protection_edit_pending'] is False


def test_rejected_outward_tp_does_not_dispatch_dependent_inner_tier(live):
    book, exchange, _ = live
    _, identity, _ = enter(book)
    before = book.snapshot()['positions'][0]
    writes_before = len(exchange.writes)
    first = str(D(before['take_profit_prices'][1]) + D('1'))
    second = str(D(before['take_profit_prices'][1]) + D('3'))
    exchange.reject_amend_values.add(second)

    with pytest.raises(Rejected, match='交易所拒绝保护修改'):
        book.change_protection(uuid.uuid4().hex, identity, take_profits=[first, second])
    position = book.snapshot()['positions'][0]
    amend_prices = [payload['newTpTriggerPx'] for path, payload in exchange.writes[writes_before:]
        if path.endswith('/amend-algos') and 'newTpTriggerPx' in payload]
    assert amend_prices == [second]
    assert position['take_profit_prices'] == before['take_profit_prices']
    assert position['take_profit_overrides'] == before['take_profit_overrides']
    assert position['protection_edit_pending'] is False


def test_bad_tp_recovery_keeps_verified_stop_without_emergency_close(live):
    book, exchange, _ = live
    _, identity, _ = enter(book)
    stop_id = next(row['algoClOrdId'] for row in exchange.algos.values()
        if row.get('slTriggerPx'))
    with book.transaction() as (_, state):
        position = next(row for row in state['positions'] if row['id'] == identity)
        position['take_profit_overrides'] = ['1', None]  # invalid for a long position
    writes_before = len(exchange.writes)

    assert not book.reconcile()
    position = book.snapshot()['positions'][0]
    assert position['take_profit_error']
    assert position['protection'] == 'awaiting-exchange'
    assert exchange.algos[stop_id]['state'] == 'live'
    assert not [payload for path, payload in exchange.writes[writes_before:]
        if path.endswith('/order') and payload.get('ordType') == 'market']

"""Management contract checks use only the synthetic exchange, never live keys."""
from copy import deepcopy
import pytest
from test_free_manual import h, confirmed, SYMBOL
from yoyo.okx_execution.free_manual import consume as enter
from yoyo.okx_execution.free_management import Management, consume
from yoyo.okx_execution.client import TransportError, APIError


def setup(h, *, pending=False, protection=True, side='long'):
    v=h.venue
    if pending:v.order_state='live'
    d=confirmed(h, side=side, order_type='limit' if pending else 'market', limit_price='100' if pending else None,
                stop_loss=('90' if side=='long' else '110') if protection else None,
                take_profit=('120' if side=='long' else '80') if protection else None)
    enter(h.engine,h.book,entries=True)
    d=h.book.get(d['id']);v.posts.clear();v.close_order=None;v.fail_management=None;v.accept_management=True
    if not pending:v.positions=[dict(instId=SYMBOL,pos=d['filled_size'],posSide=side,mgnMode='cross',posId='p1',avgPx='100')]
    if v.algo:v.algo['algoId']='algo1'
    get,post=v.get,v.post
    def read(path,**q):
        if path.endswith('/order') and q.get('clOrdId')!=d['client_order_id']:
            if v.close_order:return [deepcopy(v.close_order)]
            raise APIError('51603')
        if path.endswith('/orders-pending'):return [deepcopy(v.order)] if v.order['state'] in ('live','partially_filled') else []
        if path.endswith('/orders-algo-pending'):return [deepcopy(v.algo)] if v.algo and v.algo['state']=='live' and q['ordType']=='oco' else []
        return get(path,**q)
    def write(path,body):
        v.posts.append((path,deepcopy(body)))
        if v.accept_management:
            if path.endswith('/amend-algos'):v.algo.update(slTriggerPx=body['newSlTriggerPx'],slOrdPx=body['newSlOrdPx'])
            elif path.endswith('/cancel-order'):v.order['state']='canceled'
            elif path.endswith('/cancel-algos'):v.algo['state']='canceled'
            elif path.endswith('/order-algo'):v.algo=dict(body,algoId='newalgo',state='live')
            elif path.endswith('/order'):v.close_order=dict(body,ordId='close1',accFillSz=body['sz'],state='filled');v.positions=[]
            else:raise AssertionError(path)
        if v.fail_management==path:raise TransportError('lost reply')
        return [dict(sCode='0')]
    v.get,v.post=read,write
    return d,Management(h.runtime)


def start(h,book,d,action,stop=None):
    q=book.preview(h.engine,d['id'],action,stop)
    assert h.venue.posts==[]
    consume(h.engine,book)
    assert h.venue.posts==[]
    book.confirm(q['id'],h.now[0]);return q


@pytest.mark.parametrize('side,stop',[('long','95'),('short','105')])
def test_stop_amend_preserves_tp_and_confirms_exact_receipt(h,side,stop):
    d,b=setup(h,side=side);tp=h.venue.algo['tpTriggerPx'];q=start(h,b,d,'stop',stop)
    consume(h.engine,b)
    assert b.get(q['id'])['state']=='done'
    assert h.venue.algo['tpTriggerPx']==tp
    assert h.book.get(d['id'])['stop_loss']==stop
    assert h.venue.posts[0][1]['cxlOnFail'] is False
    assert not any(k.startswith('newTp') for k in h.venue.posts[0][1])


def test_missing_stop_gets_a_bound_native_stop(h):
    d,b=setup(h,protection=False);q=start(h,b,d,'stop','95');consume(h.engine,b)
    assert b.get(q['id'])['state']=='done'
    assert h.venue.posts[0][0].endswith('/order-algo')
    assert h.book.get(d['id'])['management_algo_client_id']==h.venue.algo['algoClOrdId']


@pytest.mark.parametrize('protected',[True,False])
def test_close_is_exact_reducing_side_and_cleans_only_its_protection(h,protected):
    d,b=setup(h,protection=protected);q=start(h,b,d,'close');consume(h.engine,b)
    assert b.get(q['id'])['state']=='done'
    body=h.venue.posts[0][1]
    assert body['side']=='sell' and body['posSide']=='long' and body['sz']==d['filled_size']
    assert len(h.venue.posts)==(2 if protected else 1)
    assert h.book.get(d['id'])['management_closed_ms']==h.now[0]
    consume(h.engine,Management(h.runtime));assert len(h.venue.posts)==(2 if protected else 1)
    with pytest.raises(ValueError):b.preview(h.engine,d['id'],'close')


@pytest.mark.parametrize('action,path',[('stop','/api/v5/trade/amend-algos'),('close','/api/v5/trade/order'),('cancel','/api/v5/trade/cancel-order')])
@pytest.mark.parametrize('accept',[True,False])
def test_lost_response_or_crash_never_reposts(h,action,path,accept):
    d,b=setup(h,pending=action=='cancel');q=start(h,b,d,action,'95' if action=='stop' else None)
    h.venue.fail_management=path;h.venue.accept_management=accept
    consume(h.engine,b);h.venue.fail_management=None
    for _ in range(3):consume(h.engine,Management(h.runtime))
    assert sum(p==path for p,_ in h.venue.posts)==1
    assert b.get(q['id'])['state']==('done' if accept else 'processing')
    if not accept:
        with pytest.raises(ValueError,match='待核对'):b.preview(h.engine,d['id'],action,'95' if action=='stop' else None)


@pytest.mark.parametrize('change',['quantity','side','account','algo','expired'])
def test_revalidate_before_write_rejects_changed_position(h,change):
    d,b=setup(h);q=start(h,b,d,'close')
    if change=='quantity':h.venue.positions[0]['pos']='50'
    elif change=='side':h.venue.positions[0]['posSide']='short'
    elif change=='account':h.store.set_meta('account_uid','other')
    elif change=='algo':h.venue.algo['slTriggerPx']='92'
    else:h.now[0]+=60001
    consume(h.engine,b)
    assert b.get(q['id'])['state']=='rejected' and not h.venue.posts


@pytest.mark.parametrize('stop',['0','100','105','95.05'])
def test_bad_or_crossed_stop_never_confirms(h,stop):
    d,b=setup(h)
    with pytest.raises(ValueError):b.preview(h.engine,d['id'],'stop',stop)
    assert not h.venue.posts


def test_changed_tp_receipt_does_not_claim_success(h):
    d,b=setup(h);q=start(h,b,d,'stop','95');h.venue.fail_management='/api/v5/trade/amend-algos'
    consume(h.engine,b);h.venue.algo['tpTriggerPx']='130';consume(h.engine,b)
    assert b.get(q['id'])['state']=='processing'
    assert len(h.venue.posts)==1


def test_rejected_stop_retains_original_protection(h):
    d,b=setup(h);q=start(h,b,d,'stop','95')
    def reject(*args):raise APIError('bad')
    h.venue.post=reject;consume(h.engine,b)
    assert b.get(q['id'])['state']=='rejected' and h.venue.algo['slTriggerPx']=='90'


@pytest.mark.parametrize('accepted',[True,False])
def test_exchange_timeout_error_reconciles_instead_of_unlocking_retry(h,accepted):
    d,b=setup(h);q=start(h,b,d,'stop','95');write=h.venue.post
    def timeout(path,body):
        if accepted:write(path,body)
        else:h.venue.posts.append((path,deepcopy(body)))
        raise APIError('50004')
    h.venue.post=timeout
    for _ in range(3):consume(h.engine,Management(h.runtime))
    assert len(h.venue.posts)==1
    assert b.get(q['id'])['state']==('done' if accepted else 'processing')


def test_stop_receipt_with_changed_trigger_basis_stays_unresolved(h):
    d,b=setup(h);q=start(h,b,d,'stop','95')
    h.venue.fail_management='/api/v5/trade/amend-algos';consume(h.engine,b)
    h.venue.algo['slTriggerPxType']='mark';consume(h.engine,b)
    assert b.get(q['id'])['state']=='processing' and len(h.venue.posts)==1


def test_management_api_requires_same_origin_preview_and_explicit_ack(h):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yoyo.okx_execution.manual_api import Service, install
    from yoyo.okx_execution.free_management import REVISION
    d,b=setup(h);service=Service(SimpleNamespace(),h.runtime,engine=h.engine,clock=h.engine.clock,healthy=lambda:None)
    service.close_ready=lambda:None
    app=FastAPI();install(app,SimpleNamespace(),service=service)
    headers={'Origin':'http://testserver','X-Fable-Manual':'owner-confirmation','Sec-Fetch-Site':'same-origin'}
    payload={'ticket_id':d['id'],'action':'stop','stop':'95'}
    with TestClient(app) as c:
        assert c.post('/api/manual-live/free/manage/preview',json=payload).status_code==403
        assert c.post('/api/manual-live/free/manage/preview',json=payload,headers=headers).status_code==409
        h.store.set_meta('free_management_revision',REVISION)
        response=c.post('/api/manual-live/free/manage/preview',json=payload,headers=headers)
        assert response.status_code==200,response.text
        q=response.json();assert 'account_uid' not in q
        url='/api/manual-live/free/manage/'+q['id']+'/confirm'
        assert c.post(url,json={'acknowledgement':'CONFIRM-LIVE-CLOSE'},headers=headers).status_code==422
        assert c.post(url,json={'acknowledgement':'CONFIRM-FREE-MANAGEMENT'},headers=headers).json()['state']=='confirmed'
        assert not h.venue.posts
        consume(h.engine,b)
        assert c.get('/api/manual-live/free/manage/'+q['id']).json()['state']=='done'
        assert len(h.venue.posts)==1


def test_signal_management_routes_use_existing_lot_command_owner(h,monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yoyo.okx_execution.manual_api import Service, install
    from yoyo.okx_execution.manual_commands import Commands
    called=[]
    def preview(self,engine,lot_id,action,stop):
        called.append((lot_id,action,stop));return dict(id='b'*32,lot_id=lot_id,action=action,state='preview',account_uid='owner')
    monkeypatch.setattr(Commands,'preview',preview)
    service=Service(SimpleNamespace(),h.runtime,engine=h.engine,clock=h.engine.clock,healthy=lambda:None)
    service.close_ready=lambda:None
    app=FastAPI();install(app,SimpleNamespace(),service=service)
    headers={'Origin':'http://testserver','X-Fable-Manual':'owner-confirmation','Sec-Fetch-Site':'same-origin'}
    with TestClient(app) as c:
        r=c.post('/api/manual-live/free/manage/preview',json={'source':'signal','ticket_id':'a'*32,'action':'stop','stop':'95'},headers=headers)
        assert r.status_code==200 and r.json()['source']=='signal'
        assert called==[('a'*32,'stop','95')] and not h.venue.posts

"""Connected Pro contract tests with a fake monitor and isolated persistence."""
import asyncio
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from yoyo.monitor.pro.gateway import create_app, Workspace, Record
from yoyo.monitor.pro.paper_plans import Plans, Plan


def test_proxy_keeps_fixed_upstream_and_owner_headers(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'ok':True})
    with TestClient(create_app(state_path=tmp_path/'db',upstream_transport=httpx.MockTransport(handler))) as c:
        assert c.get('/pro/market').status_code==200
        assert 'data-pro-mode="connected"' in c.get('/pro/account').text
        assert 'data-pro-mode="reference"' in c.get('/pro/account?reference=true').text
        assert c.get('/api/not-a-public-route').status_code==404
        assert c.post('/api/manual-live/preview',json={}).status_code==403
        assert c.post('/api/manual-live/preview',json={},headers={'Origin':'https://evil.test'}).status_code==403
        assert not calls
        r=c.post('/api/manual-live/preview',json={'rr':3},headers={'Origin':'http://testserver','X-Fable-Manual':'owner-confirmation','Cookie':'private'})
        assert r.status_code==200 and len(calls)==1
        assert str(calls[0].url)=='http://127.0.0.1:8766/api/manual-live/preview'
        assert calls[0].headers['origin']=='http://127.0.0.1:8766'
        assert calls[0].headers['x-fable-manual']=='owner-confirmation'
        assert 'cookie' not in calls[0].headers


def test_workspace_prevents_lost_updates_and_path_escape(tmp_path):
    with TestClient(create_app(state_path=tmp_path/'db',upstream_transport=httpx.MockTransport(lambda r:httpx.Response(200,json={})))) as c:
        h={'Origin':'http://testserver'}
        assert c.get('/api/pro/workspace/reading').json()['revision']==0
        assert c.put('/api/pro/workspace/reading',json={'revision':0,'value':{'notes':['saved']}},headers=h).json()['revision']==1
        assert c.put('/api/pro/workspace/reading',json={'revision':0,'value':{}},headers=h).status_code==409
        assert c.get('/api/pro/workspace/reading').json()['value']=={'notes':['saved']}
        assert c.get('/api/pro/workspace/bad%5Cname').status_code==404


def spec(**kw):return Plan(request_id='a'*32,account_id='b'*32,symbol='BTC-USDT-SWAP',side='long',kind='twap',quantity='1',leverage=2,slices=2,**kw)


def test_plan_reuses_exact_intent_after_lost_response(tmp_path):
    async def run():
        posts=[]
        async def handler(request):
            if request.method=='GET':return httpx.Response(200,json={'id':'q1','ask':'100','bid':'99'})
            posts.append(json.loads(request.content))
            if len(posts)==1:raise httpx.ReadTimeout('lost')
            return httpx.Response(200,json={'fill':{'id':'f1'}})
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8766',transport=httpx.MockTransport(handler)) as client:
            store=Workspace(tmp_path/'db');p=Plans(store,client);v=p.create(spec())
            assert p.create(spec())['id']==v['id']
            with pytest.raises(httpx.ReadTimeout):await p.step(v)
            restored=Plans(store,client);await restored.step(restored.rows()[0])
            assert posts[0]==posts[1] and posts[0]['quantity']=='0.500000000000'
            r=restored.rows()[0];assert r['completed']==1
            restored.cancel(r['id']);await restored.step(restored.rows()[0])
            assert len(posts)==2 and restored.rows()[0]['state']=='canceled'
    asyncio.run(run())


def test_trigger_waits_for_correct_side_then_submits_once(tmp_path):
    async def run():
        ask=['99'];posts=[]
        def handler(r):
            if r.method=='GET':return httpx.Response(200,json={'id':'q','ask':ask[0],'bid':'98'})
            posts.append(r);return httpx.Response(200,json={'id':'fill'})
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8766',transport=httpx.MockTransport(handler)) as c:
            p=Plans(Workspace(tmp_path/'db'),c)
            v=p.create(Plan(request_id='x'*32,account_id='b'*32,symbol='BTC-USDT-SWAP',side='long',kind='trigger',quantity='1',leverage=1,trigger_price='100'))
            await p.step(v);assert not posts
            ask[0]='101';await p.step(v);await p.step(p.rows()[0]);assert len(posts)==1
    asyncio.run(run())

@pytest.mark.parametrize('override',[{'quantity':'0'},{'quantity':'0.000000000001'},{'stop_loss':'NaN'},{'leverage':3}])
def test_plan_invalid_terms_rejected(override):
    payload=spec().model_dump();payload.update(override)
    with pytest.raises(ValueError):Plan(**payload)


def test_iceberg_waits_for_fill_and_keeps_exact_total(tmp_path):
    async def run():
        posts=[];orders=[]
        def handler(r):
            if r.url.path.endswith('/quote'):
                return httpx.Response(200,json={'id':'q','ask':'100','bid':'99'})
            if r.method=='GET':return httpx.Response(200,json={'orders':orders})
            posts.append(json.loads(r.content))
            order={'id':str(len(posts)),'status':'pending'};orders.append(order)
            return httpx.Response(201,json={'order':order})
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8766',transport=httpx.MockTransport(handler)) as c:
            p=Plans(Workspace(tmp_path/'db'),c)
            args=spec().model_dump();args.update(kind='iceberg',slices=3,limit_price='90')
            v=p.create(Plan(**args));await p.step(v)
            v=p.rows()[0];v['next_ms']=0;p.update(v);await p.step(v)
            assert len(posts)==1
            orders[0]['status']='filled';await p.step(v)
            v=p.rows()[0];v['next_ms']=0;p.update(v);orders[1]['status']='filled';await p.step(v)
            from decimal import Decimal
            assert sum(Decimal(x['quantity']) for x in posts)==Decimal('1')
            assert p.rows()[0]['state']=='done'
    asyncio.run(run())


def test_shared_pro_state_has_one_paper_dispatcher(tmp_path):
    transport=httpx.MockTransport(lambda r:httpx.Response(200,json={}))
    with TestClient(create_app(state_path=tmp_path/'db',upstream_transport=transport)) as first:
        assert first.app.state.paper_dispatcher
        with TestClient(create_app(state_path=tmp_path/'db',upstream_transport=transport)) as second:
            assert not second.app.state.paper_dispatcher


def test_paper_plan_backend_failure_does_not_create_an_intent(tmp_path):
    transport=httpx.MockTransport(lambda r:httpx.Response(503,json={'detail':'unavailable'}))
    with TestClient(create_app(state_path=tmp_path/'db',upstream_transport=transport)) as client:
        response=client.post('/api/pro/paper-plans',json=spec().model_dump(),headers={'Origin':'http://testserver'})
        assert response.status_code==503
        assert client.get('/api/pro/paper-plans').json()['items']==[]


def test_management_lost_confirmation_reads_committed_receipt_without_worker(tmp_path,monkeypatch):
    from yoyo.monitor.pro import management
    from yoyo.okx_execution.manual_commands import Commands
    monkeypatch.setattr(management,'DEFAULT_MANUAL_RUNTIME',tmp_path)
    queue=Commands(tmp_path)
    with queue.db() as db:
        db.execute('INSERT INTO commands VALUES(?,?,?,?)',('a'*32,'b'*32,'confirmed',json.dumps({'id':'a'*32,'account_uid':'private'})));db.commit()
    transport=httpx.MockTransport(lambda r:httpx.Response(200,json={}))
    with TestClient(create_app(state_path=tmp_path/'pro-db',upstream_transport=transport)) as c:
        response=c.post('/api/pro/management/'+'a'*32+'/confirm',json={'acknowledgement':'CONFIRM-LIVE-MANAGEMENT'},headers={'Origin':'http://testserver','X-Fable-Manual':'owner-confirmation'})
        assert response.status_code==200 and response.json()['state']=='confirmed'
        assert 'account_uid' not in response.json()

"""Run controls must stay local, versioned and outside account execution."""
import hashlib
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yoyo.research_workspace import api as workspace_api
from yoyo.research_workspace import paper_api
from yoyo.research_workspace import paper_source
from test_api_store import _fixture_root

HEADERS={'origin':'http://testserver','sec-fetch-site':'same-origin'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    root=_fixture_root(tmp_path/'repo'); static=tmp_path/'static';static.mkdir()
    (root/'adapter.py').write_text('# version 1\n')
    strategy=dict(id='spike-v128',name='SPIKE',version='12.8',description='reference',kind='rule',status='research',
                  paper_supported=True,timeframes=['15m','1H'],side='both',entry_rule='future_open',exit_rule='frozen',
                  cost_bp=20,notes='',factor_ids=[],experiment_ids=[],revision=0,production_eligible=False,source_hash='test-source')
    monkeypatch.setattr(paper_api,'catalog',lambda root:[strategy,dict(strategy,id='yolo-confirmed',paper_supported=False,notes='model gate')])
    sha=hashlib.sha256((root/'adapter.py').read_bytes()).hexdigest()
    manifest={'hash':'test-source','files':{'adapter.py':sha},'commit':'a'*40,'dependency_hash':'x'}
    monkeypatch.setattr(paper_api,'source_manifest',lambda root:manifest)
    class FakeSource:
        def __init__(self,runtime): pass
        def checkpoint(self,symbol,tf,now):return {'candles':[{'t':now-900000,'o':1,'h':2,'l':.5,'c':1,'v':1}], 'tick':.1,'updated_ms':now}
    monkeypatch.setattr(paper_source,'MonitorSource',FakeSource)
    monkeypatch.setattr(workspace_api,'VISION',static)
    app=FastAPI();workspace_api.install(app,runtime=tmp_path/'runtime',root=root,launch_worker=False)
    with TestClient(app) as c:
        yield c,app.state.paper_store,manifest


def request(**overrides):
    return dict(strategy_id='spike-v128',symbols=['BTC-USDT-SWAP'],timeframes=['15m'],request_id='request-1234567890',**overrides)


def test_strategy_library_and_whitelisted_run_lifecycle(client):
    c,store,_=client
    assert len(c.get('/api/research/strategies').json()['items'])==2
    r=c.post('/api/research/paper/runs',json=request(),headers=HEADERS)
    assert r.status_code==201,r.text
    run=r.json();key=run['id']
    assert run['spec']['cost_bp']==20 and run['spec']['production_eligible'] is False
    assert c.post('/api/research/paper/runs',json=request(),headers=HEADERS).json()['id']==key
    for action,status in [('pause','paused'),('resume','running'),('stop','stopped')]:
        x=c.post(f'/api/research/paper/runs/{key}/{action}',headers=HEADERS)
        assert x.status_code==200 and x.json()['status']==status
    assert c.post(f'/api/research/paper/runs/{key}/resume',headers=HEADERS).status_code==409
    assert c.get(f'/api/research/paper/runs/{key}/sources').headers['content-type']=='application/zip'
    export=c.get(f'/api/research/paper/runs/{key}/export').json()
    assert export['production_eligible'] is False
    assert [x['kind'] for x in export['transitions']]==['created','pause','resume','stop']
    assert c.get('/api/research/paper/runs').json()['items'][0]['id']==key


def test_cross_origin_and_threshold_overrides_rejected(client):
    c,store,_=client
    assert c.post('/api/research/paper/runs',json=request()).status_code==403
    assert c.post('/api/research/paper/runs',json=request(cost_bp=0),headers=HEADERS).status_code==422
    assert c.post('/api/research/paper/runs',json=request(production_eligible=True),headers=HEADERS).status_code==422
    bad=request();bad['strategy_id']='yolo-confirmed'
    assert c.post('/api/research/paper/runs',json=bad,headers=HEADERS).status_code==409
    bad=request();bad['symbols']=['../../file']
    assert c.post('/api/research/paper/runs',json=bad,headers=HEADERS).status_code==400
    assert not store.runs()


def test_annotations_cannot_hide_an_active_run_or_promote_strategy(client):
    c,store,_=client
    r=c.post('/api/research/paper/runs',json=request(),headers=HEADERS).json()
    path='/api/research/strategies/spike-v128'
    assert c.put(path,json={'stage':'archived'},headers=HEADERS).status_code==409
    assert c.put(path,json={'stage':'production'},headers=HEADERS).status_code==422
    c.post(f"/api/research/paper/runs/{r['id']}/stop",headers=HEADERS)
    x=c.put(path,json={'stage':'archived','notes':'retained evidence'},headers=HEADERS)
    assert x.status_code==200 and x.json()['paper_supported'] is False
    assert c.put(path,json={'stage':'research','expected_revision':0},headers=HEADERS).status_code==409


def test_resume_cannot_hot_swap_strategy_sources(client):
    c,store,manifest=client
    r=c.post('/api/research/paper/runs',json=request(),headers=HEADERS).json()
    manifest['hash']='different'
    assert c.post(f"/api/research/paper/runs/{r['id']}/resume",headers=HEADERS).status_code==409
    assert store.run(r['id'])['spec']['source_hash']=='test-source'


def test_detail_pagination_and_not_found(client):
    c,_,_=client
    assert c.get('/api/research/paper/runs/missing').status_code==404
    r=c.post('/api/research/paper/runs',json=request(),headers=HEADERS).json()
    assert c.get(f"/api/research/paper/runs/{r['id']}?limit=500").status_code==422
    detail=c.get(f"/api/research/paper/runs/{r['id']}?limit=10&offset=0").json()
    assert detail['decisions']==[] and detail['total']==0


def test_hourly_closed_candle_can_age_normally_without_being_stale(client, monkeypatch):
    c, store, _ = client
    class HourlySource:
        def __init__(self, runtime): pass
        def checkpoint(self, symbol, tf, now):
            return {'candles':[{'t': now - 3600000 - 3000000}], 'updated_ms': now}
    monkeypatch.setattr(paper_source, 'MonitorSource', HourlySource)
    payload = request()
    payload['timeframes'] = ['1H']
    assert c.post('/api/research/paper/runs', json=payload, headers=HEADERS).status_code == 201


def test_missing_closed_candles_beyond_delivery_budget_rejects_run(client, monkeypatch):
    c, store, _ = client
    class StaleSource:
        def __init__(self, runtime): pass
        def checkpoint(self, symbol, tf, now):
            return {'candles':[{'t': now - 86400000}], 'updated_ms': now}
    monkeypatch.setattr(paper_source, 'MonitorSource', StaleSource)
    assert c.post('/api/research/paper/runs', json=request(), headers=HEADERS).status_code == 409
    assert not store.runs()

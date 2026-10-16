"""Behavioral contracts for isolated jobs, model clocks and projected geometry."""
from types import SimpleNamespace
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yoyo.yolo_workbench.api import Scan, install
from yoyo.yolo_workbench.store import Book
from yoyo.yolo_workbench.worker import due_schedules, execute
from yoyo.yolo_workbench.market import Market, parse, Cancelled
from yoyo.yolo_workbench.models import MODELS, DEFAULT_MODEL, TIMEFRAMES, freeze_spec
from yoyo.yolo_workbench.inference import Engine, enrich, projected, episodes, input_png


def spec(**kwargs):
    return dict(model_id=DEFAULT_MODEL,timeframe='5m',mode='latest',scope='selected',symbols=['ETH-USDT-SWAP'],start_ms=None,end_ms=None,**kwargs)


def rows(n=1200):
    return [dict(t=i*300000,o=100+i*.01,h=101+i*.01,l=99+i*.01,c=100.3+i*.01,v=10) for i in range(n)]


def test_temporal_validation_and_model_allowlist():
    with pytest.raises(ValueError):Scan(model_id='/tmp/evil.pt')
    with pytest.raises(ValueError):Scan(mode='history',start_ms=300001,end_ms=600000,timeframe='5m')
    with pytest.raises(ValueError):Scan(scope='selected',symbols=[])
    with pytest.raises(ValueError):Scan(timeframe='2m')
    assert Scan(mode='history',start_ms=300000,end_ms=600000,timeframe='5m').start_ms==300000


def test_parse_discards_unconfirmed_and_future_without_ohlc_inspection():
    raw=[['300000','1','2','.5','1','0','0','0','1'],['600000','bad','bad','bad','bad','bad','0','0','1'],['0','bad','bad','bad','bad','bad','0','0','0']]
    assert parse(raw,'5m',600000)==[dict(t=300000,o=1.,h=2.,l=.5,c=1.,v=0.)]
    with pytest.raises(ValueError):parse([['0','1','.5','2','1','1','0','0','1']],'5m',600000)


def test_market_requires_complete_warmup_and_no_gaps():
    data=rows(1300)
    class Client:
        def get(self,path,p):
            values=[r for r in data if r['t']<int(p['after'])][-100:]
            return [[str(r[k]) for k in ('t','o','h','l','c','v')]+['0','0','1'] for r in reversed(values)]
    market=Market(Client())
    out=market.candles('ETH-USDT-SWAP','5m',1300*300000,1100*300000)
    assert out[0]['t']==100*300000 and out[-1]['t']==1299*300000
    data.pop(150)
    with pytest.raises(ValueError,match='缺口'):market.candles('ETH-USDT-SWAP','5m',1300*300000,1100*300000)
    with pytest.raises(Cancelled):market.candles('ETH-USDT-SWAP','5m',1300*300000,1100*300000,lambda:True)


def test_raw_vertical_edges_are_inverted_not_reboxed():
    from yoyo.layers.l1_detection.render import render_chart
    frame=enrich(rows(150),'close').tail(18)
    _,tf=render_chart(frame,out_path=None)
    b=projected([.5,.5,.2,.1],0,.8,frame,tf,MODELS[DEFAULT_MODEL],'ETH-USDT-SWAP','5m','pixels')
    assert b['price_high']==pytest.approx(tf.price_max-(.45*tf.height-tf.top)/tf.plot_h*(tf.price_max-tf.price_min))
    assert b['price_low']!=frame.low.min()
    assert b['detected_at_ms']==150*300000
    assert b['window_end_ms']<b['detected_at_ms']


def test_causal_mas_and_hl2_are_distinct():
    source=rows(1200)
    prefix=enrich(source[:-1],'close')
    source[-1]['c']=1e6
    full=enrich(source,'close')
    for col in ('sma20','ema120'):np.testing.assert_array_equal(prefix[col],full[col].iloc[:-1])
    assert enrich(source,'hl2').iloc[-1].ema20!=full.iloc[-1].ema20


def test_scan_uses_only_closed_prefix_and_preserves_exact_input_png():
    seen=[]
    class FakeModel:
        def predict(self,source,**kw):
            seen.extend(source)
            class Tensor:
                def __init__(self,x):self.x=np.array(x)
                def cpu(self):return self
                def numpy(self):return self.x
            return [SimpleNamespace(boxes=SimpleNamespace(xywhn=Tensor([[.55,.5,.22,.15]]),cls=Tensor([0]),conf=Tensor([.8]))) for _ in source]
    engine=Engine();engine.models[DEFAULT_MODEL]=(FakeModel(),'cpu')
    settings=spec();settings.update(end_ms=1200*300000,start_ms=960*300000)
    result=engine.scan(rows(1205),settings,'ETH-USDT-SWAP')
    assert result['windows_scored']==6
    assert len(seen)==6 and seen[0].shape==(742,1280,3)
    assert max(c['t'] for c in result['candles'])==1199*300000
    assert {d['window_end_ms'] for d in result['raw_detections']}=={i*300000 for i in (1197,1198,1199)}
    assert input_png(result,result['raw_detections'][0]['id']).startswith(b'\x89PNG')
    # Choose a row inside the selected exact window to detect altered snapshot pixels.
    chosen=result['raw_detections'][-1];result['candles'][-1]['close']+=.2
    with pytest.raises(ValueError,match='不一致'):input_png(result,chosen['id'])


def test_episode_clock_not_backdated_to_highest_score():
    a=dict(id='a',side='long',core_start_ms=10,core_end_ms=20,confidence=.5,detected_at_ms=50)
    b=dict(a,id='b',confidence=.9,detected_at_ms=80)
    c=dict(a,id='c',side='short')
    out=episodes([a,b,c]);assert len(out)==2
    long=next(x for x in out if x['side']=='long')
    assert long['id']=='b' and long['first_detected_at_ms']==50 and long['detected_at_ms']==80


def test_queue_idempotency_cancellation_and_restart(tmp_path):
    book=Book(tmp_path);s=spec();j=book.submit(s,'request1')
    assert book.submit(s,'request1')['id']==j['id']
    with pytest.raises(ValueError):book.submit(dict(s,timeframe='1H'),'request1')
    book.cancel(j['id']);assert book.claim() is None
    j=book.submit(s,'request2');assert book.claim()['id']==j['id'];book.result(j['id'],'ETH-USDT-SWAP',dict(error='example'))
    book.recover();assert book.get(j['id'])['status']=='interrupted' and len(book.summaries(j['id']))==1
    history=dict(s,mode='history',start_ms=300000,end_ms=600000)
    book.submit(history,'history1')
    with pytest.raises(ValueError):book.submit(dict(history,end_ms=900000),'history1')


def test_schedule_does_not_overlap_or_backfill(tmp_path):
    book=Book(tmp_path);book.schedule(dict(id='s',spec=spec(),enabled=True,next_due_ms=1000,interval_minutes=5,last_job_id=None))
    due_schedules(book,1000000);assert len(book.jobs())==1
    s=book.schedules()[0];assert s['next_due_ms']==1300000
    due_schedules(book,1400000);assert len(book.jobs())==1 and book.schedules()[0]['skipped_overlap']==1
    book.cancel(book.jobs()[0]['id']);due_schedules(book,1800000);assert len(book.jobs())==2
    book.remove_schedule('s');due_schedules(book,3000000);assert len(book.jobs())==2


def test_worker_keeps_per_symbol_failures_and_correct_total(tmp_path):
    book=Book(tmp_path);s=spec();s['symbols']=['ETH-USDT-SWAP','BTC-USDT-SWAP'];j=book.submit(s,'run');j=book.claim()
    class Source:
        client=SimpleNamespace(clock=lambda:1200*300000)
        def universe(self):return [{'symbol':s} for s in ('ETH-USDT-SWAP','BTC-USDT-SWAP')]
        def candles(self,symbol,*args):
            if symbol.startswith('BTC'):raise ValueError('missing history')
            return rows()
    class Detector:
        def scan(self,*args):return dict(episodes=[],raw_detections=[],windows_scored=6)
    execute(book,j,Source(),Detector());saved=book.get(j['id'])
    assert saved['status']=='partial' and saved['progress']['completed']==2
    assert saved['progress']['failed']==1 and len(book.summaries(j['id']))==2


def test_api_rejects_cross_origin_and_never_launches_on_reads(tmp_path,monkeypatch):
    from yoyo.yolo_workbench import api as api_module
    original=api_module.catalog
    def available():
        value=original()
        for m in value['items']:m['available']=True
        return value
    monkeypatch.setattr(api_module,'catalog',available)
    app=FastAPI();install(app,tmp_path,launch_worker=False);client=TestClient(app)
    assert client.get('/api/yolo-detect/catalog').status_code==200
    assert client.get('/api/yolo-detect/jobs').json()['items']==[]
    assert client.post('/api/yolo-detect/jobs',json=spec()).status_code==403
    response=client.post('/api/yolo-detect/jobs',json=spec(),headers={'Origin':'http://testserver'})
    assert response.status_code==202
    j=response.json();assert j['spec']['model_sha256']==MODELS[DEFAULT_MODEL]['sha256']
    assert client.get('/api/yolo-detect/jobs/'+j['id']).json()['results']==[]
    assert client.post('/api/yolo-detect/jobs/'+j['id']+'/cancel',headers={'Origin':'http://testserver'}).json()['status']=='cancelled'
    assert client.get('/api/yolo-detect/jobs/nope').status_code==404

"""Display updates preserve the original YOLO input and causal MA state."""
from copy import deepcopy
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yoyo.yolo_workbench.api import install
from yoyo.yolo_workbench.chart_tail import ChartTail, extend, MAX_TAIL
from yoyo.yolo_workbench.inference import enrich

STEP=900000

def raw_rows(n=1220):
    return [dict(t=i*STEP,o=100+i*.01,h=102+i*.01,l=99+i*.01,c=101+i*.01+(i%17)*.03,v=10) for i in range(n)]

def snapshot(source='close'):
    return dict(symbol='ETH-USDT-SWAP',timeframe='15m',ma_source=source,cutoff_ms=1200*STEP,
                candles=enrich(raw_rows(1200),source).tail(240).to_dict('records'),episodes=[{'id':'original-box'}])

class Source:
    def __init__(self, rows=None, cached=None):
        self.rows=raw_rows() if rows is None else rows;self.cached=cached or [];self.client=self;self.calls=[];self.stamp=1220*STEP
    def synchronize(self):pass
    def clock(self):return self.stamp
    def checkpoint(self,*args):return self.cached
    def get(self,path,params):
        self.calls.append((path,params))
        rows=[r for r in self.rows if r['t']<int(params['after'])][-int(params['limit']):]
        return [[str(r[k]) for k in ('t','o','h','l','c','v')]+[0,0,str(r.get('confirm',1))] for r in reversed(rows)]

@pytest.mark.parametrize('source',['close','hl2'])
def test_tail_continues_exact_prefix_mas_without_rewriting_snapshot(source):
    original=snapshot(source);copy=deepcopy(original)
    tail=extend(original,raw_rows()[1200:])
    full=enrich(raw_rows(),source).iloc[1200:].to_dict('records')
    for a,b in zip(tail,full):
        for column in ('sma20','sma60','sma120','ema20','ema60','ema120'):
            assert a[column]==pytest.approx(b[column],abs=1e-10)
    assert original==copy and tail[0]['t']==1200*STEP

def test_existing_confirmed_checkpoint_avoids_network_and_cache_advances():
    market=Source(cached=raw_rows());service=ChartTail(market=market);original=snapshot()
    first=service.read('job',original)
    assert len(first['candles'])==20 and market.calls==[]
    assert service.read('job',original)==first
    market.rows=raw_rows(1221);market.stamp+=STEP
    latest=service.read('job',original)
    assert len(latest['candles'])==21 and len(market.calls)==1
    assert latest['chart_cutoff_ms']==1221*STEP
    assert original['candles'][-1]['t']==1199*STEP

def test_missing_or_unconfirmed_tail_fails_instead_of_skipping_or_faking_bars():
    rows=raw_rows();rows[-1]['confirm']=0
    source=Source(rows=rows);service=ChartTail(market=source)
    with pytest.raises(ValueError,match='尚未到齐'):service.read('job',snapshot())
    assert not service.cache
    source.rows=raw_rows();source.rows.pop(1202)
    with pytest.raises(ValueError,match='尚未到齐'):service.read('job',snapshot())
    source.stamp=(1200+MAX_TAIL+1)*STEP
    with pytest.raises(ValueError,match='3,000'):service.read('job',snapshot())

def test_failed_update_does_not_replace_the_successful_cache():
    market=Source();service=ChartTail(market=market);old=service.read('job',snapshot())
    market.stamp+=STEP
    with pytest.raises(ValueError,match='尚未到齐'):service.read('job',snapshot())
    assert service.cache[('job','ETH-USDT-SWAP')]==old

def test_api_returns_tail_separately_and_sort_metadata_without_new_job(tmp_path,monkeypatch):
    market=Source(cached=raw_rows())
    monkeypatch.setattr('yoyo.yolo_workbench.chart_tail.Market',lambda **kw:market)
    app=FastAPI();install(app,tmp_path,launch_worker=False);book=app.state.yolo_workbench
    spec=dict(mode='latest',timeframe='15m',model_id='grade-a-close-1280')
    job=book.submit(spec,'old-job');value=snapshot();value['raw_detections']=[]
    value['episodes']=[dict(id='a',detected_at_ms=10,confidence=.9),dict(id='b',detected_at_ms=20,confidence=.5)]
    book.result(job['id'],value['symbol'],value);client=TestClient(app)
    tail=client.get(f"/api/yolo-detect/jobs/{job['id']}/results/{value['symbol']}/chart").json()
    assert len(tail['candles'])==20 and 'episodes' not in tail
    assert book.job_count()==1 and book.result(job['id'],value['symbol'])==value
    summary=book.summaries(job['id'])[0]
    assert summary['latest_detection_ms']==20 and summary['max_confidence']==.9

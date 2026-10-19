"""The combined workspace must preserve every period and immutable provenance."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yoyo.yolo_workbench.store import Book
from yoyo.yolo_workbench.latest import Latest
from yoyo.yolo_workbench.api import install


def add(book, key, period='5m', cutoff=300000, mode='latest', count=1, error=None, symbol='ETH-USDT-SWAP'):
    job = book.submit(dict(model_id='test', timeframe=period, mode=mode, scope='selected', symbols=[symbol], end_ms=cutoff), key)
    book.result(job['id'], symbol, dict(cutoff_ms=cutoff, observed_ms=cutoff+100, episodes=[dict(detected_at_ms=cutoff, confidence=.8)]*count, raw_detections=[], error=error))
    book.update(job['id'], status='completed')
    return job['id']


def test_latest_merges_periods_and_does_not_resurrect_shapes(tmp_path):
    book=Book(tmp_path);first=add(book,'a');quarter=add(book,'b','15m',900000)
    view=Latest(book);data=view.read()
    assert {(r['timeframe'],r['job_id']) for r in data['items']}=={('5m',first),('15m',quarter)}
    assert 'items' not in view.read(data['revision'])
    newer=add(book,'c',cutoff=600000,count=0)
    rows=view.read()['items']
    assert next(r for r in rows if r['timeframe']=='5m')['count']==0
    assert {r['job_id'] for r in rows}=={newer,quarter}
    add(book,'late-completion',cutoff=100000)
    add(book,'historical','15m',9000000,mode='history')
    assert {r['job_id'] for r in view.read()['items']}=={newer,quarter}
    assert Latest(book).read()['items']==view.read()['items']


def test_latest_exposes_errors_without_fabricating_coverage(tmp_path):
    book=Book(tmp_path);add(book,'good');bad=add(book,'failed',cutoff=600000,count=0,error='source unavailable')
    rows=Latest(book).read()['items']
    assert len(rows)==1 and rows[0]['job_id']==bad and rows[0]['error']=='source unavailable'
    assert 'candles' not in rows[0] and 'spec' not in rows[0]


def test_latest_route_only_reads_and_validates_revision(tmp_path):
    app=FastAPI();install(app,tmp_path,launch_worker=False)
    book=app.state.yolo_workbench;identity=add(book,'a')
    client=TestClient(app);response=client.get('/api/yolo-detect/latest').json()
    assert response['items'][0]['job_id']==identity
    assert client.get('/api/yolo-detect/latest',params={'if_revision':response['revision']}).json()['unchanged']
    assert book.job_count()==1


def test_latest_delta_contains_only_changed_pairs(tmp_path):
    book=Book(tmp_path);add(book,'a');add(book,'b','15m',900000)
    view=Latest(book);first=view.read();newer=add(book,'c',cutoff=600000,count=0)
    delta=view.read(first['revision'])
    assert delta['partial'] is True
    assert len(delta['items'])==1 and delta['items'][0]['job_id']==newer
    assert len(view.read()['items'])==2

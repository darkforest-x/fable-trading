"""Patrol feed clocks, duplicate suppression and exact chart provenance."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yoyo.yolo_workbench.api import install
from yoyo.yolo_workbench.discoveries import Feed
from yoyo.yolo_workbench.store import Book, now


def add(book, key, *, schedule='plan', observed=100, start=10, end=20, side='long',
        symbol='ETH-USDT-SWAP', timeframe='15m', mode='latest', model='model-a', error=None):
    spec=dict(model_id=model,model_sha256=model,renderer_sha256='renderer',parameters={'conf':.25},
              adapter_version='v1',timeframe=timeframe,mode=mode,scope='selected',symbols=[symbol])
    job=book.submit(spec,key,schedule_id=schedule)
    box=dict(id=key,side=side,confidence=.8,core_start_ms=start,core_end_ms=end,
             core_bars=4,post_bars=3,detected_at_ms=end+30,first_detected_at_ms=end+20)
    book.result(job['id'],symbol,dict(observed_ms=observed,episodes=[box],candles=['not sent'],error=error))
    book.update(job['id'],status='partial' if error else 'completed')
    return job,box


def test_repeated_core_keeps_first_observation_but_links_latest_exact_box(tmp_path):
    book=Book(tmp_path);feed=Feed(book)
    first,_=add(book,'first',observed=100)
    original=feed.read(days=0)['items'][0]
    latest,_=add(book,'later',schedule='other-plan',observed=200,start=15,end=25)
    item=feed.read(days=0)['items'][0]
    assert item['id']==original['id'] and item['first_observed_ms']==100
    assert item['last_observed_ms']==200 and item['first_confirmed_ms']==40
    assert item['job_id']==latest['id'] and item['detection_id']=='later'
    assert item['scan_count']==2 and item['detected_at_ms']==55
    assert 'candles' not in item and 'contract' not in item
    assert Feed(book).read(days=0)['items']==feed.read(days=0)['items']
    # Plans can stop without erasing discoveries.
    book.remove_schedule('plan');assert feed.read(days=0)['total']==1


def test_manual_history_and_failed_results_do_not_enter_patrol_feed(tmp_path):
    book=Book(tmp_path)
    add(book,'manual',schedule=None);add(book,'history',mode='history')
    add(book,'bad',error='unavailable');job,_=add(book,'valid')
    book.update(job['id'],status='partial',error='another coin failed')
    out=Feed(book).read(days=0)
    assert len(out['items'])==1 and out['items'][0]['detection_id']=='valid'
    assert out['latest_job']['status']=='partial'


def test_overlap_is_transitive_but_period_direction_model_and_symbol_are_separate(tmp_path):
    book=Book(tmp_path)
    add(book,'a',start=10,end=20);add(book,'b',start=30,end=40)
    add(book,'bridge',start=19,end=31)
    add(book,'other-period',timeframe='5m');add(book,'other-side',side='short')
    add(book,'other-model',model='model-b');add(book,'other-coin',symbol='BTC-USDT-SWAP')
    items=Feed(book).read(days=0)['items']
    assert len(items)==5 and sorted(r['scan_count'] for r in items)==[1,1,1,1,3]


def test_repeat_does_not_bump_old_shape_and_filters_apply_after_merging(tmp_path):
    book=Book(tmp_path);stamp=now()
    add(book,'old',observed=stamp-9*86400000)
    add(book,'new-event',observed=stamp-1000,start=50,end=60)
    add(book,'old-again',observed=stamp,start=11,end=21)
    feed=Feed(book)
    assert feed.read()['total']==1
    all_=feed.read(days=0,limit=1)
    assert all_['total']==2 and all_['items'][0]['detection_id']=='new-event'
    assert feed.read(days=0,query=' eth ',timeframe='15m',side='long')['total']==2
    assert feed.read(days=0,side='short')['items']==[]


def test_api_reads_do_not_submit_jobs_and_warm_cache_observes_external_writes(tmp_path):
    app=FastAPI();install(app,tmp_path,launch_worker=False);client=TestClient(app)
    url='/api/yolo-detect/discoveries?days=0&limit=1'
    assert client.get(url).json()['items']==[]
    book=Book(tmp_path);job,_=add(book,'existing',observed=now())
    out=client.get(url).json()
    assert out['total']==1 and out['items'][0]['job_id']==job['id']
    assert client.get(url).json()['total']==1 and book.job_count()==1
    assert client.get('/api/yolo-detect/discoveries?days=7&side=long').status_code==200
    assert client.get('/api/yolo-detect/discoveries?limit=501').status_code==422

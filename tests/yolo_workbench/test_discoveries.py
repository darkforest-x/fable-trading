"""Patrol feed clocks, duplicate suppression and exact chart provenance."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yoyo.yolo_workbench.api import install
from yoyo.yolo_workbench.discoveries import Feed
from yoyo.yolo_workbench.store import Book, now


def add(book, key, *, schedule='plan', observed=100, start=10, end=20, side='long',
        symbol='ETH-USDT-SWAP', timeframe='15m', mode='latest', model='model-a', error=None,
        confidence=.8):
    spec=dict(model_id=model,model_sha256=model,renderer_sha256='renderer',parameters={'conf':.25},
              adapter_version='v1',timeframe=timeframe,mode=mode,scope='selected',symbols=[symbol])
    job=book.submit(spec,key,schedule_id=schedule)
    box=dict(id=key,side=side,confidence=confidence,core_start_ms=start,core_end_ms=end,
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


def test_sorting_applies_before_pagination_and_preserves_discovery_times(tmp_path):
    app=FastAPI();install(app,tmp_path,launch_worker=False);client=TestClient(app)
    book=Book(tmp_path)
    add(book,'newest',symbol='Z-USDT-SWAP',observed=300,confidence=.4)
    add(book,'highest',symbol='B-USDT-SWAP',observed=100,confidence=.9)
    add(book,'alphabetic',symbol='A-USDT-SWAP',observed=200,confidence=.5)
    for sort,expected,observed in [('newest','newest',300),('score','highest',100),('symbol','alphabetic',200)]:
        out=client.get(f'/api/yolo-detect/discoveries?days=0&limit=1&sort={sort}').json()
        assert out['total']==3 and out['items'][0]['detection_id']==expected
        assert out['items'][0]['first_observed_ms']==observed
    assert client.get('/api/yolo-detect/discoveries?sort=unknown').status_code==422


def test_pages_are_bounded_filtered_and_keep_new_arrivals_out_of_older_pages(tmp_path):
    app=FastAPI();install(app,tmp_path,launch_worker=False);client=TestClient(app)
    book=Book(tmp_path)
    for i in range(5):
        add(book,str(i),symbol=f'COIN{i}-USDT-SWAP',observed=100+i)
    first=client.get('/api/yolo-detect/discoveries?days=0&limit=2&before_ms=104').json()
    add(book,'arrival',symbol='NEW-USDT-SWAP',observed=105)
    second=client.get('/api/yolo-detect/discoveries?days=0&limit=2&offset=2&before_ms=104').json()
    last=client.get('/api/yolo-detect/discoveries?days=0&limit=2&offset=4&before_ms=104').json()
    ids=[r['detection_id'] for page in [first,second,last] for r in page['items']]
    assert ids==['4','3','2','1','0']
    assert second['total']==5 and second['offset']==2 and second['limit']==2
    assert second['before_ms']==104 and book.job_count()==6
    assert client.get('/api/yolo-detect/discoveries?days=0&offset=20').json()['items']==[]
    assert client.get('/api/yolo-detect/discoveries?offset=-1').status_code==422
    assert client.get('/api/yolo-detect/discoveries?before_ms=-1').status_code==422
    filtered=client.get('/api/yolo-detect/discoveries?days=0&q=COIN1&limit=1').json()
    assert filtered['total']==1 and filtered['items'][0]['detection_id']=='1'


def test_full_contract_search_is_exact_while_abbreviated_search_stays_substring(tmp_path):
    app=FastAPI();install(app,tmp_path,launch_worker=False);client=TestClient(app)
    book=Book(tmp_path)
    add(book,'btc',symbol='BTC-USDT-SWAP',observed=100)
    add(book,'wbtc',symbol='WBTC-USDT-SWAP',observed=200)

    exact=client.get('/api/yolo-detect/discoveries?days=0&q=%20btc-usdt-swap%20&limit=1').json()
    exact_after_last=client.get('/api/yolo-detect/discoveries?days=0&q=BTC-USDT-SWAP&limit=1&offset=1').json()
    assert exact['total']==1 and [r['detection_id'] for r in exact['items']]==['btc']
    assert exact_after_last['total']==1 and exact_after_last['items']==[]

    partial_first=client.get('/api/yolo-detect/discoveries?days=0&q=BTC&limit=1&offset=0').json()
    partial_second=client.get('/api/yolo-detect/discoveries?days=0&q=BTC&limit=1&offset=1').json()
    assert partial_first['total']==partial_second['total']==2
    assert [r['detection_id'] for r in partial_first['items']+partial_second['items']]==['wbtc','btc']


def test_page_cutoff_freezes_scores_and_cluster_merges_before_sorting(tmp_path):
    book=Book(tmp_path);feed=Feed(book)
    add(book,'a',observed=100,start=10,end=20,confidence=.9)
    add(book,'b',observed=101,start=30,end=40,confidence=.7)
    add(book,'c',observed=102,start=50,end=60,confidence=.5)
    first=feed.read(days=0,limit=1,sort='score',before_ms=102)
    add(book,'bridge',observed=103,start=19,end=31,confidence=.1)
    add(book,'promoted',observed=104,start=50,end=60,confidence=.99)
    second=feed.read(days=0,offset=1,limit=2,sort='score',before_ms=first['before_ms'])
    assert [r['detection_id'] for r in first['items']+second['items']]==['a','b','c']
    assert second['total']==3
    assert feed.read(days=0,sort='score')['items'][0]['detection_id']=='promoted'
    assert Feed(book).read(days=0,offset=1,limit=2,sort='score',before_ms=102)['items']==second['items']

"""Coverage, fair batching, resume and lossless chart-storage regressions."""
from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace
import json
import numpy as np
import pytest
from yoyo.yolo_workbench.store import Book
from yoyo.yolo_workbench.patrol import Patrol,enqueue_due,next_due,GRACE_MS
from yoyo.yolo_workbench.models import DEFAULT_MODEL,TIMEFRAMES
from yoyo.yolo_workbench.inference import Engine,enrich,input_png
from yoyo.yolo_workbench.market import Market

END=1800000000000


def plan(book,key='s',tf='15m',interval=15):
    step=TIMEFRAMES[tf];end=END//step*step
    s=dict(id=key,spec=dict(model_id=DEFAULT_MODEL,timeframe=tf,mode='latest',scope='all',symbols=[]),
           enabled=True,incremental=True,interval_minutes=interval,next_due_ms=end+GRACE_MS,
           baseline_after_ms=end-max(3,interval*60000//step)*step,last_job_id=None)
    book.schedule(s);return s


class MarketStub:
    sources={}
    client=SimpleNamespace(clock=lambda:END+GRACE_MS)
    def __init__(self):self.fail=set();self.visits=[]
    def universe(self):return [dict(symbol=s) for s in ['A','B','C']]
    def candles(self,symbol,tf,end,start,*args):
        self.visits.append((symbol,tf))
        if symbol in self.fail:raise ValueError('missing closed candle')
        return []


class Detector:
    def __init__(self):self.calls=[]
    def scan(self,rows,spec,symbol,*args):
        endpoints=list(range(spec['scan_after_ms']+TIMEFRAMES[spec['timeframe']],spec['end_ms']+1,TIMEFRAMES[spec['timeframe']]))
        self.calls.append((symbol,spec['timeframe'],endpoints))
        return dict(symbol=symbol,timeframe=spec['timeframe'],endpoints_scored=len(endpoints),episodes=[],raw_detections=[])


def finish(patrol,limit=100):
    for _ in range(limit):
        if not patrol.tick():return
    raise AssertionError('patrol did not settle')


def test_grid_and_four_hour_cycle_covers_all_four_hourly_endpoints(tmp_path):
    book=Book(tmp_path);s=plan(book,tf='1H',interval=240);end=s['next_due_ms']-GRACE_MS
    enqueue_due(book,end+GRACE_MS+21000);engine=Detector();p=Patrol(book,MarketStub(),engine);finish(p)
    assert len(engine.calls)==3 and all(len(c[2])==4 for c in engine.calls)
    assert engine.calls[0][2][-1]==end
    assert book.schedules()[0]['next_due_ms']==next_due(end+GRACE_MS+21000,240)
    enqueue_due(book,end+4*3600000+GRACE_MS);finish(p)
    assert engine.calls[3][2][0]==end+3600000
    assert set(engine.calls[0][2]).isdisjoint(engine.calls[3][2])


def test_failure_never_advances_cursor_and_retry_then_catchup_is_contiguous(tmp_path):
    book=Book(tmp_path);plan(book);enqueue_due(book,END+GRACE_MS);market=MarketStub();market.fail={'A'};engine=Detector();p=Patrol(book,market,engine)
    finish(p)
    with closing(book.connect()) as db,db:
        assert db.execute("SELECT through_ms FROM patrol_cursors WHERE symbol='A'").fetchone() is None
        db.execute('UPDATE patrol_units SET retry_ms=0')
    market.fail.clear();finish(p)
    assert all(j['status']=='completed' for j in book.jobs())
    enqueue_due(book,END+900000+GRACE_MS);finish(p)
    assert all(len(c[2])==1 for c in engine.calls[-3:])


def test_short_jobs_rotate_coins_before_long_job_and_recover_after_result_commit(tmp_path):
    book=Book(tmp_path);plan(book,'long','1H',240);plan(book,'short','5m',15);plan(book,'short2','15m',15)
    enqueue_due(book,END+GRACE_MS);engine=Detector();p=Patrol(book,MarketStub(),engine)
    p.tick();p.tick();assert {c[1] for c in engine.calls}=={'5m','15m'}
    book.recover();finish(p)
    assert len(engine.calls)==9 and all(c[1]!='1H' for c in engine.calls[:6])
    # Crash after the final atomic commit but before updating the job summary.
    job=next(j for j in book.jobs() if j['spec']['timeframe']=='1H');book.update(job['id'],status='running')
    book.recover();finish(p);assert book.get(job['id'])['status']=='completed' and len(engine.calls)==9


def test_long_outage_is_bounded_but_never_skips_remaining_endpoints(tmp_path):
    book=Book(tmp_path);s=plan(book,'s','5m',15);enqueue_due(book,END+GRACE_MS)
    engine=Detector();p=Patrol(book,MarketStub(),engine);finish(p);initial=len(engine.calls)
    stamp=END+20*300000+GRACE_MS
    enqueue_due(book,stamp);finish(p)
    assert all(len(c[2])==12 for c in engine.calls[initial:])
    enqueue_due(book,stamp);finish(p)
    assert all(len(c[2])==8 for c in engine.calls[-3:])
    for symbol in ['A','B','C']:
        endpoints=[t for sym,_,ts in engine.calls if sym==symbol for t in ts]
        assert len(endpoints)==len(set(endpoints))==23
        assert all(b-a==300000 for a,b in zip(endpoints,endpoints[1:]))


def test_insufficient_listing_is_explicit_and_does_not_fetch_or_claim_coverage(tmp_path):
    book=Book(tmp_path);plan(book,tf='1Dutc',interval=1440);enqueue_due(book,END+GRACE_MS)
    market=MarketStub();market.universe=lambda:[dict(symbol='NEW',listed_ms=END-10*86400000)]
    p=Patrol(book,market,Detector());finish(p)
    j=book.jobs()[0];assert j['progress']['unavailable']==1 and j['progress']['failed']==0
    assert book.summaries(j['id'])[0]['availability']=='insufficient_history'
    assert market.visits==[]
    with closing(book.connect()) as db:assert db.execute('SELECT count(*) FROM patrol_cursors').fetchone()[0]==0


def test_lossless_chart_storage_shares_ohlc_without_reusing_different_mas(tmp_path):
    book=Book(tmp_path)
    rows=[dict(t=i*900000,open=100+i*.01,high=101+i*.01,low=99+i*.01,close=100+i*.01,volume=10,sma20=99.999999999+i*.01) for i in range(240)]
    original=dict(symbol='A',timeframe='15m',candles=rows,episodes=[])
    j=book.submit({},'one');book.result(j['id'],'A',original)
    changed=deepcopy(original)
    for r in changed['candles']:r['sma20']+=1e-10
    second=book.submit({},'two');book.result(second['id'],'A',changed)
    assert book.result(j['id'],'A')==original and book.result(second['id'],'A')==changed
    with closing(book.connect()) as db:
        assert db.execute('SELECT count(*) FROM chart_blobs').fetchone()[0]==10 # 8 shared OHLC blocks + 2 exact MA versions
        assert db.execute('SELECT sum(length(payload)) FROM chart_blobs').fetchone()[0]<len(json.dumps(original))


def test_incremental_endpoint_pixels_match_the_original_snapshot(tmp_path):
    import hashlib
    seen=[]
    class Model:
        def predict(self,source,**kwargs):
            seen.extend(hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest() for image in source)
            return [SimpleNamespace(boxes=None) for _ in source]
    engine=Engine();engine.models[DEFAULT_MODEL]=(Model(),'cpu')
    rows=[dict(t=i*300000,o=100+i*.01,h=101+i*.01,l=99+i*.01,c=100+i*.01,v=10) for i in range(1240)]
    spec=dict(model_id=DEFAULT_MODEL,timeframe='5m',mode='latest',start_ms=1000*300000,end_ms=1240*300000)
    old=engine.scan(rows,spec,'A');new=engine.scan(rows,dict(spec,incremental=True,scan_after_ms=1239*300000),'A')
    assert old['candles']==new['candles'] and new['endpoints_scored']==1 and new['windows_scored']==2
    assert seen[4:6]==seen[6:8]


def test_saved_research_prefix_is_reused_after_market_restart(tmp_path):
    book=Book(tmp_path);step=300000
    rows=[dict(t=i*step,o=100,h=101,l=99,c=100,v=10) for i in range(1241)]
    calls=[]
    class Client:
        def clock(self):return 1241*step
        def get(self,path,p):
            calls.append(path);selected=[r for r in rows if r['t']<int(p['after'])][-int(p['limit']):]
            return [[r[k] for k in ('t','o','h','l','c','v')]+[0,0,'1'] for r in reversed(selected)]
    first=Market(Client(),book=book);first.candles('A','5m',1240*step,1000*step);n=len(calls)
    restarted=Market(Client(),book=book);result=restarted.candles('A','5m',1241*step,1001*step)
    assert len(calls)==n+1 and result[0]['t']==step
    for i in range(40):restarted.remember((str(i),'5m'),rows)
    assert len(restarted.cache)==32
    book.input_cache('A','5m',rows[:1240])
    assert book.input_cache('A','5m')[-1]['t']==1240*step


def test_newer_monitor_tail_avoids_duplicate_download_but_rejects_conflicts(tmp_path):
    book=Book(tmp_path);step=900000
    rows=[dict(t=i*step,o=100.,h=101.,l=99.,c=100.,v=10.) for i in range(1241)]
    book.input_cache('A','15m',rows[:1240])
    class Client:
        def get(self,*args):raise AssertionError('confirmed tail must not be downloaded again')
    market=Market(Client(),runtime=tmp_path,book=book);market.checkpoint=lambda *_:rows
    result=market.candles('A','15m',1241*step,1001*step)
    assert result==rows[1:] and market.sources[('A','15m')]=='existing_okx_confirmed_checkpoint'
    changed=deepcopy(rows);changed[100]['c']=100.5;market.checkpoint=lambda *_:changed
    with pytest.raises(ValueError,match='未静默覆盖'):market.candles('A','15m',1241*step,1001*step)


def test_bounded_prefetch_overlaps_only_io_and_preserves_endpoint_coverage(tmp_path,monkeypatch):
    import threading,time
    import yoyo.yolo_workbench.prefetch as prefetch
    book=Book(tmp_path);plan(book,'a','5m',15);plan(book,'b','15m',15);enqueue_due(book,END+GRACE_MS)
    active=0;peak=0;lock=threading.Lock()
    class Reader:
        sources={}
        def __init__(self,*args,**kwargs):pass
        def candles(self,*args):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            try:time.sleep(.04);return []
            finally:
                with lock:active-=1
    monkeypatch.setattr(prefetch,'Market',Reader)
    market=MarketStub();market.runtime=tmp_path;engine=Detector();patrol=Patrol(book,market,engine,prefetch=True)
    try:
        finish(patrol)
        assert peak==2 and not patrol.prefetch.pending
        assert len(engine.calls)==6 and all(j['status']=='completed' for j in book.jobs())
        assert all(len(ts)==3 for _,_,ts in engine.calls)
    finally:patrol.close()


def test_plan_recovery_requires_durable_coverage_and_retains_original_failure(tmp_path):
    book=Book(tmp_path);j=book.submit(dict(incremental=True,cursor_key='s',end_ms=100),'recover')
    book.result(j['id'],'A',dict(symbol='A',error='network',retry_pending=True))
    book.update(j['id'],status='partial',progress=dict(completed=1,total=1,failed=1))
    with closing(book.connect()) as db,db:db.execute('INSERT INTO patrol_cursors VALUES (?,?,?)',('s','A',99))
    assert book.current_progress(book.get(j['id']))['failed']==1
    with closing(book.connect()) as db,db:db.execute('UPDATE patrol_cursors SET through_ms=100')
    assert book.current_progress(book.get(j['id']))['recovered']==1
    assert book.current_progress(book.get(j['id']))['failed']==0
    assert book.get(j['id'])['progress']['failed']==1

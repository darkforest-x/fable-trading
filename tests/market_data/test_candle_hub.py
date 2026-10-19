"""Concurrency, incremental I/O and causal raw-field contracts for public OHLC."""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import time

import pytest

from yoyo.data.candle_hub import CandleHub, CandleError, bar_open, validate

PATH = '/api/v5/market/candles'
NOW = 600_001


def row(t, confirm='1'):
    return [str(t),'2','3','1','2','100','25','50',confirm]


def query(limit=5, **extra):
    return dict(instId='BTC-USDT-SWAP',bar='1m',limit=str(limit),**extra)


class Exchange:
    def __init__(self):
        self.calls = []

    def __call__(self,path,params):
        self.calls.append((path,params))
        end = (int(params['after'])-1)//60_000*60_000
        return [row(t) for t in range(end,end-int(params['limit'])*60_000,-60_000) if t>=0]


def test_cross_client_overlap_and_incremental_tail(tmp_path):
    first, second = CandleHub(tmp_path), CandleHub(tmp_path)
    source = Exchange()
    initial = first.fetch(PATH,query(),source,now_ms=NOW)
    assert len(initial)==5 and initial[0][5:8]==['100','25','50']
    assert second.fetch('/api/v5/market/history-candles',query(3),source,now_ms=NOW)==initial[:3]
    assert len(source.calls)==1
    fresh = second.fetch(PATH,query(),source,now_ms=NOW+60_000)
    assert len(fresh)==5 and fresh[0][0]=='600000'
    assert source.calls[-1][1]['limit']=='1'
    assert source.calls[-1][1]['after']=='600001'
    extended = first.fetch(PATH,query(8),source,now_ms=NOW+60_000)
    assert len(extended)==8 and source.calls[-1][1]['limit']=='2'


def test_many_threads_one_fetch(tmp_path):
    hubs=[CandleHub(tmp_path) for _ in range(8)]
    source=Exchange()
    def slow(path,params):
        time.sleep(.05)
        return source(path,params)
    with ThreadPoolExecutor(8) as pool:
        rows=list(pool.map(lambda hub:hub.fetch(PATH,query(),slow,now_ms=NOW),hubs))
    assert len(source.calls)==1
    assert all(r==rows[0] for r in rows)


def process_read(root, barrier, calls, queue):
    hub=CandleHub(root)
    barrier.wait(timeout=10)
    def source(path,params):
        with calls.get_lock():calls.value+=1
        time.sleep(.1)
        return Exchange()(path,params)
    queue.put(hub.fetch(PATH,query(),source,now_ms=NOW))


def test_cross_process_singleflight(tmp_path):
    ctx=multiprocessing.get_context('spawn')
    barrier,calls,queue=ctx.Barrier(3),ctx.Value('i',0),ctx.Queue()
    workers=[ctx.Process(target=process_read,args=(str(tmp_path),barrier,calls,queue),daemon=True) for _ in range(3)]
    for p in workers:p.start()
    rows=[queue.get(timeout=20) for _ in workers]
    for p in workers:
        p.join(10)
        assert p.exitcode==0
    assert calls.value==1 and rows[0]==rows[1]==rows[2]


def test_open_bar_ttl_and_confirmation_never_inferred(tmp_path):
    hub=CandleHub(tmp_path);calls=[]
    def source(path,params):
        calls.append(params)
        return [row(600_000,'0'),row(540_000)]
    assert hub.fetch(PATH,query(2),source,now_ms=NOW,include_open=True)[0][8]=='0'
    assert len(hub.fetch(PATH,query(2),source,now_ms=NOW+1000,include_open=True))==2
    assert len(calls)==1
    hub.fetch(PATH,query(2),source,now_ms=NOW+3000,include_open=True)
    assert len(calls)==2 and calls[-1]['limit']=='1'
    # An expired forming row never becomes a confirmed bar just by time passing.
    result=hub.fetch(PATH,query(1),lambda *_:[],now_ms=NOW+60_000)
    assert result==[]


def test_exchange_revisions_are_atomic_errors(tmp_path):
    hub=CandleHub(tmp_path);source=Exchange()
    initial=hub.fetch(PATH,query(),source,now_ms=NOW)
    def revision(*_):
        bad=row(540_000);bad[5]='101'
        return [row(600_000),bad]
    with pytest.raises(CandleError,match='revised'):
        hub.fetch(PATH,query(),revision,now_ms=NOW+60_000)
    with hub.db() as db:
        assert db.execute('SELECT max(t) FROM candles').fetchone()[0]==540_000
    assert hub.fetch(PATH,query(),source,now_ms=NOW)==initial


def test_failed_refresh_is_not_stale_success_and_retries_coalesce(tmp_path):
    hub=CandleHub(tmp_path);source=Exchange();calls=[]
    hub.fetch(PATH,query(),source,now_ms=NOW)
    def fail(*_):
        calls.append(1)
        raise CandleError('offline')
    for _ in range(3):
        with pytest.raises(CandleError,match='offline'):
            hub.fetch(PATH,query(),fail,now_ms=NOW+60_000)
    assert len(calls)==1
    assert hub.snapshot()['summary']['errors']==1
    hub.fetch(PATH,query(),source,now_ms=NOW+63_000)
    assert hub.snapshot()['summary']['errors']==0


def test_gap_and_short_response_do_not_fabricate_history(tmp_path):
    hub=CandleHub(tmp_path);calls=[]
    def gap(*_):
        calls.append(1)
        return [row(540_000),row(420_000)]
    result=hub.fetch(PATH,query(),gap,now_ms=NOW)
    assert [r[0] for r in result]==['540000','420000']
    assert hub.fetch(PATH,query(),gap,now_ms=NOW+1000)==result and len(calls)==1
    hub.fetch(PATH,query(),gap,now_ms=NOW+3000)
    assert len(calls)==2


def test_origin_symbol_and_period_are_separate(tmp_path):
    main,other=CandleHub(tmp_path),CandleHub(tmp_path,'https://fixture.invalid')
    source=Exchange()
    main.fetch(PATH,query(),source,now_ms=NOW)
    other.fetch(PATH,query(),source,now_ms=NOW)
    main.fetch(PATH,dict(query(),instId='ETH-USDT-SWAP'),source,now_ms=NOW)
    assert len(source.calls)==3 and main.snapshot()['summary']['series']==3


def test_boundaries_and_exclusive_query(tmp_path):
    assert bar_open(0,'1Dutc')==0
    assert bar_open(0,'1D')==-8*3_600_000
    assert bar_open(4*86_400_000,'1Wutc')==4*86_400_000
    result=CandleHub(tmp_path).fetch(PATH,query(5,after='540000',before='360000'),Exchange(),now_ms=NOW)
    assert [r[0] for r in result]==['480000','420000']


@pytest.mark.parametrize('bad', [row(540001),row(600000),row(540000,'2'),['0']])
def test_invalid_and_future_confirmed_rows_rejected(bad):
    with pytest.raises(CandleError):validate([bad],'1m',NOW)


def test_decimal_precision_is_not_rounded():
    value=row(540000);value[7]='123456789012345678901234567890.123456789'
    assert validate([value],'1m',NOW)[540000][7]==value[7]


def budget_worker(root, barrier, queue):
    hub=CandleHub(root);barrier.wait(timeout=10);hub.acquire('monitor');queue.put(time.time())


def test_rate_limit_is_shared_across_processes(tmp_path):
    ctx=multiprocessing.get_context('spawn');barrier,queue=ctx.Barrier(3),ctx.Queue()
    workers=[ctx.Process(target=budget_worker,args=(str(tmp_path),barrier,queue),daemon=True) for _ in range(3)]
    # Different process ages expose Python 3.9/macOS monotonic epoch differences.
    for p in workers:
        p.start()
        time.sleep(.25)
    stamps=sorted(queue.get(timeout=20) for _ in workers)
    for p in workers:p.join(10);assert p.exitcode==0
    assert all(b-a>=.12 for a,b in zip(stamps,stamps[1:]))


def test_background_budget_leaves_monitor_capacity(tmp_path):
    hub=CandleHub(tmp_path)
    hub.acquire('background');start=time.monotonic()
    hub.acquire('monitor');elapsed=time.monotonic()-start
    assert .12<=elapsed<.30
    hub.acquire('background')
    assert time.monotonic()-start>=.32

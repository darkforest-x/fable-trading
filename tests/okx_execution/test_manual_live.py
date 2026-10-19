"""Offline lifecycle/auth/fault tests; exchange and account are synthetic."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.okx_execution.client import TransportError, Client
from yoyo.okx_execution.config import Config
from yoyo.okx_execution.manual import closed_stop, take_profit, total_account
from yoyo.okx_execution.manual_api import Service, Preview, install
from yoyo.okx_execution.manual_engine import ManualEngine
from yoyo.okx_execution.manual_queue import Queue
from yoyo.okx_execution.risk import D
from yoyo.okx_execution.store import Store
from test_manual_source import Book, JOB, BOX, SYMBOL
from test_client_store import Opener


class Exchange:
    def __init__(self, clock):
        self.clock, self.posts, self.positions = clock, [], []
        self.algo, self.order, self.fail_post = None, None, None
        self.fill = '100'
        self.price = '100'
        self.balance = dict(totalEq='1000', details=[dict(ccy='USDT',eq='1000',eqUsd='1000',availEq='1000',imr='0')])

    def get(self, path, **query):
        if path.endswith('/config'): return [dict(uid='owner',posMode='long_short_mode',acctLv='2')]
        if path.endswith('/balance'): return [deepcopy(self.balance)]
        if path.endswith('/positions'): return deepcopy(self.positions)
        if path.endswith('/orders-pending'): return []
        if path.endswith('/orders-algo-pending'):
            return [deepcopy(self.algo)] if self.algo and self.algo['state']=='live' and query['ordType']=='oco' else []
        if path.endswith('/candles'):
            opening = self.clock()//300000*300000
            return [[str(opening),'100','110','1','100','10','10','10','0']] + [
                [str(opening-(i+1)*300000),'100','102' if i==2 else '101',
                 '98' if i==2 else '99','100','10','10','10','1'] for i in range(5)]
        if path.endswith('/instruments'):
            return [dict(instId=SYMBOL,instFamily='ETH-USDT',instType='SWAP',ctType='linear',settleCcy='USDT',
                         state='live',ctVal='.25',lotSz='1',minSz='1',maxMktSz='10000',tickSz='.01',lever='100')]
        if path.endswith('/position-tiers'): return [dict(minSz='0',maxSz='10000',maxLever='75')]
        if path.endswith('/ticker'): return [dict(askPx=self.price,bidPx=self.price,ts=str(self.clock()))]
        if path.endswith('/leverage-info'): return [dict(instId=SYMBOL,mgnMode='cross',lever='75')]
        if path.endswith('/order-algo'): return [deepcopy(self.algo)]
        if path.endswith('/order'): return [deepcopy(self.order)]
        raise AssertionError(path)

    def post(self, path, body):
        self.posts.append((path,deepcopy(body)))
        if path.endswith('/order'):
            self.positions=[dict(instId=SYMBOL,posSide=body['posSide'],pos=body['sz'],mgnMode='cross')]
            self.order=dict(instId=SYMBOL,posSide=body['posSide'],side=body['side'],clOrdId=body['clOrdId'],
                            state='filled',accFillSz=body['sz'],avgPx=self.fill,fillTime=str(self.clock()),uTime=str(self.clock()))
            attached=body['attachAlgoOrds'][0]
            self.algo=dict(attached,algoId='oco1',algoClOrdId=attached['attachAlgoClOrdId'],state='live',instId=SYMBOL,
                           tdMode='cross',posSide=body['posSide'],side='sell' if body['side']=='buy' else 'buy',sz=body['sz'],ordType='oco')
        if self.fail_post==path: raise TransportError('unknown response')
        if path.endswith('/amend-algos'): self.algo['tpTriggerPx']=body['newTpTriggerPx']
        return [dict(sCode='0',ordId='entry1')]


class Source:
    def events(self,*args):return []
    def bars(self,*args):raise AssertionError('manual lots must not use SPIKE exit bars')
    def checkpoint(self,*args):raise AssertionError('saved detection is not an automatic signal')


@pytest.fixture
def h(tmp_path):
    now=[9_010_000]
    clock=lambda:now[0]
    exchange,store,queue=Exchange(clock),Store(tmp_path),Queue(tmp_path)
    store.set_meta('account_uid','owner');store.set_meta('cutoff_ms',0)
    engine=ManualEngine(Config(),exchange,Source(),store,clock=clock,queue=queue)
    book=Book()
    service=Service(book,tmp_path,engine=engine,clock=clock,healthy=lambda:None)
    service.ma_snapshot=lambda symbol: dict(symbol=symbol, cutoff_ms=clock()//300000*300000, observed_ms=clock(), ma=dict(sma20='100',sma60='99',sma120='98.01',ema20='100',ema60='101',ema120='101.99'))
    return dict(now=now,exchange=exchange,store=store,queue=queue,engine=engine,service=service,book=book)


def tick(h):
    # CLI catches this expected gate while a multi-tick intent reconciles.
    try:
        h['engine'].tick()
    except ValueError as exc:
        if str(exc) != 'unresolved execution intent; reconcile before new entries':
            raise


def preview(h,**kw):
    return h['service'].preview(Preview(job_id=JOB,detection_id=BOX,symbol=SYMBOL,**kw))


def enter(h,**kw):
    d=preview(h,**kw);h['service'].confirm(d['id']);tick(h)
    return h['store'].trades()[0]


def test_preview_uses_full_account_three_percent_and_six_ma_5m_stop_without_post(h):
    d=preview(h)
    assert d['initial_stop']=='98' and d['take_profit']=='106'
    assert D(d['target_risk'])==30 and D(d['planned_risk'])==30
    assert d['size']=='60' and d['leverage']=='75' and D(d['margin'])==20
    assert d['detection']['automatic_entry_eligible'] is False
    assert 'account_uid' not in str(d) and not h['exchange'].posts
    tick(h)
    assert not h['exchange'].posts and not h['store'].trades()


def test_api_market_order_limit_has_actionable_numbers_without_a_draft(h):
    original = h['exchange'].get
    def get(path, **query):
        rows = original(path, **query)
        if path.endswith('/instruments'):
            rows[0]['maxMktSz'] = '50'
        return rows
    h['exchange'].get = get
    app = FastAPI(); install(app, h['book'], service=h['service'])
    with TestClient(app, base_url='https://workspace.example') as client:
        response = client.post('/api/manual-live/preview', json=dict(job_id=JOB, symbol=SYMBOL, detection_id=BOX, rr=3),
                               headers={'Origin':'https://workspace.example', 'X-Fable-Manual':'owner-confirmation'})
    assert response.status_code == 409
    d = response.json()['detail']
    assert d['code'] == 'market_order_size_limit'
    assert d['requested_size'] == '60' and d['max_market_size'] == '50'
    assert d['target_risk'] == '30.00' and d['stop_distance'] == '2'
    assert d['reference']['display_only'] is True
    assert d['reference']['price'] == d['price'] and d['reference']['initial_stop'] == d['stop']
    assert d['reference']['take_profit'] == '106' and 'id' not in d['reference']
    assert 'account_uid' not in str(d) and not h['exchange'].posts
    with h['queue'].connect() as db:
        assert db.execute('SELECT COUNT(*) FROM requests').fetchone()[0] == 0


def test_api_margin_rejection_exposes_budget_without_queuing_or_writing(h):
    original = h['exchange'].get
    def get(path, **query):
        rows = original(path, **query)
        if path.endswith('/balance'):
            rows[0]['details'][0]['availEq'] = '10'
        return rows
    h['exchange'].get = get
    app = FastAPI(); install(app, h['book'], service=h['service'])
    with TestClient(app, base_url='https://workspace.example') as client:
        response = client.post('/api/manual-live/preview', json=dict(job_id=JOB, symbol=SYMBOL, detection_id=BOX),
                               headers={'Origin':'https://workspace.example', 'X-Fable-Manual':'owner-confirmation'})
    d = response.json()['detail']
    assert response.status_code == 409 and d['code'] == 'insufficient_margin'
    assert D(d['required_margin']) == 20 and D(d['usable_margin']) == 10 and D(d['shortfall']) == 10
    assert d['leverage'] == '75' and not h['exchange'].posts
    assert d['reference']['initial_stop'] == '98' and d['reference']['take_profit'] == '106'
    assert d['reference']['display_only'] is True and 'id' not in d['reference']
    with h['queue'].connect() as db:
        assert db.execute('SELECT COUNT(*) FROM requests').fetchone()[0] == 0


def test_chart_route_is_read_only_and_validates_symbol(h):
    h['service'].chart = lambda symbol: dict(symbol=symbol, timeframe='5m', candles=[])
    app = FastAPI(); install(app, h['book'], service=h['service'])
    with TestClient(app) as client:
        assert client.get('/api/manual-live/chart', params={'symbol':SYMBOL}).json()['symbol'] == SYMBOL
        assert client.get('/api/manual-live/chart', params={'symbol':'bad/path'}).status_code == 422
    assert not h['exchange'].posts


def test_reference_works_without_private_account_or_healthy_worker(h, monkeypatch, tmp_path):
    class PublicReads:
        def __init__(self, **kwargs): pass
        def get(self, path, params):
            assert path.startswith('/api/v5/market/') or path == '/api/v5/public/instruments'
            return h['exchange'].get(path, **params)
    def forbidden(): raise AssertionError('reference must not access private engine')
    monkeypatch.setattr('yoyo.monitor.okx.OKX', PublicReads)
    h['book'].runtime = tmp_path / 'yolo'
    h['service'].engine = forbidden
    h['service']._healthy = forbidden
    app = FastAPI(); install(app, h['book'], service=h['service'])
    headers={'Origin':'https://workspace.example','X-Fable-Manual':'owner-confirmation'}
    payload=dict(job_id=JOB,symbol=SYMBOL,detection_id=BOX,rr=5)
    with TestClient(app, base_url='https://workspace.example') as client:
        response=client.post('/api/manual-live/reference',json=payload,headers=headers)
        assert response.status_code == 200
        d=response.json()
        assert d['display_only'] is True and d['take_profit'] == '110'
        assert 'id' not in d and 'queue_state' not in d
        assert client.post('/api/manual-live/reference',json=payload).status_code == 403
        assert client.post('/api/manual-live/reference',json={**payload,'rr':4},headers=headers).status_code == 409
    assert not h['exchange'].posts
    with h['queue'].connect() as db:
        assert db.execute('SELECT COUNT(*) FROM requests').fetchone()[0] == 0


@pytest.mark.parametrize('ahead,oversleep,ok', [(571, 0, True), (5000, 0, True), (5001, 0, False), (571, 6000, False), (571, 60000, False)])
def test_small_clock_lead_waits_without_relaxing_quote_or_preview_expiry(h, monkeypatch, ahead, oversleep, ok):
    original = h['exchange'].get
    waits = []
    def get(path, **query):
        rows = original(path, **query)
        if path.endswith('/ticker'):
            rows[0]['ts'] = str(h['now'][0] + ahead)
        return rows
    def sleep(seconds):
        waits.append(seconds)
        h['now'][0] += round(seconds * 1000) + oversleep
    h['exchange'].get = get
    monkeypatch.setattr('yoyo.okx_execution.manual.time.sleep', sleep)
    if ok:
        result = preview(h)
        plan = h['queue'].get(result['id'])['plan']
        assert 0 <= plan['quote_age_ms'] <= 5000
        assert result['expires_ms'] - h['queue'].get(result['id'])['event']['preview_ms'] == 60000
    else:
        with pytest.raises(ValueError, match='expired'):
            preview(h)
    assert bool(waits) is (ahead <= 5000)
    assert not h['exchange'].posts


def test_total_equity_converts_usd_to_usdt_including_other_account_assets(h):
    h['exchange'].balance.update(totalEq='2002')
    h['exchange'].balance['details'][0].update(eqUsd='1001')
    assert D(total_account(h['exchange'])['total_equity_usdt'])==2000
    assert D(preview(h)['target_risk'])==60


@pytest.mark.parametrize('side,rr,sl,tp',[('long',3,'98','106'),('long',5,'98','110'),('short',3,'102','94'),('short',5,'102','90')])
def test_direction_rr_and_fixed_protection_lifecycle(h,side,rr,sl,tp):
    h['book'].value['raw_detections'][0]['side']=side
    row=enter(h,rr=rr)
    assert row['initial_stop']==sl and row['take_profit']==tp
    req=h['exchange'].posts[-1][1]
    assert req['ordType']=='market' and 'px' not in req
    assert req['attachAlgoOrds'][0]['tpTriggerPx']==tp
    assert req['attachAlgoOrds'][0]['slTriggerPx']==sl
    tick(h);tick(h);tick(h)
    row=h['store'].trades()[0]
    assert row['state']=='active' and row['tp_basis']=='actual_fill'
    assert h['service'].status(row['id'])['protection_verified'] is True
    assert len(h['exchange'].posts)==2  # leverage + one order, never trailing


def test_fill_price_rebases_target_and_unknown_amendment_is_never_repeated(h):
    h['exchange'].fill='100.10'
    h['exchange'].fail_post='/api/v5/trade/amend-algos'
    row=enter(h)
    tick(h);tick(h)
    row=h['store'].trades()[0]
    assert row['state']=='manual_tp_pending' and D(row['tp_target'])==D('106.40')
    assert D(row['actual_risk'])==D('31.50')  # actual slippage is reported honestly
    tick(h)
    assert len([p for p in h['exchange'].posts if p[0].endswith('amend-algos')])==1
    h['exchange'].algo['tpTriggerPx']='106.40'
    tick(h)
    assert h['store'].trades()[0]['tp_basis']=='actual_fill'


def test_restart_between_protection_and_target_calibration_recovers(h):
    h['exchange'].fill='100.1'
    row=enter(h);h['engine'].manage(row)
    row.update(state='active',algo_id='oco1')
    h['store'].save(row)
    tick(h);tick(h)
    assert D(h['store'].trades()[0]['take_profit'])==D('106.4')


def test_missing_tp_is_not_protected_and_requests_owned_exit(h):
    row=enter(h);tick(h)
    h['exchange'].algo['tpTriggerPx']=''
    h['now'][0]+=11_000
    tick(h)
    assert h['store'].trades()[0]['state']=='exit_needed'
    assert not h['service'].status(row['id'])['protection_verified']


def test_repeated_confirmation_concurrency_and_unknown_entry_never_duplicate(h):
    d=preview(h)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:h['service'].confirm(d['id']),range(2)))
    h['exchange'].fail_post='/api/v5/trade/order'
    with pytest.raises(TransportError):tick(h)
    h['service'].confirm(d['id'])
    tick(h);tick(h)
    assert h['store'].trades()[0]['state']=='active'
    assert len([p for p in h['exchange'].posts if p[0].endswith('/order')])==1


def test_superseded_and_expired_previews_cannot_confirm(h):
    first=preview(h);second=preview(h,rr=5)
    with pytest.raises(ValueError,match='superseded'):h['service'].confirm(first['id'])
    h['now'][0]=second['expires_ms']
    with pytest.raises(ValueError,match='expired'):h['service'].confirm(second['id'])
    tick(h)
    assert not h['exchange'].posts


def test_confirmed_but_expired_before_execution_is_skipped_without_mutation(h):
    d=preview(h);h['service'].confirm(d['id']);h['now'][0]=d['expires_ms']
    tick(h)
    assert not h['exchange'].posts and h['store'].trades()[0]['state']=='skipped'


def test_preview_cannot_be_passed_directly_to_enter(h):
    d=preview(h);raw=h['queue'].get(d['id'])
    with pytest.raises(ValueError,match='confirmation'):h['engine'].enter(raw['plan'])
    assert not h['exchange'].posts


def test_execution_never_increases_reviewed_size_margin_or_risk(h):
    d=preview(h);h['service'].confirm(d['id'])
    h['exchange'].balance['totalEq']='2000'
    h['exchange'].price='99.99'
    tick(h);row=h['store'].trades()[0]
    assert D(row['size'])<=D(d['size']) and D(row['margin'])<=D(d['margin'])
    assert D(row['planned_risk'])<=D(d['target_risk'])


@pytest.mark.parametrize('rr',[0,2,4,6])
def test_unapproved_rr_rejected_without_any_order(h,rr):
    with pytest.raises(ValueError):preview(h,rr=rr)
    assert not h['exchange'].posts


@pytest.mark.parametrize('stop',['0','100','101'])
def test_invalid_long_stop_fails_before_confirmation(h,stop):
    with pytest.raises(ValueError):preview(h,stop=stop)
    assert not h['exchange'].posts


@pytest.mark.parametrize('actual,reason',[('tp','exchange_take_profit'),('sl','exchange_stop')])
def test_trigger_receipt_labels_tp_and_sl(h,actual,reason):
    row=enter(h);tick(h);tick(h)
    h['exchange'].algo.update(state='effective',actualSide=actual,ordIdList=['exit1'])
    h['exchange'].order.update(side='sell',avgPx='106' if actual=='tp' else '98')
    h['exchange'].positions=[]
    tick(h);tick(h)
    assert h['store'].trades()[0]['exit_reason']==reason


def test_api_remote_workspace_allowed_but_csrf_and_payload_injection_rejected(h):
    app=FastAPI();install(app,h['book'],service=h['service'])
    with TestClient(app,base_url='https://workspace.example') as client:
        data=dict(job_id=JOB,symbol=SYMBOL,detection_id=BOX,rr=3)
        assert client.post('/api/manual-live/preview',json=data).status_code==403
        headers={'Origin':'https://workspace.example','X-Fable-Manual':'owner-confirmation'}
        assert client.post('/api/manual-live/preview',json=dict(data,size='100000'),headers=headers).status_code==422
        response=client.post('/api/manual-live/preview',json=data,headers=headers)
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        d=response.json();url=f"/api/manual-live/{d['id']}/confirm"
        assert client.post(url,json={'acknowledgement':'CONFIRM-LIVE-ORDER','rr':5},headers=headers).status_code==422
        assert client.post(url,json={'acknowledgement':'CONFIRM-LIVE-ORDER'},headers=dict(headers,Origin='https://attacker.example')).status_code==403
        assert client.post(url,json={'acknowledgement':'CONFIRM-LIVE-ORDER'},headers=headers).json()['state']=='queued'
        assert not h['exchange'].posts  # Web routes cannot place exchange orders.
        assert 'account_uid' not in response.text and 'approved_' not in response.text


def test_candle_arrays_supported_only_on_public_candle_endpoint():
    payload=dict(code='0',data=[['0','1','2','1','2','3','3','3','1']])
    c=Client(Config(),opener=Opener(payload))
    assert c.get('/api/v5/market/candles')[0][8]=='1'
    with pytest.raises(TransportError):c.get('/api/v5/market/ticker')


def test_developing_or_old_candles_cannot_supply_default_stop(h,monkeypatch):
    monkeypatch.setattr(h['exchange'],'get',lambda *a,**kw:[['8700000','100','102','98','100','1','1','1','0']])
    with pytest.raises(ValueError,match='unavailable'):closed_stop(h['exchange'],SYMBOL,'long',lambda:9_010_000)


def test_tp_rounding_is_toward_entry_and_must_remain_positive():
    assert D(take_profit('1.003','1','long',3,'.01'))==D('1.01')
    assert D(take_profit('1.003','1.01','short',3,'.01'))==D('.99')
    with pytest.raises(ValueError):take_profit('1','2','short',5,'.01')


def test_expired_confirmed_attempt_requires_new_preview_and_new_click(h):
    first=preview(h);h['service'].confirm(first['id']);h['now'][0]=first['expires_ms'];tick(h)
    second=preview(h)
    assert second['id']!=first['id'] and not h['exchange'].posts
    assert h['service'].confirm(first['id'])['state']=='skipped'
    tick(h);assert not h['exchange'].posts
    h['service'].confirm(second['id']);tick(h)
    assert len([p for p in h['exchange'].posts if p[0].endswith('/order')])==1


def test_stale_protection_receipt_is_not_shown_as_verified(h):
    row=enter(h);tick(h);tick(h)
    assert h['service'].status(row['id'])['protection_verified']
    h['now'][0]+=60_000
    assert not h['service'].status(row['id'])['protection_verified']


def test_expiry_during_leverage_readback_cannot_submit_entry(h,monkeypatch):
    d=preview(h);h['service'].confirm(d['id']);original=h['exchange'].get
    def get(path,**query):
        result=original(path,**query)
        if path.endswith('leverage-info'):h['now'][0]=d['expires_ms']
        return result
    monkeypatch.setattr(h['exchange'],'get',get)
    tick(h)
    assert h['store'].trades()[0]['state']=='skipped'
    assert [p[0] for p in h['exchange'].posts]==['/api/v5/account/set-leverage']


@pytest.mark.parametrize('delta',[{'state':'canceled'},{'tpTriggerPx':''},{'slTriggerPx':'0'}])
def test_lost_or_invalid_protection_during_target_amendment_exits_owned_lot(h,delta):
    h['exchange'].fill='100.1';h['exchange'].fail_post='/api/v5/trade/amend-algos'
    enter(h);tick(h);tick(h)
    h['exchange'].algo.update(delta)
    tick(h)
    assert h['store'].trades()[0]['state']=='exit_needed'


def test_foreign_algo_response_blocks_mutation_instead_of_canceling_it(h):
    enter(h);tick(h);tick(h)
    h['exchange'].algo['algoClOrdId']='foreign'
    before=len(h['exchange'].posts)
    tick(h)
    assert len(h['exchange'].posts)==before and h['store'].meta('errors')


def test_slow_obsolete_preview_cannot_supersede_new_visible_quote(h):
    from threading import Event
    started, release = Event(), Event()
    service=h['service']; original=service._preview
    def delayed(payload, engine, context, **kwargs):
        if payload.rr==3:
            started.set()
            assert release.wait(3)
        return original(payload,engine,context,**kwargs)
    service._preview=delayed
    payload=dict(job_id=JOB,symbol=SYMBOL,detection_id=BOX,stop='98')
    with ThreadPoolExecutor(2) as pool:
        old=pool.submit(service.preview,Preview(**payload,rr=3))
        assert started.wait(3)
        try:
            current=service.preview(Preview(**payload,rr=5))
        finally:
            release.set()
        with pytest.raises(ValueError,match='superseded'):
            old.result(3)
    assert Queue(service.runtime).get(current['id'])['queue_state']=='preview'
    assert service._previews=={} and not h['exchange'].posts

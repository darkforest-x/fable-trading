"""An external combined close is ledger evidence, never permission to send orders."""
from copy import deepcopy
import pytest
from test_manual_inventory import shared
from test_manual_live import h, preview, tick, SYMBOL
from yoyo.okx_execution.manual_reconciliation import external_flattening
from yoyo.okx_execution.manual_readiness import entry_status
from yoyo.okx_execution.manual_engine import MANUAL_ENGINE_REVISION
from yoyo.okx_execution.dashboard import trade_row


@pytest.fixture
def flattened(shared):
    d = preview(shared)
    shared['service'].confirm(d['id'])
    for _ in range(4):
        tick(shared)
    trade = shared['store'].trades()[0]
    shared['exchange'].algo['state'] = 'canceled'
    shared['exchange'].positions = []
    closing = dict(ordId='external-close', instId=SYMBOL, posSide='long', side='sell',
                   state='filled', reduceOnly='true', accFillSz='600', sz='600',
                   avgPx='101', pnl='150', fee='-1', feeCcy='USDT',
                   cTime=str(trade['entry_ms']+100), uTime=str(trade['entry_ms']+100))
    get = shared['exchange'].get
    def read(path, **q):
        if path.endswith('/orders-history'):
            return deepcopy(shared['history'])
        return get(path, **q)
    shared['history'] = [closing]
    shared['exchange'].get = read
    shared['engine'].manage(trade)
    assert trade['state'] == 'exit_needed'
    shared['trade'] = trade
    shared['posts_before'] = deepcopy(shared['exchange'].posts)
    return shared


def test_combined_external_close_reconciles_without_post_or_false_pnl(flattened):
    h = flattened
    h['engine'].tick()
    row = h['store'].trades()[0]
    assert row['state'] == 'closed' and row['exit_reason'] == 'external_flattened'
    assert row['external_close']['close_orders'][0]['accFillSz'] == '600'
    assert row['entry_order'] and not row.get('exit_order') and not row.get('exit_price')
    assert trade_row(row)['realized_net_usdt'] is None
    assert not h['store'].meta('errors') and h['exchange'].posts == h['posts_before']
    h['engine'].tick()
    assert h['exchange'].posts == h['posts_before']


@pytest.mark.parametrize('change', ['position', 'opposite', 'pending', 'algo', 'live_protection',
                                    'missing_history', 'old_close', 'wrong_side', 'partial', 'not_reduce',
                                    'too_small', 'foreign_instrument', 'sibling', 'unknown_exit'])
def test_incomplete_proof_never_closes_or_writes(flattened, change):
    h = flattened; t = h['trade']
    if change in ('position','opposite'):
        h['exchange'].positions = [dict(instId=SYMBOL, posSide='long' if change=='position' else 'short',pos='1',mgnMode='cross')]
    if change=='pending': h['outside_orders'].append(dict(instId=SYMBOL,ordId='outside'))
    if change=='algo': h['outside_algos'].append(dict(instId=SYMBOL,algoId='outside',ordType='oco'))
    if change=='live_protection': h['exchange'].algo['state']='live'
    if change=='missing_history': h['history']=[]
    if change=='old_close': h['history'][0]['cTime']=str(t['entry_ms']-1)
    if change=='wrong_side': h['history'][0]['posSide']='short'
    if change=='partial': h['history'][0]['state']='partially_filled'
    if change=='not_reduce': h['history'][0]['reduceOnly']='false'
    if change=='too_small': h['history'][0]['accFillSz']='1'
    if change=='foreign_instrument': h['history'][0]['instId']='BTC-USDT-SWAP'
    if change=='sibling': h['store'].save(dict(t,id='another'))
    if change=='unknown_exit': t['exit_id']='pending-exit'
    assert external_flattening(h['engine'], t) is None
    assert h['exchange'].posts == h['posts_before']
    assert h['store'].trades()[0]['state']=='exit_needed'


def test_second_account_read_catches_reappearing_position(flattened):
    h=flattened; original=h['engine'].account; reads=[]
    def account():
        result=original();reads.append(1)
        if len(reads)>1: result['positions']=[dict(instId=SYMBOL,pos='1')]
        return result
    h['engine'].account=account
    assert external_flattening(h['engine'], h['trade']) is None
    assert len(reads)==2 and h['exchange'].posts == h['posts_before']


def test_failed_read_does_not_become_an_empty_position(flattened):
    h=flattened
    def broken(): raise ValueError('account read failed')
    h['engine'].account=broken
    with pytest.raises(ValueError,match='account read failed'):
        external_flattening(h['engine'], h['trade'])
    assert h['store'].trades()[0]['state']=='exit_needed'
    assert h['exchange'].posts == h['posts_before']


def test_readiness_names_blocking_symbol_without_raw_errors(flattened):
    h=flattened;s=h['store'];now=h['now'][0]
    s.set_meta('engine_revision',MANUAL_ENGINE_REVISION);s.set_meta('heartbeat_ms',now);s.set_meta('entries_enabled',True)
    s.set_meta('errors',[dict(id=h['trade']['id'],error='exchange positions do not match owned signal lots')])
    result=entry_status(s,now,True)
    assert result['status']=='blocked' and 'ETH' in result['message']
    assert result['issues'][0]['code']=='position_mismatch'
    s.set_meta('errors',[dict(id=h['trade']['id'],error='SECRET_SENTINEL')])
    assert 'SECRET_SENTINEL' not in str(entry_status(s,now,True))
    s.set_meta('errors',[])
    assert entry_status(s,now,True)['status']=='ready'
    assert entry_status(s,now+60000,True)['code']=='worker_stale'
    assert entry_status(s,now,False)['code']=='worker_stopped'


def test_manual_readiness_does_not_share_automatic_revision(h):
    from yoyo.okx_execution.engine import ENGINE_REVISION
    s=h['store'];now=h['now'][0]
    s.set_meta('engine_revision',ENGINE_REVISION)
    s.set_meta('heartbeat_ms',now);s.set_meta('entries_enabled',True)
    assert entry_status(s,now,True)['code']=='worker_version'
    s.set_meta('engine_revision',MANUAL_ENGINE_REVISION)
    assert entry_status(s,now,True)['code']=='ready'


def test_api_returns_structured_blocker_and_get_does_not_create_a_draft(flattened):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yoyo.okx_execution.manual_api import install
    from test_manual_source import JOB, BOX
    h=flattened;s=h['store'];service=h['service'];service._healthy=None
    s.set_meta('engine_revision',MANUAL_ENGINE_REVISION)
    s.set_meta('heartbeat_ms',h['now'][0]);s.set_meta('entries_enabled',True)
    s.set_meta('errors',[dict(id=h['trade']['id'],error='exchange positions do not match owned signal lots')])
    app=FastAPI();install(app,h['book'],service=service)
    with s.lock(),TestClient(app) as client:
        status=client.get('/api/manual-live/readiness').json()
        assert status['code']=='reconciliation_blocked' and status['issues'][0]['symbol']==SYMBOL
        response=client.post('/api/manual-live/preview',json=dict(job_id=JOB,detection_id=BOX,symbol=SYMBOL,rr=3),
            headers={'Origin':'http://testserver','X-Fable-Manual':'owner-confirmation'})
        assert response.status_code==409 and response.json()['detail']==status
    assert len(s.trades())==1 and h['exchange'].posts==h['posts_before']


def test_worker_read_failure_is_not_reported_as_an_old_order(h):
    s=h['store'];now=h['now'][0]
    s.set_meta('engine_revision',MANUAL_ENGINE_REVISION)
    s.set_meta('heartbeat_ms',now);s.set_meta('entries_enabled',True)
    s.set_meta('errors',[]);s.set_meta('worker_error','PRIVATE_NETWORK_SENTINEL')
    result=entry_status(s,now,True)
    assert result['status']=='blocked' and result['code']=='worker_check_failed'
    assert '旧单' not in result['title'] and 'PRIVATE_' not in str(result)
    s.set_meta('worker_error',None)
    assert entry_status(s,now,True)['status']=='ready'


@pytest.mark.parametrize('location', ['worker', 'orders', 'mixed'])
def test_transport_retry_is_distinct_from_an_inventory_conflict(h, location):
    from yoyo.okx_execution.manual_readiness import READ_RETRY
    s=h['store'];now=h['now'][0]
    s.set_meta('engine_revision',MANUAL_ENGINE_REVISION)
    s.set_meta('heartbeat_ms',now);s.set_meta('entries_enabled',True)
    if location=='worker':s.set_meta('worker_error',READ_RETRY)
    else:
        failures=[dict(id='one',error=READ_RETRY)]
        if location=='mixed':failures.append(dict(id='two',error='exchange positions do not match owned signal lots'))
        s.set_meta('errors',failures)
    result=entry_status(s,now,True)
    assert result['status']=='blocked'
    assert result['code']==('reconciliation_blocked' if location=='mixed' else 'exchange_read_retry')


def test_multiple_owned_lots_require_all_protections_and_combined_size(flattened):
    h=flattened;t=h['trade'];other=dict(t,id='second',algo_id='oco2',entry_ms=t['entry_ms']+1)
    h['store'].save(other)
    original=h['engine'].algo
    h['engine'].algo=lambda trade: dict(original(t),algoId=trade['algo_id'])
    h['history'][0]['accFillSz']=str(float(t['size'])+float(other['size']))
    proof=external_flattening(h['engine'],t)
    assert proof and set(proof['sibling_ids'])=={t['id'],'second'}
    assert proof['economics']=='unattributed_external_close'
    h['history'][0]['accFillSz']=t['size']
    assert external_flattening(h['engine'],t) is None
    h['history'][0]['accFillSz']='600'
    h['engine'].algo=lambda trade: dict(original(t),state='live' if trade['id']=='second' else 'canceled')
    assert external_flattening(h['engine'],t) is None
    assert h['exchange'].posts==h['posts_before']

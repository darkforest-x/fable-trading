"""Two accounts and owner-managed inventory never share execution ownership."""
from copy import deepcopy
import pytest
from test_manual_live import h, preview, tick, SYMBOL, Book
from yoyo.okx_execution.manual_api import Service
from yoyo.okx_execution.manual_inventory import pages, runtime_policy
from yoyo.okx_execution.config import DEFAULT_RUNTIME, DEFAULT_MANUAL_RUNTIME
from yoyo.okx_execution.dashboard import Dashboard


@pytest.fixture
def shared(h):
    h['store'].set_meta('manual_account_policy',dict(version=1,mode='manual_only_shared',account_uid='owner'))
    external=dict(instId='FIL-USDT-SWAP',posSide='long',pos='8389',mgnMode='cross')
    h['exchange'].positions=[external]
    h['outside_orders']=[dict(instId='BTC-USDT-SWAP',ordId='owner-limit')]
    h['outside_algos']=[dict(instId='FIL-USDT-SWAP',algoId='owner-stop',ordType='oco')]
    original_get,original_post=h['exchange'].get,h['exchange'].post
    def get(path,**query):
        if path.endswith('/orders-pending'):return deepcopy(h['outside_orders'])
        result=original_get(path,**query)
        if path.endswith('/orders-algo-pending'):
            result+=deepcopy([r for r in h['outside_algos'] if r['ordType']==query['ordType']])
        return result
    def post(path,body):
        result=original_post(path,body)
        if path.endswith('/order'):h['exchange'].positions.append(deepcopy(external))
        return result
    h['exchange'].get=get;h['exchange'].post=post
    return h


def test_routes_default_to_separate_private_runtimes():
    assert DEFAULT_RUNTIME != DEFAULT_MANUAL_RUNTIME
    assert Service(Book()).runtime == DEFAULT_MANUAL_RUNTIME
    dashboard=Dashboard()
    assert dashboard.runtime == DEFAULT_RUNTIME
    dashboard.close()


def test_runtime_role_cannot_fall_back_to_automatic_on_manual_metadata_loss(h):
    assert runtime_policy(h['store'], DEFAULT_RUNTIME) is None
    with pytest.raises(ValueError, match='not configured'):
        runtime_policy(h['store'], DEFAULT_MANUAL_RUNTIME)
    h['store'].set_meta('manual_account_policy', dict(version=1, mode='manual_only_shared', account_uid='owner'))
    assert runtime_policy(h['store'], DEFAULT_MANUAL_RUNTIME)['mode'] == 'manual_only_shared'
    with pytest.raises(ValueError, match='cannot consume'):
        runtime_policy(h['store'], DEFAULT_RUNTIME)


def test_shared_account_does_not_scan_or_enter_automatic_signals(shared):
    def forbidden(*a):raise AssertionError('manual account must never scan automatic signals')
    shared['engine'].source.events=forbidden
    tick(shared)
    assert not shared['exchange'].posts
    assert not shared['store'].trades()


def test_policy_bound_to_account_identity(shared):
    shared['store'].set_meta('manual_account_policy',dict(version=1,mode='manual_only_shared',account_uid='different'))
    with pytest.raises(ValueError,match='different account'):preview(shared)
    assert not shared['exchange'].posts


@pytest.mark.parametrize('kind',['position','opposite','order','algo'])
def test_no_entry_into_owner_occupied_instrument(shared,kind):
    if kind in ('position','opposite'):
        shared['exchange'].positions.append(dict(instId=SYMBOL,posSide='short' if kind=='opposite' else 'long',pos='1',mgnMode='cross'))
    elif kind=='order':shared['outside_orders'].append(dict(instId=SYMBOL,ordId='owner2'))
    else:shared['outside_algos'].append(dict(instId=SYMBOL,algoId='owner2',ordType='oco'))
    with pytest.raises(ValueError,match='owner-managed'):preview(shared)
    assert not shared['exchange'].posts


@pytest.mark.parametrize('rr',[3,5])
def test_confirmed_lifecycle_preserves_external_inventory(shared,rr):
    d=preview(shared,rr=rr)
    assert d['account_scope']=='yolo_manual' and float(d['target_risk'])==30
    assert not shared['exchange'].posts
    shared['service'].confirm(d['id'])
    for _ in range(4):tick(shared)
    row=shared['store'].trades()[0]
    assert row['state']=='active'
    assert shared['service'].status(d['id'])['protection_verified'] is True
    assert any(p['instId']=='FIL-USDT-SWAP' and p['pos']=='8389' for p in shared['exchange'].positions)
    assert all(body['instId']==SYMBOL for _,body in shared['exchange'].posts)
    assert shared['outside_algos'][0]['algoId']=='owner-stop'
    assert shared['outside_orders'][0]['ordId']=='owner-limit'


def test_owner_order_arriving_after_preview_blocks_before_leverage(shared):
    d=preview(shared);shared['service'].confirm(d['id'])
    shared['outside_orders'].append(dict(instId=SYMBOL,ordId='new-owner-order'))
    tick(shared)
    assert not shared['exchange'].posts
    assert shared['store'].trades()[0]['state']=='skipped'


def test_owner_position_arriving_during_leverage_prevents_entry(shared):
    d=preview(shared);shared['service'].confirm(d['id'])
    old=shared['exchange'].get
    def get(path,**q):
        if path.endswith('/leverage-info'):
            shared['exchange'].positions.append(dict(instId=SYMBOL,posSide='long',pos='1',mgnMode='cross'))
        return old(path,**q)
    shared['exchange'].get=get
    tick(shared)
    assert [path for path,_ in shared['exchange'].posts]==['/api/v5/account/set-leverage']
    assert shared['store'].trades()[0]['state']=='skipped'


@pytest.mark.parametrize('change',['quantity','opposite','order','algo'])
def test_owned_instrument_conflict_blocks_discretionary_mutations(shared,change):
    d=preview(shared);shared['service'].confirm(d['id'])
    for _ in range(4):tick(shared)
    posts=len(shared['exchange'].posts)
    if change=='quantity':shared['exchange'].positions[0]['pos']='61'
    if change=='opposite':shared['exchange'].positions.append(dict(instId=SYMBOL,posSide='short',pos='1',mgnMode='cross'))
    if change=='order':shared['outside_orders'].append(dict(instId=SYMBOL,ordId='unexpected'))
    if change=='algo':shared['outside_algos'].append(dict(instId=SYMBOL,algoId='unexpected',ordType='oco'))
    with pytest.raises(ValueError):shared['engine'].assert_owned_quantity()
    assert len(shared['exchange'].posts)==posts


def test_pending_pagination_cannot_hide_an_external_instrument():
    class Client:
        def get(self,path,**query):
            if query.get('after'):return [dict(ordId='last',instId=SYMBOL)]
            return [dict(ordId=str(i),instId='BTC-USDT-SWAP') for i in range(100)]
    rows=list(pages(Client(),'/api/v5/trade/orders-pending','ordId'))
    assert len(rows)==101 and rows[-1]['instId']==SYMBOL

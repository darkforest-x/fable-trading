"""Owner exposure defaults, independent of margin leverage and stop distance."""
from copy import deepcopy
import pytest
from test_free_manual import h, TERMS, preview
from yoyo.okx_execution.free_manual import make_plan, consume
from yoyo.contracts.manual_exposure import allowance


def venue_symbols(h):
    original=h.venue.get
    def get(path,**q):
        rows=original(path,**q)
        if path.endswith('/instruments'):
            rows[0].update(instId=q['instId'],instFamily=q['instId'].replace('-SWAP',''),ctValCcy=q['instId'].split('-')[0])
        if path.endswith('/ticker'):rows[0]['instId']=q['instId']
        return rows
    h.venue.get=get


def test_max_leverage_uses_quantity_tier_and_total_equity_not_available(h):
    get=h.venue.get
    def reads(path,**q):
        rows=get(path,**q)
        if path.endswith('/balance'):rows[0]['details'][0]['availEq']='200'
        return rows
    h.venue.get=reads
    a=make_plan(h.engine,dict(TERMS,leverage=None,notional_usdt=None))
    b=make_plan(h.engine,dict(TERMS,leverage=None,notional_usdt=None,stop_loss='50'))
    assert float(a['notional'])==10000 and a['notional']==b['notional']
    assert a['leverage']=='75' and float(a['margin'])==pytest.approx(10000/75)
    smaller=make_plan(h.engine,dict(TERMS,leverage='60',notional_usdt=None))
    assert smaller['size']==a['size'] and float(smaller['margin'])>float(a['margin'])
    assert not h.venue.posts


def test_independent_major_pools_and_shared_alts_gross():
    existing=[('BTC-USDT-SWAP','8000'),('ETH-USDT-SWAP','3000'),('SOL-USDT-SWAP','2000'),('W-USDT-SWAP','1000')]
    assert allowance('BTC-USDT-SWAP',1000,existing)['exposure_remaining']=='2000'
    assert allowance('ETH-USDT-SWAP',1000,existing)['exposure_remaining']=='7000'
    assert allowance('DOGE-USDT-SWAP',1000,existing)['exposure_remaining']=='2000'


def test_positions_pending_open_orders_and_local_intents_consume_shared_pool(h):
    venue_symbols(h);get=h.venue.get
    h.venue.positions=[dict(instId='SOL-USDT-SWAP',pos='1000',markPx='100')]
    def reads(path,**q):
        if path.endswith('/orders-pending'):
            return [dict(instId='W-USDT-SWAP',ordId='order',clOrdId='external',posSide='short',side='sell',sz='500',accFillSz='100',px='100')]
        return get(path,**q)
    h.venue.get=reads
    t=dict(TERMS,symbol='DOGE-USDT-SWAP',notional_usdt=None,leverage=None)
    d=make_plan(h.engine,t)
    assert float(d['exposure_used'])==1400 and float(d['notional'])==3600
    a=h.book.preview(d);h.book.confirm(a['id'],h.now[0],'owner')
    with pytest.raises(ValueError):make_plan(h.engine,dict(t,symbol='ADA-USDT-SWAP'))
    assert not h.venue.posts


def test_two_stale_previews_cannot_reserve_same_alt_allowance(h):
    venue_symbols(h)
    a=preview(h,symbol='SOL-USDT-SWAP',notional_usdt='4000',ticket_id='b'*32)
    b=preview(h,symbol='ADA-USDT-SWAP',notional_usdt='4000',ticket_id='c'*32)
    h.book.confirm(a['id'],h.now[0],'owner')
    with pytest.raises(ValueError,match='占用'):h.book.confirm(b['id'],h.now[0],'owner')
    assert not h.venue.posts


def test_worker_rechecks_equity_before_any_exchange_write(h):
    a=preview(h,notional_usdt='9000');h.book.confirm(a['id'],h.now[0],'owner')
    get=h.venue.get
    def reads(path,**q):
        rows=get(path,**q)
        if path.endswith('/balance'):rows[0]['details'][0]['eq']='500'
        return rows
    h.venue.get=reads;consume(h.engine,h.book,entries=True)
    assert h.book.get(a['id'])['state']=='rejected' and not h.venue.posts

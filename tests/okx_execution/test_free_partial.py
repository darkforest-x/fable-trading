"""Synthetic receipts: partial exits keep ownership and exact remaining protection."""
from copy import deepcopy
import pytest
from test_free_manual import h
from test_free_management import setup
from yoyo.okx_execution.free_management import Management, consume
from yoyo.okx_execution.client import TransportError, APIError


def confirmed(h, book, ticket, **kw):
    q=book.preview(h.engine,ticket['id'],**kw)
    assert not h.venue.posts
    book.confirm(q['id'],h.now[0])
    return q


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('protected',[True,False])
def test_partial_then_full_exit_retains_manageable_remainder(h,side,protected):
    t,b=setup(h,side=side,protection=protected)
    q=confirmed(h,b,t,action='close',percentage='25')
    for _ in range(3):consume(h.engine,b)
    assert b.get(q['id'])['state']=='done'
    assert float(h.venue.positions[0]['pos'])==75
    saved=h.book.get(t['id']);assert float(saved['management_closed_size'])==25
    assert not saved.get('management_closed_ms')
    if protected:
        assert float(h.venue.algo['sz'])==75
        assert h.venue.algo['tpTriggerPx']==('120' if side=='long' else '80')
    assert h.venue.posts[0][1]['side']==('sell' if side=='long' else 'buy')
    h.venue.posts.clear()
    end=confirmed(h,b,t,action='close',percentage='100')
    consume(h.engine,b)
    assert b.get(end['id'])['state']=='done'
    assert not h.venue.positions and h.book.get(t['id'])['management_closed_ms']


@pytest.mark.parametrize('percentage',['0','101','0.01','NaN'])
def test_invalid_or_dust_partial_is_rejected_before_any_write(h,percentage):
    t,b=setup(h)
    with pytest.raises(ValueError):b.preview(h.engine,t['id'],'close',percentage=percentage)
    assert not h.venue.posts


@pytest.mark.parametrize('accepted',[True,False])
def test_ambiguous_resize_never_recloses_or_reposts(h,accepted):
    t,b=setup(h);q=confirmed(h,b,t,action='close',percentage='50')
    post=h.venue.post
    def write(path,body):
        if path.endswith('/amend-algos'):
            if accepted:post(path,body)
            else:h.venue.posts.append((path,deepcopy(body)))
            raise TransportError('lost resize response')
        return post(path,body)
    h.venue.post=write
    for _ in range(4):consume(h.engine,Management(h.runtime))
    assert len(h.venue.posts)==2
    assert b.get(q['id'])['state']==('done' if accepted else 'processing')
    assert float(h.venue.positions[0]['pos'])==50


def test_protection_edit_verifies_both_exits_and_allows_manual_removal(h):
    t,b=setup(h);q=confirmed(h,b,t,action='protection',stop='95',take_profit='130')
    consume(h.engine,b);assert b.get(q['id'])['state']=='done'
    assert h.book.get(t['id'])['take_profit']=='130'
    h.venue.posts.clear();q=confirmed(h,b,t,action='protection')
    consume(h.engine,b);assert b.get(q['id'])['state']=='done'
    assert h.book.get(t['id'])['management_no_protection']
    h.venue.posts.clear();q=confirmed(h,b,t,action='protection',stop='92',take_profit='125')
    consume(h.engine,b);assert b.get(q['id'])['state']=='done'
    assert h.venue.algo['ordType']=='oco'


def test_partial_rejects_changed_protection_and_position(h):
    t,b=setup(h);q=confirmed(h,b,t,action='close',percentage='50')
    h.venue.positions[0]['pos']='90';consume(h.engine,b)
    assert b.get(q['id'])['state']=='rejected' and not h.venue.posts


@pytest.mark.parametrize('fail',['none','rejected','lost'])
def test_replacing_protection_verifies_new_before_canceling_old(h,fail):
    t,b=setup(h);v=h.venue
    old=v.algo;algos={old['algoClOrdId']:old};get,post=v.get,v.post
    def read(path,**kw):
        if path.endswith('/order-algo'):
            return [deepcopy(algos[kw['algoClOrdId']])]
        return get(path,**kw)
    def write(path,body):
        if path.endswith('/order-algo'):
            assert old['state']=='live'
            if fail=='rejected':
                v.posts.append((path,deepcopy(body)));raise APIError('bad')
            result=post(path,body);algos[body['algoClOrdId']]=v.algo
            if fail=='lost':raise TransportError('lost creation receipt')
            return result
        if path.endswith('/cancel-algos'):
            assert v.algo['state']=='live' and v.algo['algoClOrdId']!=old['algoClOrdId']
            assert body[0]['algoId']==old['algoId']
            v.posts.append((path,deepcopy(body)));old['state']='canceled';return [dict(sCode='0')]
        return post(path,body)
    v.get,v.post=read,write
    q=confirmed(h,b,t,action='protection',stop='95')
    for _ in range(3):consume(h.engine,Management(h.runtime))
    assert b.get(q['id'])['state']==('rejected' if fail=='rejected' else 'done')
    assert old['state']==('live' if fail=='rejected' else 'canceled')
    assert len(v.posts)==(1 if fail=='rejected' else 2)

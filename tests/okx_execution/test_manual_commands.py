"""Offline owner-intent and lost-write tests; never use the real exchange."""
from copy import deepcopy
import pytest
from test_manual_live import h, preview, tick
from yoyo.okx_execution.manual_commands import Commands, consume, reconcile_stop
from yoyo.okx_execution.client import TransportError

@pytest.fixture
def active(h):
    draft=preview(h);h['service'].confirm(draft['id'])
    for _ in range(4):tick(h)
    assert h['store'].trades()[0]['state']=='active'
    h['posts']=deepcopy(h['exchange'].posts)
    return h

def test_preview_confirmation_and_close_never_write_directly(active):
    h=active;e=h['engine'];q=e.commands;t=h['store'].trades()[0]
    d=q.preview(e,t['id'],'close')
    consume(e,q);assert h['store'].trades()[0]['state']=='active'
    q.confirm(d['id'],e.clock());q.confirm(d['id'],e.clock())
    consume(e,q);assert h['store'].trades()[0]['state']=='exit_needed'
    assert h['exchange'].posts==h['posts']
    consume(e,q);assert h['exchange'].posts==h['posts']
    assert q.get(d['id'])['state']=='processing'

@pytest.mark.parametrize('stop',['97','106','0','98.001',None])
def test_stop_never_expands_risk_or_breaks_tick(active,stop):
    h=active
    with pytest.raises(ValueError):h['engine'].commands.preview(h['engine'],h['store'].trades()[0]['id'],'stop',stop)
    assert h['exchange'].posts==h['posts']

def test_lost_amend_is_read_reconciled_once(active):
    h=active;e=h['engine'];q=e.commands;t=h['store'].trades()[0]
    def amend(path,body):
        h['exchange'].posts.append((path,body));h['exchange'].algo['slTriggerPx']=body['newSlTriggerPx']
        raise TransportError('lost response')
    h['exchange'].post=amend
    d=q.preview(e,t['id'],'stop','99');q.confirm(d['id'],e.clock())
    with pytest.raises(TransportError):consume(e,q)
    assert len(h['exchange'].posts)==len(h['posts'])+1
    consume(e,q);reconcile_stop(e,h['store'].trades()[0]);consume(e,q)
    assert q.get(d['id'])['state']=='done' and h['store'].trades()[0]['stop']=='99'
    assert len(h['exchange'].posts)==len(h['posts'])+1

def test_expiry_and_ownership_are_rechecked(active):
    h=active;e=h['engine'];q=e.commands;t=h['store'].trades()[0]
    d=q.preview(e,t['id'],'close');h['now'][0]+=60000
    with pytest.raises(ValueError):q.confirm(d['id'],e.clock())
    d=q.preview(e,t['id'],'close');q.confirm(d['id'],e.clock())
    t['size']='1';h['store'].save(t);consume(e,q)
    assert q.get(d['id'])['state']=='rejected' and h['exchange'].posts==h['posts']

def test_other_pending_command_and_crash_boundary_do_not_redispatch(active):
    h=active;e=h['engine'];q=e.commands;t=h['store'].trades()[0]
    one=q.preview(e,t['id'],'close');q.confirm(one['id'],e.clock())
    two=q.preview(e,t['id'],'close')
    with pytest.raises(ValueError):q.confirm(two['id'],e.clock())
    q.save(one,'processing');consume(e,q)
    assert q.get(one['id'])['state']=='unknown' and h['exchange'].posts==h['posts']

"""Same paper ledger, explicit quick policy and real public contract constraints."""
from types import SimpleNamespace
from decimal import Decimal as D
from fastapi import FastAPI,APIRouter
from fastapi.testclient import TestClient
import pytest
from yoyo.research_workspace.paper_desk_api import install
from yoyo.research_workspace.paper_desk import clock_ms

@pytest.fixture
def env(tmp_path):
    app=FastAPI();router=APIRouter();install(router,app,SimpleNamespace(runtime=tmp_path));app.include_router(router)
    desk=app.state.paper_desk;account=desk.create_account('Synthetic drawer','1000','account-quick-test')['id']
    def mark(s,px='100'):
        now=clock_ms()
        return dict(symbol=s,bid=px,ask=px,source_ms=now,received_ms=now,mark_price=px,mark_source_ms=now,risk=dict(contract_size='.01',fetched_ms=now,
          tiers=[dict(maxSz='1000000',mmr='.004',maxLever='75')],instrument=dict(instId=s,ctVal='.01',ctValCcy=s.split('-')[0],lever='100',tickSz='.1',lotSz='1',minSz='1',maxMktSz='100000',maxLmtSz='100000')))
    app.state.paper_desk_quotes=SimpleNamespace(get=lambda symbols:{s:mark(s) for s in symbols})
    return SimpleNamespace(c=TestClient(app),desk=desk,account=account,mark=mark,app=app)


def preview(e,**kw):
    t=dict(account_id=e.account,symbol='BTC-USDT-SWAP',side='long',order_type='market',stop_loss='90');t.update(kw)
    r=e.c.post('/paper-desk/quick/preview',json=t);assert r.status_code==200,r.text
    return r.json()


def open_order(e,q,key='quick-open-request'):
    return e.c.post(f'/paper-desk/accounts/{e.account}/open',json=dict(quantity=q['quantity'],quote_id=q['id'],request_id=key,side='long',leverage=int(q['leverage']),stop_loss=q['stop_loss']))


def test_auto_total_equity_actual_max_and_stop_independent(env):
    a=preview(env);b=preview(env,stop_loss='50');c=preview(env,leverage='60')
    assert D(a['notional'])==10000 and a['notional']==b['notional']==c['notional']
    assert D(a['leverage'])==75 and D(a['margin'])==D(10000)/75
    r=open_order(env,a);assert r.status_code==201,r.text
    assert D(r.json()['position']['quantity'])==100
    assert len(env.desk.export(env.account)['fills'])==1
    # Replaying is durable even though the current allowance is now consumed.
    assert open_order(env,a).json()==r.json()


def test_alt_confirm_checks_pool_atomically_and_major_remains_independent(env):
    a=preview(env,symbol='SOL-USDT-SWAP',notional_usdt='4000')
    b=preview(env,symbol='ADA-USDT-SWAP',notional_usdt='4000')
    assert open_order(env,a).status_code==201
    second=open_order(env,b,'second-quick-open')
    assert second.status_code==409 and '额度' in second.text
    remaining=env.c.post('/paper-desk/quick/defaults',json=dict(account_id=env.account,symbol='DOGE-USDT-SWAP')).json()
    assert D(remaining['exposure_remaining'])==990 # 998 equity after entry fee *5 - 4000.
    major=env.c.post('/paper-desk/quick/defaults',json=dict(account_id=env.account,symbol='BTC-USDT-SWAP')).json()
    assert D(major['notional_usdt'])==9980


def test_resting_limit_keeps_quick_policy_and_actual_max_on_fill(env,monkeypatch):
    q=preview(env,symbol='SOL-USDT-SWAP',order_type='limit',limit_price='95',allocation_percent='50')
    payload=dict(quantity=q['quantity'],quote_id=q['id'],request_id='quick-limit-request',side='long',leverage=int(q['leverage']),limit_price='95',stop_loss=q['stop_loss'])
    r=env.c.post(f'/paper-desk/accounts/{env.account}/limit-open',json=payload)
    assert r.status_code==201,r.text
    assert r.json()['order']['status']=='pending'
    from yoyo.research_workspace import paper_desk, paper_desk_limits, paper_desk_risk
    at=max(clock_ms(),r.json()['order']['created_ms'])+10
    for module in (paper_desk,paper_desk_limits,paper_desk_risk):monkeypatch.setattr(module,'clock_ms',lambda:at)
    mark=env.mark('SOL-USDT-SWAP','94');mark.update(source_ms=at,received_ms=at,mark_source_ms=at)
    env.app.state.paper_desk_limits.match({'SOL-USDT-SWAP':mark})
    receipt=env.c.get('/paper-desk/quick/receipt/quick-limit-request').json()
    assert receipt['order']['status']=='filled',receipt
    assert env.desk.export(env.account)['positions'][0]['leverage']==75


def test_manual_amount_cannot_bypass_cap_and_no_stop_does_not_invent_1r(env):
    r=env.c.post('/paper-desk/quick/preview',json=dict(account_id=env.account,symbol='SOL-USDT-SWAP',notional_usdt='5001'))
    assert r.status_code==409
    q=preview(env,stop_loss=None)
    assert q['stop_loss'] is None and D(q['notional'])==10000

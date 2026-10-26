"""1.618 constraints run against closed synthetic ranges, no account writes."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from test_free_manual import h, TERMS, SYMBOL
from yoyo.monitor.monday_fib import week_window
from yoyo.contracts.monday_order import bind, check_quote
from yoyo.okx_execution.free_manual import make_plan


def context(now):
    w=week_window(now)
    return dict(state='ready',symbol=SYMBOL,week=w['week'],end_ms=w['end'],expires_ms=w['expires'],high=110,low=90)


@pytest.mark.parametrize('day',[23,24,25])
@pytest.mark.parametrize('side,price,target,extreme',[('long','77.6','102.4','110'),('short','122.4','97.6','90')])
def test_weekend_mirrored_tick_aligned_limit_and_targets(h,day,side,price,target,extreme):
    h.now[0]=int(datetime(2026,10,day,12,tzinfo=ZoneInfo('America/New_York')).timestamp()*1000)
    terms=dict(TERMS,side=side,entry_policy='monday_1618',monday_range=context(h.now[0]),order_type='limit',limit_price=price,stop_loss=None,take_profit=None)
    defaults=make_plan(h.engine,terms,defaults_only=True)
    assert defaults['limit_price']==price and defaults['target_0618']==target and defaults['target_extreme']==extreme
    plan=make_plan(h.engine,terms)
    assert plan['order_body']['ordType']=='post_only' and plan['order_body']['px']==price
    assert not h.venue.posts


@pytest.mark.parametrize('change',[{'order_type':'market'},{'limit_price':'80'},{'side':'short'},{'range_week':'2026-10-12'}])
def test_edited_home_entry_rejected(h,change):
    terms=dict(TERMS,entry_policy='monday_1618',monday_range=context(h.now[0]),order_type='limit',limit_price='77.6',stop_loss=None,take_profit=None)
    with pytest.raises(ValueError):make_plan(h.engine,dict(terms,**change))
    assert not h.venue.posts


def test_display_previous_and_expired_ranges_are_not_executable(h):
    r=context(h.now[0]);terms=dict(TERMS,entry_policy='monday_1618',monday_range=r,order_type='limit',limit_price='77.6')
    for bad in [dict(r,display_previous=True),dict(r,state='developing'),dict(r,expires_ms=h.now[0])]:
        with pytest.raises(ValueError):bind(dict(terms,monday_range=bad),'.1',h.now[0])


def test_paper_bound_quote_cannot_be_reused_as_market_other_side_or_next_week():
    q={'risk':{'monday_bound':dict(side='long',limit_price='77.6',expires_ms=200)}}
    check_quote(q,'long','77.6',100)
    for side,price,at in [('long',None,100),('short','77.6',100),('long','80',100),('long','77.6',200)]:
        with pytest.raises(ValueError):check_quote(q,side,price,at)


def test_worker_preserves_home_post_only_policy_after_confirmation(h):
    from yoyo.okx_execution.free_manual import consume
    h.venue.order_state='live'
    terms=dict(TERMS,entry_policy='monday_1618',monday_range=context(h.now[0]),order_type='limit',limit_price='77.6',stop_loss=None,take_profit='110')
    plan=h.book.preview(make_plan(h.engine,terms))
    h.book.confirm(plan['id'],h.now[0],'owner')
    consume(h.engine,h.book,entries=True)
    order=next(body for path,body in h.venue.posts if path.endswith('/order'))
    assert order['ordType']=='post_only' and order['px']=='77.6'


def test_live_home_api_uses_server_range_and_accepts_only_known_policy(h):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yoyo.okx_execution.manual_api import Service, install
    app=FastAPI();app.state.monday_chart_levels=SimpleNamespace(read_ticket=lambda symbol,at:context(at))
    service=Service(SimpleNamespace(),h.runtime,engine=h.engine,clock=h.engine.clock,healthy=lambda:None)
    install(app,SimpleNamespace(),service=service)
    headers={'Origin':'http://testserver','X-Fable-Manual':'owner-confirmation','Sec-Fetch-Site':'same-origin'}
    terms=dict(TERMS,entry_policy='monday_1618',order_type='limit',limit_price=None,stop_loss=None,take_profit=None)
    with TestClient(app) as c:
        response=c.post('/api/manual-live/free/defaults',json=terms,headers=headers)
        assert response.status_code==200,response.text
        terms.update(limit_price=response.json()['limit_price'],range_week=response.json()['range_week'])
        response=c.post('/api/manual-live/free/preview',json=terms,headers=headers)
        assert response.status_code==200,response.text
        assert response.json()['limit_price']=='77.6'
        assert c.post('/api/manual-live/free/preview',json=dict(terms,monday_range=context(h.now[0])),headers=headers).status_code==422
        assert c.post('/api/manual-live/free/preview',json=dict(terms,limit_price='80'),headers=headers).status_code==409
    assert not h.venue.posts


def test_monday_manual_fallback_matches_display_and_expires_at_new_range():
    from yoyo.monitor.monday_chart_levels import MondayChartLevels
    at=int(datetime(2026,10,26,12,tzinfo=ZoneInfo('America/New_York')).timestamp()*1000)
    previous=context(at-86400000)
    previous.update(display_previous=True,current_week='2026-10-26')
    reader=MondayChartLevels();reader.read_display=lambda symbol,now:previous
    range_value=reader.read_ticket(SYMBOL,at)
    terms=dict(TERMS,entry_policy='monday_1618',monday_range=range_value,order_type='limit',limit_price='77.6',range_week='2026-10-19')
    _,values=bind(terms,'.1',at)
    assert values['range_week']=='2026-10-19'
    assert range_value['ticket_until_ms']==week_window(at)['end']
    with pytest.raises(ValueError):bind(terms,'.1',range_value['ticket_until_ms'])


@pytest.mark.parametrize('path',['/api/v5/account/set-leverage','/api/v5/trade/order'])
def test_timeout_acknowledgements_do_not_unlock_a_duplicate_home_order(h,path):
    from yoyo.okx_execution.free_manual import consume
    from yoyo.okx_execution.client import APIError
    h.venue.order_state='live'
    terms=dict(TERMS,entry_policy='monday_1618',monday_range=context(h.now[0]),order_type='limit',limit_price='77.6',stop_loss=None,take_profit=None)
    q=h.book.preview(make_plan(h.engine,terms));h.book.confirm(q['id'],h.now[0],'owner')
    post=h.venue.post
    def uncertain(endpoint,body):
        result=post(endpoint,body)
        if endpoint==path:raise APIError('50004')
        return result
    h.venue.post=uncertain
    for _ in range(3):consume(h.engine,h.book,entries=True)
    assert h.book.get(q['id'])['state']=='live'
    assert sum(endpoint==path for endpoint,body in h.venue.posts)==1


def test_empty_usdt_manual_account_can_stay_idle_but_cannot_preview(h):
    from yoyo.okx_execution.engine import Engine
    get=h.venue.get
    h.venue.get=lambda path,**kw:[{'totalEq':'20','details':[{'ccy':'BTC','eq':'1'}]}] if path.endswith('/balance') else get(path,**kw)
    account=h.engine.account()
    assert account['available']=='0' and account['equity']=='0' and not account['positions']
    with pytest.raises(ValueError,match='可用 USDT'):make_plan(h.engine,TERMS,defaults_only=True)
    with pytest.raises(ValueError):Engine.account(h.engine)
    h.venue.positions=[{'instId':SYMBOL,'pos':'1'}]
    with pytest.raises(ValueError):h.engine.account()
    assert not h.venue.posts


def test_missing_balance_details_remain_unavailable(h):
    get=h.venue.get
    h.venue.get=lambda path,**kw:[{'totalEq':'0'}] if path.endswith('/balance') else get(path,**kw)
    with pytest.raises(ValueError):h.engine.account()

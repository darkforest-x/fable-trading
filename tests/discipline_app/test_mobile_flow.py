"""Collateral caps and mobile acknowledgements against synthetic ledgers only."""
import threading
import time
import uuid
from dataclasses import replace
from decimal import Decimal as D
import pytest
from fastapi.testclient import TestClient
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.policy import Policy, Rejected, plan
from yoyo.discipline_app.order_requests import OrderRequests
from test_live_book import live, ticket as live_ticket
from test_discipline_service import Clock, market, ticket


def margin_book(tmp_path):
    book=Book(tmp_path/'margin.sqlite3',Clock(),initial_equity='1000')
    book.configure(Policy())
    market(book)
    return book


def test_margin_slider_is_equity_fraction_and_still_risk_capped(tmp_path):
    b=margin_book(tmp_path)
    low=b.preview(ticket(fraction='.10',leverage=10,sizing_mode='margin'))['plan']
    assert D(low['margin'])<=100 and D(low['risk'])<=50
    assert D(low['risk'])==D(low['price_risk'])+D(low['costs'])
    with pytest.raises(Rejected,match='比例超过'):
        b.preview(ticket(fraction='.101',leverage=100,sizing_mode='margin'))
    alt=b.preview(ticket(symbol='SOL',fraction='.05',leverage=20,sizing_mode='margin'))['plan']
    assert D(alt['margin'])<=50 and D(alt['notional'])<=1000
    with pytest.raises(Rejected,match='比例超过'):
        b.preview(ticket(symbol='SOL',fraction='.051',leverage=20,sizing_mode='margin'))


def test_existing_category_margin_reduces_new_order_and_legacy_requests(tmp_path):
    b=margin_book(tmp_path)
    with b.transaction() as (_,s):
        s['policy']['major_allowance_scope']='shared'
        s['positions'].append(dict(id='fixture',symbol='BTC-USDT-SWAP',side='long',quantity='9',remaining='9',entry='100',leverage=10,margin='90',direction=1))
    result=b.preview(ticket(symbol='ETH',fraction='.1',leverage=10,sizing_mode='margin'))['plan']
    assert D(result['margin'])<=10
    legacy=b.preview(ticket(symbol='ETH',fraction='1',leverage=10))['plan']
    assert D(legacy['margin'])<=10


def test_saved_policy_does_not_silently_activate_new_limits():
    old=Policy().document();old.pop('major_margin_fraction');old.pop('alt_margin_fraction')
    assert Policy.parse(old).margin_cap('BTC-USDT-SWAP') is None


def test_tightening_keeps_active_exit_plans_and_risk_history(tmp_path):
    b=margin_book(tmp_path)
    with b.transaction() as (_,s):
        s['policy']['major_margin_fraction']=None;s['policy']['alt_margin_fraction']=None
        s['positions']=[dict(id='existing',policy={'risk_fraction':'.05'},risk='12',stop='90')]
        s['loss_streak']=2;s['cooldown_until']=9999999999
        s['daily']={'key':'fixture','entries':3}
    with b.read_transaction() as (_,s):before=s
    b.tighten_margin_caps('.10','.05')
    with b.read_transaction() as (_,s):after=s
    for field in ('positions','daily','loss_streak','cooldown_until','pending','closed'):
        assert after[field]==before[field]
    assert after['policy_revision']==before['policy_revision']+1
    with pytest.raises(Rejected,match='只能收紧'):
        b.tighten_margin_caps('.11','.05')


def test_absent_request_fence_blocks_delayed_send_but_never_cancels_accepted(live):
    book,exchange,_=live
    requests=OrderRequests()
    body=dict(request_id=uuid.uuid4().hex,ticket=live_ticket(),policy_revision=1,preview_id=None,confirmed_live=True)
    try:
        result=requests.abandon_unreceived(book,body['request_id'])
        assert result['status']=='cancelled'
        with pytest.raises(Rejected):requests.submit(book,body)
        assert not exchange.writes
        body['request_id']=uuid.uuid4().hex
        requests.submit(book,body)
    finally:requests.shutdown()
    before=len(exchange.writes)
    assert requests.abandon_unreceived(book,body['request_id'])['status']=='filled'
    assert len(exchange.writes)==before


def test_api_acknowledges_before_slow_execution_and_polling_never_resends(live):
    book,exchange,_=live
    entered,release=threading.Event(),threading.Event()
    original=book.enter;calls=[]
    def slow(*args):
        calls.append(args[0]);entered.set();assert release.wait(3);return original(*args)
    book.enter=slow
    preview=book.preview(live_ticket())
    body=dict(request_id=uuid.uuid4().hex,ticket=live_ticket(),policy_revision=1,preview_id=preview['preview_id'],confirmed_live=True)
    headers={'Authorization':'Bearer '+'x'*36}
    # Match the validated API's explicit default field in the saved preview.
    body['ticket']['sizing_mode']='risk'
    preview=book.preview(body['ticket']);body['preview_id']=preview['preview_id']
    with TestClient(create_app(book,'x'*36,market_enabled=False)) as client:
        try:
            start=time.monotonic();response=client.post('/v1/order-requests',json=body,headers=headers)
            assert response.status_code==202 and time.monotonic()-start<1
            assert entered.wait(1)
            for _ in range(3):
                assert client.get('/v1/order-requests/'+body['request_id'],headers=headers).json()['status']=='processing'
            assert client.post('/v1/order-requests',json=body,headers=headers).status_code==202
            changed={**body,'ticket':{**body['ticket'],'stop':'94'}}
            assert client.post('/v1/order-requests',json=changed,headers=headers).status_code==409
            assert len(calls)==1 and not exchange.writes
            assert client.get('/v1/order-requests/'+body['request_id']).status_code==401
        finally:
            release.set()
    assert len(calls)==1
    assert len([x for x in exchange.writes if x[0]=='/api/v5/trade/order'])==1


def test_no_preview_single_tap_is_validated_and_crash_never_replays(live):
    book,exchange,_=live
    requests=OrderRequests()
    body=dict(request_id=uuid.uuid4().hex,ticket=live_ticket(),policy_revision=1,preview_id=None,confirmed_live=True)
    try:
        requests.submit(book,body)
    finally:
        requests.shutdown()
    assert requests.status(book,body['request_id'])['status']=='filled'
    before=len(exchange.writes)
    new=OrderRequests()
    try:
        assert new.status(book,body['request_id'])['status']=='filled'
        assert len(exchange.writes)==before
        key=uuid.uuid4().hex
        with book.connect() as db:
            db.execute('INSERT INTO mobile_requests VALUES(?,?,?,?,?,?)',(key,'fake','prior-process','processing',None,book.clock()))
        assert new.status(book,key)['status']=='interrupted'
        assert len(exchange.writes)==before
    finally:
        new.shutdown()


def test_price_move_shrinks_size_instead_of_increasing_reviewed_plan(live):
    from test_live_book import quote
    book,exchange,_=live
    request=live_ticket()
    preview=book.preview(request)
    quote(book,'100.01')
    result=book.enter(uuid.uuid4().hex,request,preview['policy_revision'],preview['preview_id'],True)
    assert result['status']=='filled'
    for field in ['quantity','notional','risk','margin']:
        assert D(result['plan'][field])<=D(preview['plan'][field])
    assert len([x for x in exchange.writes if x[0]=='/api/v5/trade/order'])==1

"""Offline exchange protocol and protection arithmetic; no account writes."""
import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal as D
import httpx
import pytest
from yoyo.discipline_app.okx_demo import Credentials
from yoyo.discipline_app.okx_execution import (OkxExecutionClient, ExchangeUncertain,
    ExchangeRejected, entry_payload, protective_prices, stable_id)
from yoyo.discipline_app.policy import Policy, Rejected


def client(handler, **kwargs):
    result = OkxExecutionClient(Credentials('test-key','test-secret','test-pass'),
        environment='okx_live', transport=httpx.MockTransport(handler), **kwargs)
    result.synchronized_at = time.time()
    return result


def test_writes_default_disabled_and_sensitive_nontrade_routes_forbidden():
    calls = []
    c = client(lambda request: calls.append(request))
    try:
        with pytest.raises(Rejected, match='尚未启用'):
            c.post('/api/v5/trade/order', {})
        for path in ('/api/v5/asset/withdrawal', '/api/v5/users/subaccount/modify-apikey'):
            with pytest.raises(Rejected, match='权限范围'):
                c.post(path, {})
        assert calls == []
    finally:
        c.close()


def test_signed_body_and_live_environment_are_explicit():
    requests = []
    def exchange(request):
        requests.append(request)
        assert request.headers['x-simulated-trading'] == '0'
        message = request.headers['OK-ACCESS-TIMESTAMP'] + 'POST' + request.url.raw_path.decode() + request.content.decode()
        expected = base64.b64encode(hmac.new(b'test-secret',message.encode(),hashlib.sha256).digest()).decode()
        assert request.headers['OK-ACCESS-SIGN'] == expected
        assert 3000 < int(request.headers['expTime']) - time.time()*1000 <= 5000
        return httpx.Response(200,json={'code':'0','data':[{'sCode':'0','ordId':'fixture'}]})
    c = client(exchange,write_enabled=True)
    try:
        assert c.post('/api/v5/trade/order',{'clOrdId':'test','sz':'1'})[0]['ordId']=='fixture'
        assert len(requests)==1
    finally:
        c.close()


def test_ambiguous_write_is_not_retried_or_reported_rejected():
    count = [0]
    def exchange(request):
        count[0] += 1
        raise httpx.ReadTimeout('private provider message', request=request)
    c = client(exchange,write_enabled=True)
    try:
        with pytest.raises(ExchangeUncertain) as error:
            c.post('/api/v5/trade/order',{})
        assert 'private' not in str(error.value)
        assert count[0]==1
    finally:
        c.close()
    with pytest.raises(ExchangeUncertain):
        OkxExecutionClient._response(httpx.Response(503,text='unavailable'),write=True)
    with pytest.raises(ExchangeRejected) as error:
        OkxExecutionClient._response(httpx.Response(200,json={'code':'1','data':[{'sCode':'51008','sMsg':'private'}]}),write=True)
    assert error.value.code=='51008'


@pytest.mark.parametrize('side,stop', [('long','95'),('short','105')])
def test_split_protection_preserves_contract_units_and_net_R(side,stop):
    policy=Policy()
    instrument=dict(contract_value='.01', step='.01', minimum='.01', tick='.01')
    ticket=dict(symbol='BTC-USDT-SWAP',side=side)
    sized=dict(quantity='1.03',entry='100',stop=stop,risk='5.356')
    payload=entry_payload('fixture-identity',ticket,sized,instrument,policy,'long_short_mode')
    assert payload['ordType']=='fok' and payload['posSide']==side
    assert D(payload['sz'])==103
    sl,tp1,tp2=payload['attachAlgoOrds']
    assert sl['slTriggerPx']==stop and 'tpTriggerPx' not in sl
    assert D(tp1['sz'])==61 and D(tp2['sz'])==42
    assert sum(D(tp['sz']) for tp in (tp1,tp2))==D(payload['sz'])
    assert len({payload['clOrdId'],*[x['attachAlgoClOrdId'] for x in (sl,tp1,tp2)]})==4
    prices=protective_prices('100','1.03','5.356','.0515',policy,side,'.01')
    direction=1 if side=='long' else -1
    for target,r in zip(prices['targets'],(3,5)):
        gross=(D(target)-100)*D('1.03')*direction
        net=gross-D('.0515')-D(target)*D('1.03')*D(policy.fee_rate)
        assert net>=D(r)*D('5.356')
    close_price=D(prices['breakeven'])*(1-direction*D(policy.slippage_rate))
    net=(close_price-100)*D('1.03')*direction-D('.0515')-close_price*D('1.03')*D(policy.fee_rate)
    assert net>=0


def test_too_small_split_rejects_before_sending_and_ids_stable():
    with pytest.raises(Rejected,match='最小合约数量'):
        entry_payload('a',dict(side='long',symbol='BTC-USDT-SWAP'),
            dict(quantity='.01',entry='100',stop='95',risk='.1'),
            dict(contract_value='.01',step='.01',minimum='.01',tick='.01'),Policy(),'net_mode')
    assert stable_id('one','entry')==stable_id('one','entry')
    assert stable_id('one','entry')!=stable_id('one','close')
    assert len(stable_id('one','entry'))==32

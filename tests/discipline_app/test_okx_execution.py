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
from yoyo.discipline_app.policy import (Policy, Rejected, take_profit_targets,
    resolve_take_profit_targets)


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


def test_cross_tier_uses_contracts_and_keeps_fractional_maximum():
    from yoyo.discipline_app.leverage_tiers import fit_order

    def exchange(request):
        assert request.method == 'GET' and request.url.path == '/api/v5/public/position-tiers'
        assert dict(request.url.params) == {'instType': 'SWAP', 'tdMode': 'cross', 'instFamily': 'SOL-USDT'}
        assert 'OK-ACCESS-KEY' not in request.headers
        return httpx.Response(200, json={'code':'0','data':[
            dict(instFamily='SOL-USDT', minSz='0', maxSz='5000', maxLever='100'),
            dict(instFamily='SOL-USDT', minSz='5000.01', maxSz='10000', maxLever='66.66')]})

    c = client(exchange)
    spec = dict(symbol='SOL-USDT-SWAP', contract_value='.1', step='.001', max_leverage=100)
    def fit(size, cash='100000', **changes):
        terms = dict(side='long', held={}, quantity=D(size), entry=D(100),
                     unit_cost=D(0), available=D(cash))
        return fit_order(c, spec, **{**terms, **changes})

    try:
        assert fit('500') == (D('500'), D(100))
        assert fit('500.001') == (D('500.001'), D('66.66'))
        # Second-tier collateral only covers 400 base units, while the first
        # tier permits 500 at 100x. Spend all 500 instead of stopping at 400.
        assert fit('600', cash='600') == (D('500'), D(100))
        assert fit('100', fixed_leverage=100, held={'long':'499.9'}) == (D('.1'), D(100))
        with pytest.raises(Rejected, match='档位'):
            fit('1', fixed_leverage=100, held={'short':'501'})
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


@pytest.mark.parametrize('side,stop,custom', [('long','95','120'),('short','105','80')])
def test_entry_payload_uses_per_tier_custom_target_and_original_split(side,stop,custom):
    policy=Policy()
    instrument=dict(contract_value='.01',step='.01',minimum='.01',tick='.01')
    ticket=dict(symbol='BTC-USDT-SWAP',side=side,take_profits=[None,custom])
    sized=dict(quantity='1.03',entry='100',stop=stop,risk='5.356')
    payload=entry_payload('custom-target',ticket,sized,instrument,policy,'long_short_mode')
    first,second=payload['attachAlgoOrds'][1:]
    assert first['tpTriggerPx'] == protective_prices('100','1.03','5.356','.0515',policy,side,'.01')['targets'][0]
    assert D(second['tpTriggerPx']) == D(custom)
    assert D(first['sz']) == 61 and D(second['sz']) == 42


@pytest.mark.parametrize('side,overrides', [
    ('long',['130','120']), ('short',['70','80']),
    ('long',[None,'100']), ('short',[None,'100']),
    ('long',[None,'120.005']), ('short',[None,'79.995']),
])
def test_custom_targets_require_favorable_tick_aligned_prices_in_tier_order(side,overrides):
    with pytest.raises(Rejected):
        take_profit_targets('100','1.03','5.356','.0515',Policy(),side,'.01',overrides)


def test_custom_target_cannot_collide_with_another_tier_but_auto_rounding_stays_compatible():
    policy=Policy()
    automatic=protective_prices('100','1.03','5.356','.0515',policy,'long','.01')['targets']
    with pytest.raises(Rejected,match='档序'):
        take_profit_targets('100','1.03','5.356','.0515',policy,'long','.01',
                            [automatic[1],None])
    rounded=Policy(take_profits=(('0.00001','.6'),('0.00002','.4')))
    targets=take_profit_targets('100','10','.0001','0',rounded,'long','.01')
    assert targets[0] == targets[1]


def test_fill_time_manual_collision_freezes_valid_reviewed_auto_tier():
    policy=Policy()
    reviewed_auto=take_profit_targets('100','1.03','5.356','.0515',policy,'long','.01')
    custom_second=str(D(reviewed_auto[0])+D('1'))
    reviewed=[reviewed_auto[0],custom_second]
    resolved,frozen=resolve_take_profit_targets('100','1.03','9','.0515',policy,'long','.01',
        [None,custom_second],reviewed)
    assert resolved==reviewed
    assert frozen==[True,False]
    with pytest.raises(Rejected):
        resolve_take_profit_targets('117','1.03','9','.0515',policy,'long','.01',
            [None,custom_second],reviewed)


def test_entry_compiler_tracks_the_active_session_policy_tier_count():
    policy=Policy(take_profits=(('1','.2'),('2','.3'),('4','.5')))
    ticket=dict(symbol='BTC-USDT-SWAP',side='long',take_profits=[None,None,None])
    sized=dict(quantity='1.05',entry='100',stop='95',risk='5.46')
    instrument=dict(contract_value='.01',step='.01',minimum='.01',tick='.01')
    payload=entry_payload('three-tiers',ticket,sized,instrument,policy,'long_short_mode')
    algos=payload['attachAlgoOrds']
    assert len(algos)==4
    assert [D(row['sz']) for row in algos[1:]]==[21,31,53]

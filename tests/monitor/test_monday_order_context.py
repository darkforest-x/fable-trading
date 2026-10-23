"""Wick stops are closed, native-timeframe and current NY-week observations."""
from yoyo.monitor.monday_order_context import wick_stop
from yoyo.monitor.monday_fib import week_window
from yoyo.data.candle_hub import STEPS
import pytest

NOW=1792742400000
W=week_window(NOW)

def candle(t,**kw):return dict(time=t,open=11,close=12,high=14,low=8,**kw)

@pytest.mark.parametrize('period',['1m','5m','15m','30m','1H','4H','1D'])
def test_latest_closed_wick_by_current_timeframe(period):
    step=STEPS['1Dutc' if period=='1D' else period]
    now=(NOW//step)*step+step//2
    t=(NOW//step)*step-step
    rows=[candle(t-step),candle(t),candle(t+step)]
    got=wick_stop(rows,period=period,side='long',level=9,window=W,now=now)
    assert got['candle_ms']==t and got['stop_loss']=='8' and got['timeframe']==period
    short=wick_stop(rows,period=period,side='short',level=13,window=W,now=now)
    assert short['stop_loss']=='14' and short['candle_ms']==t


def test_body_cross_old_week_future_and_wrong_side_never_supply_stop():
    args=dict(period='15m',side='long',level=9,window=W,now=NOW)
    assert wick_stop([dict(time=NOW-900000,open=8,close=12,high=13,low=7)],**args) is None
    assert wick_stop([candle(W['end']-900000),candle(NOW)],**args) is None
    assert wick_stop([candle(NOW-900000)],**dict(args,level=15)) is None
    assert wick_stop([candle(NOW-900000)],**dict(args,now=W['expires'])) is None
    assert wick_stop([],**args) is None


def test_touch_equal_wick_tip_and_body_boundary_is_eligible_but_flat_wick_is_not():
    row=candle(NOW-900000);args=dict(period='15m',side='long',window=W,now=NOW)
    assert wick_stop([row],level=8,**args)['stop_loss']=='8'
    assert wick_stop([row],level=11,**args)['stop_loss']=='8'
    row['low']=11
    assert wick_stop([row],level=11,**args) is None


def test_small_coin_stop_stays_plain_decimal_for_order_validation():
    row=dict(time=NOW-900000,open=0.00000002,close=0.00000003,high=0.00000004,low=0.00000001)
    got=wick_stop([row],period='15m',side='long',level=.000000015,window=W,now=NOW)
    assert got['stop_loss']=='0.00000001'


def test_endpoint_backfills_past_chart_history_and_caches_current_boundary():
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yoyo.monitor.monday_order_context import install
    from yoyo.monitor.monday_candles import MondayCandles
    now=(NOW//60000)*60000;old=now-721*60000;calls=[]
    class Source:
        def clock(self):return now
        def get(self,path,q):
            calls.append(q)
            return [[str(old),'11','14','8','12','1','1','1','1']]
    app=FastAPI();app.state.monday_fib=SimpleNamespace(snapshot=lambda **kw:dict(week=W['week'],items=[dict(symbol='BTC-USDT-SWAP',state='ready',lower=9,upper=13)]))
    app.state.monday_candles=SimpleNamespace(client=Source(),_candles=MondayCandles._candles,snapshot=lambda *a,**kw:dict(last_close_ms=now,candles=[dict(time=now-i*60000,open=11,close=12,high=14,low=10) for i in range(1,721)]))
    install(app);client=TestClient(app)
    path='/api/monday-fib/order-context?symbol=BTC-USDT-SWAP&period=1m&side=long'
    result=client.get(path).json();assert result['stop_loss']=='8.0' and result['candle_ms']==old
    assert len(calls)==1 and calls[0]['bar']=='1m'
    assert client.get(path).json()==result and len(calls)==1

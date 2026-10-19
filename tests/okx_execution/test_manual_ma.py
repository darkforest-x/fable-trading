"""Six-MA stops share chart values and never use an open or future candle."""
import pytest
from yoyo.okx_execution.manual_ma import envelope_stop, MovingAverages, MA_KEYS
from yoyo.okx_execution.risk import D


def snapshot():
    return dict(cutoff_ms=360000000, observed_ms=360001000,
                ma=dict(sma20='100',sma60='99',sma120='98.015',ema20='100',ema60='101',ema120='101.985'))

@pytest.mark.parametrize('side,price,key', [('long','98','sma120'),('short','102','ema120')])
def test_stop_is_at_least_one_tick_outside_all_six_and_rounded_outward(side,price,key):
    d=envelope_stop(snapshot(),side,'.01')
    assert d['price']==price and d['boundary_key']==key
    assert d['close_ms']==360000000 and d['open_ms']==359700000
    values=[D(v) for v in d['ma'].values()]
    assert min(values)-D(price)>=D('.01') if side=='long' else D(price)-max(values)>=D('.01')


def test_missing_ma_cannot_silently_use_five_or_a_candle_low():
    d=snapshot();del d['ma']['ema120']
    with pytest.raises(KeyError):envelope_stop(d,'long','.01')


def test_chart_and_stop_share_one_causal_warmup_snapshot(tmp_path):
    cutoff=360000000
    class Market:
        def __init__(self):self.calls=0
        def candles(self,symbol,tf,close,visible):
            self.calls+=1
            assert tf=='5m' and close==cutoff and visible==cutoff-100*300000
            return [dict(t=cutoff-(1100-i)*300000,o=100+i/100,h=101+i/100,l=99+i/100,c=100+i/100,v=1) for i in range(1100)]
    market=Market();data=MovingAverages(tmp_path,market=market)
    try:
        chart=data.get('A-USDT-SWAP',lambda:cutoff+10)
        quote=data.get('A-USDT-SWAP',lambda:cutoff+20)
        assert market.calls==1 and len(chart['candles'])==100
        assert all(D(chart['ma'][k])==D(str(chart['candles'][-1][k])) for k in MA_KEYS)
        assert chart['candles'][-1]['time']*1000==cutoff-300000
        assert quote['observed_ms']==cutoff+20
        stop=envelope_stop(quote,'long','.001')
        assert D(stop['price'])<min(D(v) for v in chart['ma'].values())
    finally:data.close()

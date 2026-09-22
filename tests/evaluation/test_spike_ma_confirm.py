"""Execution-phase contracts for HTF confirmation exits and fixed 2R fills."""
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation import spike_ma_confirm as k
from yoyo.evaluation import spike_v10_4_study as source
from yoyo.evaluation import spike_v1_v8_be05 as old


def make(changes=None,side=1,reverse=None,gap=None):
    index=pd.date_range('2025-01-01',periods=12,freq='15min',tz='UTC')
    f=pd.DataFrame({'open':100.,'high':100.5,'low':99.5,'close':100.,'atr':1.},index=index)
    for i,ohlc in (changes or {}).items(): f.iloc[i,:4]=ohlc
    if side==-1:
        o,h,l,c=(f[n].copy() for n in ('open','high','low','close'))
        f['open'],f['high'],f['low'],f['close']=200-o,200-l,200-h,200-c
    raw=np.zeros(len(f),int);raw[4]=side
    if reverse is not None:raw[reverse]=-side
    gaps=np.zeros(len(f),bool)
    if gap is not None:gaps[gap]=True
    p=source.prepared_arm(f,gaps,raw,'synthetic:15m',{'symbol':'SYN','timeframe':'15m'},15,.01)
    p=replace(p,allowed=raw!=0)
    return p,np.full(len(f),98.5 if side==1 else 101.5)


def run(p,h,policy):return k.replay_serial(p,policy=policy,higher=h)[0].iloc[0]


@pytest.mark.parametrize('side',[1,-1])
@pytest.mark.parametrize('changes,reverse,gap',[
    ({5:(100,106,99.5,105),6:(101.5,102,100.5,101)},None,None),
    ({7:(97,99,96,98)},6,None),
    ({},None,7),({},None,None),
])
def test_original_full_key_parity(side,changes,reverse,gap):
    p,h=make(changes,side,reverse,gap)
    a=k.replay_serial(p,policy='original__trail',higher=h)[0]
    b=old.replay_serial(p.context,arm='v8',enable_be=False,prepared=p)[0]
    pd.testing.assert_frame_equal(a[old.KEY+['censored','mfe_r','protection']],b[old.KEY+['censored','mfe_r','protection']],check_dtype=False)


@pytest.mark.parametrize('side',[1,-1])
def test_close_and_body_confirm_next_open_at_real_price(side):
    p,h=make({5:(100,100.5,98,98.3),6:(98.2,98.4,97.9,98.1),7:(97.5,100,97,99)},side)
    close=run(p,h,'htf_close__trail');body=run(p,h,'htf_body__trail')
    assert close.exit_i==6 and body.exit_i==7
    assert close.exit_reason=='ma_close_next_open' and body.exit_reason=='ma_body_next_open'
    assert close.exit_price==p.open[6] and body.exit_price==p.open[7]
    assert body.ma_confirm_time==p.frame.index[7]
    assert body.reference_risk_only
    assert body.exit_price != h[6]


@pytest.mark.parametrize('side',[1,-1])
def test_wick_is_ignored_but_touch_arm_exits(side):
    p,h=make({5:(100,100.5,97,100)},side)
    assert run(p,h,'htf_body__trail').censored
    assert run(p,h,'htf_touch__trail').exit_i==5


@pytest.mark.parametrize('side',[1,-1])
def test_equality_does_not_confirm_and_current_known_line_updates(side):
    p,h=make({5:(98.5,99,98,98.5)},side)
    assert run(p,h,'htf_body__trail').censored
    h[6:]=101 if side==1 else 99
    a=run(p,h,'htf_body__trail');assert a.exit_i==7


def test_large_down_body_can_lose_more_than_reference_r():
    p,h=make({5:(100,100,89,90),6:(90,91,79,80),7:(79,100,78,90)})
    a=run(p,h,'htf_body__tp2')
    assert a.exit_i==7 and a.net_r < -10 and a.reference_risk_only


@pytest.mark.parametrize('side',[1,-1])
def test_tp2_new_r_cost_and_both_touched_stop_first(side):
    p,h=make({5:(100,104.2,99.5,104)},side)
    a=run(p,h,'original__tp2')
    assert a.exit_reason=='tp2' and a.gross_r==pytest.approx(2)
    assert a.gross_return-a.net_return==pytest.approx(.002)
    p,h=make({5:(100,104.2,97.9,100)},side)
    b=run(p,h,'original__tp2')
    assert b.exit_reason=='initial_stop' and b.ambiguous_bar
    # A wider entry anchor moves the target too; it cannot reuse old 2R.
    h[:]=96 if side==1 else 104
    c=run(p,h,'htf_touch__tp2');assert c.initial_risk>2 and c.censored


def test_tp_gap_is_target_fill_and_open_stop_is_not_ambiguous():
    p,h=make({6:(105,106,104,105)})
    a=run(p,h,'original__tp2');assert a.exit_reason=='tp2_gap' and a.exit_price==104
    p,h=make({6:(97,106,96,100)})
    a=run(p,h,'original__tp2');assert a.exit_reason=='initial_stop_gap' and not a.ambiguous_bar


def test_close_confirmation_cannot_exit_same_bar_and_trail_is_next_bar():
    p,h=make({5:(98.3,99,98,98.2),6:(97.8,99,97.5,98)})
    a=run(p,h,'htf_body__trail');assert a.exit_i==6
    p,h=make({5:(100,106,99.5,105),6:(101.5,102,100.5,101)})
    a=run(p,h,'htf_body__trail');assert a.exit_i==6 and a.exit_price==101
    assert a.exit_reason=='trailing_stop'


@pytest.mark.parametrize('policy',k.POLICIES)
def test_fixed_and_serial_share_exits_and_future_prefix(policy):
    p,h=make({5:(100,100.5,98,98.3),6:(98.2,98.4,97.9,98.1),7:(97.5,100,97,99)})
    a=run(p,h,policy)
    raw=source._initial_position_fast(p.frame.index,p.open,p.high,p.low,p.close,p.atr,p.gap,4,1,p.spec)
    b=k.replay_fixed(p,pd.Series(raw),policy=policy,higher=h)
    for name in old.KEY+['censored']:
        if isinstance(a[name],(float,np.floating)):
            np.testing.assert_allclose(a[name],b[name],equal_nan=True)
        else: assert a[name]==b[name]
    if not a.censored:
        end=int(a.exit_i)+1
        pp=replace(p,frame=p.frame.iloc[:end],gap=p.gap[:end],allowed=p.allowed[:end],raw_side=p.raw_side[:end],
            open=p.open[:end],high=p.high[:end],low=p.low[:end],close=p.close[:end],atr=p.atr[:end])
        prefix=run(pp,h[:end],policy)
        assert prefix.exit_i==a.exit_i and prefix.exit_price==a.exit_price


def test_unknown_ma_and_gap_censor_without_future_fill():
    p,h=make();h[6]=np.nan
    a=run(p,h,'htf_body__trail');assert a.censored and a.exit_reason=='ma_unknown_censored'
    p,h=make(gap=6);a=run(p,h,'htf_body__trail')
    assert a.censored and a.exit_reason=='data_gap_censored'
    raw=source._initial_position_fast(p.frame.index,p.open,p.high,p.low,p.close,p.atr,p.gap,4,1,p.spec)
    raw['signal_i']+=100;raw['entry_i']+=100;h[5]=np.nan
    a=k.replay_fixed(p,pd.Series(raw),policy='htf_body__trail',higher=h)
    assert a['censored'] and a['exit_i']==105


def test_tp_touch_precedes_close_only_structural_confirmation():
    p,h=make({5:(100,104.2,97.8,98)})
    a=run(p,h,'htf_close__tp2')
    assert a.exit_i==5 and a.exit_reason=='tp2'


@pytest.mark.parametrize('profit',['trail','tp2'])
def test_fixed_honors_already_widened_entry_and_matches_serial(profit):
    p,h=make({5:(100,106,99.5,105),6:(100,101,95,96)})
    h[:]=96
    policy='htf_touch__'+profit;a=run(p,h,policy)
    raw=source._initial_position_fast(p.frame.index,p.open,p.high,p.low,p.close,p.atr,p.gap,4,1,p.spec)
    row=k.spike_ma_stop.transform_initial(raw,arm='htf_sma60',sma120=None,htf_sma60=96,atr=1,tick=.01)
    b=k.replay_fixed(p,pd.Series(row),policy=policy,higher=h)
    assert a.initial_risk>4 and a.initial_risk==b['initial_risk']
    assert a.exit_i==b['exit_i'] and a.exit_price==b['exit_price']
    assert a.exit_reason=='initial_stop' and b['net_r']==pytest.approx(a.net_r)

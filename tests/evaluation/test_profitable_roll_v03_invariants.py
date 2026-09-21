"""Independent financial/causal counterexamples, specified before market replay."""
import pandas as pd
import pytest

from yoyo.evaluation.profitable_roll_v03 import replay_roll


def source():
    bars=[(100,101,99,100),(100,106,100,105),(105,106,103,104),
        (104,111,104,110),(111,112,110,111),(111,112,109,110),
        (111,118,111,117),(118,119,117,118),(118,119,116,117),
        (117,125,117,124),(125,126,123,125),(115,116,114,115)]
    return pd.DataFrame(bars,index=pd.date_range('2026-01-01',periods=len(bars),freq='1h',tz='UTC'),
                        columns=['open','high','low','close'],dtype=float)


def run(frame=None,**overrides):
    kwargs=dict(entry_i=0,initial_stop=98,tick=.01,quantity_step=1,min_quantity=1,
        initial_quantity_requested=100,capital=10000,leverage=40,max_adds=None,fee=.001,
        tiers=[{'max_quantity':1e12,'mmr':.01,'max_leverage':50}])
    kwargs.update(overrides)
    return replay_roll(source() if frame is None else frame,**kwargs)


def test_three_adds_have_independent_reconciled_fee_funding_profit():
    frame=source();rates={int(frame.index[i].value//1_000_000):.0005 for i in [4,8]}
    result,events=run(frame,funding_rates=rates)
    assert result['status']=='complete'
    legs=[e for e in events if e['kind'] in ['entry','add']]
    assert len(legs)==4 and result['adds_count']==3
    q=sum(e['quantity'] for e in legs);cost=sum(e['quantity']*e['price'] for e in legs)
    paid=sum(e['payment'] for e in events if e['kind']=='funding')
    expected=10000+q*result['exit_price']-cost-.001*cost-.001*q*result['exit_price']-paid
    assert result['final_balance']==pytest.approx(expected)
    assert result['initial_actual_stop_risk']==pytest.approx(200)
    for e in events:
        if e['kind']=='add':
            assert e['protective_equity']>=e['protective_required']-1e-7
            assert e['net_at_stop']>=e['retained_target']-1e-7


def test_initial_bar_can_stop_and_costs_are_not_ignored():
    frame=source().iloc[:1].copy();frame.iloc[0]=[100,101,97,99]
    result,events=run(frame)
    assert result['status']=='complete' and result['exit_price']==98
    assert result['final_balance']==pytest.approx(9780.2)
    assert [e['kind'] for e in events]==['entry','exit']


def test_pending_add_fills_before_later_same_bar_stop():
    frame=source().iloc[:5].copy();frame.iloc[4]=[111,112,100,110]
    result,events=run(frame)
    assert result['status']=='complete' and result['adds_count']==1
    kinds=[e['kind'] for e in events]
    assert kinds.index('add')<kinds.index('exit')
    assert result['exit_price']==pytest.approx(102.99)


def test_confirmation_low_is_included_in_the_new_stop():
    frame=source().iloc[:5].copy();frame.iloc[3]=[104,111,101,110]
    _,events=run(frame)
    first=next(e for e in events if e['kind']=='stop_update')
    assert first['common_stop']==pytest.approx(100.99)


def test_pullback_high_is_frozen_even_if_intermediate_wick_is_higher():
    frame=source().iloc[:5].copy()
    frame.iloc[3]=[104,120,102,104.5]
    frame.iloc[4]=[104.5,108,103,107]
    _,events=run(frame)
    updates=[e for e in events if e['kind']=='stop_update']
    assert len(updates)==1
    assert updates[0]['common_stop']==pytest.approx(101.99)


def test_minimum_notional_does_not_destroy_feasible_initial_interval():
    result,_=run(source().iloc[:1],capital=700,initial_stop=95,initial_quantity_requested=100,
        min_notional=6000,tiers=[{'max_quantity':1e12,'mmr':.05,'max_leverage':40}])
    assert 60<=result['initial_quantity']<=71

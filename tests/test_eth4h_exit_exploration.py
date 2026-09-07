"""Causal counterexamples for the two authorized R2 research dimensions."""
from dataclasses import asdict, replace

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.eth4h_exit_exploration import BASE, BE_OFF, ExplorationReplay
from yoyo.layers.l3_backtest.eth4h_trend_candidate import CHAIN, CandidateReplay


def frame():
    f=pd.DataFrame({'open_time':pd.date_range('2024-01-01',periods=10,freq='4h',tz='UTC'),
                    'open':100.,'high':100.,'low':100.,'close':100.,'atr':1.,'slow_ma':np.arange(90.,100.),
                    'v7_long':False,'v7_short':False,'cross_long':False,'cross_short':False,
                    'entry_allowed':True,'osc':1.,'hk_dayofweek':0,'hk_hour':0})
    f.loc[1,'v7_long']=True
    return f


def test_be_switch_changes_only_one_policy_field():
    a,b=asdict(BASE),asdict(BE_OFF)
    assert [k for k in a if k!='name' and a[k]!=b[k]]==['breakeven']


def test_r1_default_behavior_is_unchanged():
    f=frame();f.loc[2,['high','close']]=[102.,101.]
    a=CandidateReplay(f,CHAIN[-1]).run(0,len(f))
    b=ExplorationReplay(f,BASE).run(0,len(f))
    pd.testing.assert_frame_equal(a['trades'],b['trades'])
    pd.testing.assert_frame_equal(a['equity'],b['equity'])


@pytest.mark.parametrize('side',[1,-1])
def test_disable_be_keeps_initial_hard_stop(side):
    f=frame();f['v7_long']=False;f.loc[1,'v7_long' if side==1 else 'v7_short']=True
    f.loc[2,'high' if side==1 else 'low']=102. if side==1 else 98.
    f.loc[4,'low' if side==1 else 'high']=96. if side==1 else 104.
    a=ExplorationReplay(f,BASE).run(0,len(f))['trades'].iloc[0]
    b=ExplorationReplay(f,BE_OFF).run(0,len(f))['trades'].iloc[0]
    assert a.exit_i==3
    assert b.exit_i==4 and b.exit_price==100-side*3
    assert a.qty==b.qty and a.initial_stop==b.initial_stop


def test_slope_gate_blocks_only_flat_entries_not_opposite_exits():
    p=replace(BE_OFF,slope_gate=True)
    f=frame();f.loc[3,'v7_short']=True
    r=ExplorationReplay(f,p)
    assert not r.entry_signal_allowed(3,-1,None)
    assert r.entry_signal_allowed(3,-1,{'direction':1})
    t=r.run(0,len(f))['trades']
    assert len(t)==1 and t.iloc[0].exit_i==4


def test_slope_gate_is_prefix_causal_and_zero_slope_rejects():
    f=frame();p=replace(BASE,slope_gate=True)
    a=ExplorationReplay(f,p);b=ExplorationReplay(f.iloc[:5],p)
    assert [a.entry_signal_allowed(i,1,None) for i in range(5)]==[b.entry_signal_allowed(i,1,None) for i in range(5)]
    f['slow_ma']=100.
    assert not ExplorationReplay(f,p).entry_signal_allowed(1,1,None)


def test_be_off_does_not_disable_same_side_ratchet():
    r=ExplorationReplay(frame(),BE_OFF)
    assert r.reset_stop(97.,98.,{'direction':1},1)==98.
    assert r.managed_stop(3,{'direction':1,'entry_price':100.},98.)==98.

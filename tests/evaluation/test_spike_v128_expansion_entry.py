"""Behavioral entry-gate checks using synthetic histories, not market outcomes."""
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation import spike_v128_expansion_entry as study


def test_expansion_prefix_causality_ties_and_gap_reset():
    cfg={'ratio_window_bars':3,'rank_history_days':4/24,'quantiles':[.2,.4,.6,.8]}
    idx=pd.date_range('2026-01-01',periods=20,freq='h',tz='UTC')
    frame=pd.DataFrame({'atr':np.arange(1,21,dtype=float),'close':100.},index=idx)
    gap=np.zeros(20,bool)
    full=study.expansion(frame,gap,60,cfg)
    for n in (7,12,19):
        pd.testing.assert_frame_equal(full.iloc[:n],study.expansion(frame.iloc[:n],gap[:n],60,cfg))
    flat=frame.assign(atr=1.)
    tied=study.expansion(flat,gap,60,cfg)
    assert tied.expansion_quintile.iloc[:6].eq(0).all()
    assert tied.expansion_quintile.iloc[6:].eq(1).all()
    gap[10]=True
    split=study.expansion(frame,gap,60,cfg)
    assert split.expansion_quintile.iloc[10:16].eq(0).all()
    assert split.expansion_quintile.iloc[16]>0


def event(i,end,q,key):
    return {'trade_key':key,'arm':'joint','symbol':'X','timeframe_min':15,'signal_i':i,
            'signal_close':f'2026-01-01T00:{i:02}:00Z','side':1,'status':'closed',
            'expansion_quintile':q,'high20':q==5,'exit_i':end}


def test_serial_inherits_position_and_admits_newly_freed_event():
    rows=[event(1,3,5,'blocked'),event(4,10,1,'old'),event(6,8,5,'new'),event(9,12,5,'after')]
    b,bs=study.select_serial(rows,'baseline',20,(4,'carry'))
    h,hs=study.select_serial(rows,'high20',20,(4,'carry'))
    assert [x['trade_key'] for x in b]==['old']
    assert [x['trade_key'] for x in h]==['new','after']
    assert bs[0]['blocking_trade']==hs[0]['blocking_trade']=='carry'
    assert hs[1]['status']=='filtered_quintile'


def test_control_failure_not_redrawn_and_gate_constrains_pool(monkeypatch):
    idx=pd.date_range('2026-01-05',periods=4,freq='h',tz='UTC')
    frame=pd.DataFrame({'ready':True},index=idx)
    prepared=SimpleNamespace(frame=frame,context=SimpleNamespace(minutes=60),atr=np.ones(4),close=np.ones(4)*100,gap=np.zeros(4,bool))
    calls=[]
    def attempt(p,i,side):
        calls.append(i);return 'next_bar_is_gap',None
    monkeypatch.setattr(study.parent,'attempt',attempt)
    cfg={'start':'2026-01-01T00:00Z','end':'2026-02-01T00:00Z','split':'2026-01-23T00:00Z','vol_bins':[.005,.02],'control_seed':1}
    trade={'signal_i':0,'side':1,'trade_key':'x','arm':'joint','symbol':'X','censored':False,'net_return':0.}
    c=study.controls_for(prepared,[trade],cfg,np.array([False,False,True,False]))
    assert len(c)==2 and not c.matched.any()
    assert c[c.control_pool=='high20'].control_sig.iloc[0]==2
    assert c.reason.eq('next_bar_is_gap').all()
    assert len(calls)==len(set(c.control_sig))


def test_parent_parity_rejects_changed_execution():
    before=pd.DataFrame({'trade_key':['x'],'net_r':[1.]})
    with pytest.raises(AssertionError):study.assert_parity(before.assign(net_r=2.),before,['trade_key'],['net_r'])

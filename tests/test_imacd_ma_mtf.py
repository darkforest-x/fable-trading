"""Causal confluence timing, latched formation and waiting counterexamples."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.imacd_ma_mtf import align_closed,formation_features,select_requests,nominate,POLICIES
from yoyo.evaluation.imacd_ma_mtf import side_masks


def test_higher_timeframe_is_unavailable_until_it_closes():
    src=pd.DataFrame(dict(md=[1.,-1.],sh=[2.,-2.],sma60_slope=[3.,-3.],eligible=[True,True]),index=pd.date_range('2024-01-01',periods=2,freq='4h',tz='UTC'))
    target=pd.date_range('2024-01-01',periods=8,freq='1h',tz='UTC')
    a=align_closed(src,240,target,60)
    assert a.md.iloc[:3].isna().all()
    assert (a.md.iloc[3:7]==1).all() and a.md.iloc[7]==-1
    src.loc[src.index[1],'md']=999
    b=align_closed(src,240,target,60)
    pd.testing.assert_frame_equal(a.iloc[:7],b.iloc[:7])


def test_source_warmup_and_lower_timeframe_last_closed_bar():
    src=pd.DataFrame(dict(md=np.arange(12.),sh=1.,sma60_slope=1.,eligible=True),index=pd.date_range('2024-01-01',periods=12,freq='15min',tz='UTC'))
    src.loc[src.index[:4],'eligible']=False
    a=align_closed(src,15,pd.date_range('2024-01-01',periods=3,freq='1h',tz='UTC'),60)
    assert pd.isna(a.md.iloc[0]) and a.md.iloc[1]==7 and a.md.iloc[2]==11


def test_wait_uses_first_confirmation_and_cancels_at_neutral():
    f=pd.DataFrame(dict(md=[0,1,2,3,0,1,2,0],dense_recent=[False,True,True,True,False,True,True,False]))
    masks={s:{p:np.zeros(8,dtype=bool) for p in POLICIES} for s in [-1,1]}
    masks[1]['P11_wait_htf'][[3,6]]=True
    assert select_requests(f,[1,5],7,'P11_wait_htf',masks)==[(1,3,1),(5,6,1)]
    f.loc[2,'md']=0
    assert select_requests(f,[1],7,'P11_wait_htf',masks)==[]


def test_wait_cannot_backfill_anchor_formation_or_wait_beyond_nine():
    f=pd.DataFrame(dict(md=np.ones(20),dense_recent=True));f.loc[1,'dense_recent']=False
    masks={s:{p:np.zeros(20,dtype=bool) for p in POLICIES} for s in [-1,1]}
    masks[1]['P11_wait_htf'][[2,12]]=True
    assert select_requests(f,[1],19,'P11_wait_htf',masks)==[]
    masks[1]['P11_wait_htf'][2]=False
    assert select_requests(f,[2],19,'P11_wait_htf',masks)==[]
    masks[1]['P11_wait_htf'][11]=True
    assert select_requests(f,[2],19,'P11_wait_htf',masks)==[(2,11,1)]


def test_formation_and_memory_are_prefix_invariant():
    x=100+np.sin(np.arange(600)/14)*4+np.arange(600)*.01
    b=pd.DataFrame(dict(open=x,high=x+1,low=x-1,close=x),index=pd.date_range('2024-01-01',periods=600,freq='1h',tz='UTC'))
    f=formation_features(b);short=formation_features(b.iloc[:451])
    pd.testing.assert_frame_equal(short,f.iloc[:451])
    # The decision candle cannot change its own preformation measurement.
    changed=b.copy();changed.loc[changed.index[450],['high','close']]+=20
    g=formation_features(changed)
    for col in ['dense_pre_bandwidth_atr_mean_12','dense_pre_pairwise_cross_count_12']:
        assert f[col].iloc[450]==g[col].iloc[450]


def test_nomination_rejects_validation_rows():
    d=pd.DataFrame(dict(fold=['replication'],entry_time=['2025-01-01T00:00:00Z']))
    with pytest.raises(AssertionError):nominate(d)


def test_neutral_extension_changes_only_known_zero_higher_state():
    x=100+np.sin(np.arange(400)/14)*4
    b=pd.DataFrame(dict(open=x,high=x+1,low=x-1,close=x),index=pd.date_range('2024-01-01',periods=400,freq='1h',tz='UTC'))
    f=formation_features(b)
    f['h_md']=-1.;f['h_sh']=-1.;f['h_sma60_slope']=1.;f['l_md']=1.
    f['dense_recent']=True;f['dense_rope_upper']=f.close-.1
    f.loc[f.index[-3:],'h_md']=[0.,1.,np.nan]
    masks=side_masks(f,1)
    assert masks['P08_htf_either'][-3:].tolist()==[False,True,False]
    assert masks['P13_htf_zero'][-3:].tolist()==[True,True,False]
    assert not masks['P13_htf_zero'][:-3].any()

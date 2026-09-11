import numpy as np
import pandas as pd
from yoyo.evaluation.spike_v6_bb_squeeze import _rsi_wilder, admissions, features, recent_compression


def bars(n=760, scale=1.):
    ix = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    c = (100 + np.sin(np.arange(n)/7))*scale
    return pd.DataFrame({"open":c, "high":c+scale, "low":c-scale, "close":c}, index=ix)


def test_scale_prefix_sigma_and_prior_threshold_are_causal():
    f=bars(); a=features(f); b=features(bars(scale=7))
    assert np.allclose(a.bb_width.dropna(), b.bb_width.dropna())
    assert np.isclose(a.bb_std_ddof0.iloc[300], np.std(f.close.iloc[101:301], ddof=0))
    changed=f.copy(); changed.iloc[700:, changed.columns.get_loc("close")]=999
    assert features(changed).iloc[:700].equals(a.iloc[:700])
    assert pd.isna(a.bb_width_p10_prior500.iloc[698]) and np.isfinite(a.bb_width_p10_prior500.iloc[699])


def test_prior_run_memory_gap_warmup_and_directional_admissions():
    f=bars(); d=features(f); i=730
    d.loc[d.index[i], "bb_prior_run3_in12"] = True
    s=pd.DataFrame({"long_signal":False,"short_signal":False},index=f.index); s.iloc[i,0]=True
    out=admissions(s,d); assert out.B.iloc[i]
    d.loc[d.index[i], "bb_width"] = d.bb_width.iloc[i-1]*.9; assert not admissions(s,d).C.iloc[i]
    d.loc[d.index[i], "bb_width"] = d.bb_width.iloc[i-1]*1.1; d.loc[d.index[i], "rsi6"] = 55; assert admissions(s,d).D.iloc[i]
    gap=pd.Series(False,index=f.index); gap.iloc[500]=True
    assert not features(f,data_gap=gap).ready.iloc[600]


def test_compression_window_current_boundary_and_gap_do_not_leak():
    ix=pd.RangeIndex(20); c=pd.Series(False,index=ix)
    c.iloc[7:10]=True; assert recent_compression(c).iloc[20-1]  # t-12..t-10 is included
    c=pd.Series(False,index=ix); c.iloc[5:8]=True; assert not recent_compression(c).iloc[19]  # t-14..t-12 excluded
    c=pd.Series(False,index=ix); c.iloc[17:20]=True; assert not recent_compression(c).iloc[19]  # current cannot count
    c=pd.Series([True,False,True,False,True],index=pd.RangeIndex(5)); assert not recent_compression(c).any()


def test_rsi_seed_and_a0_ready_contract():
    assert _rsi_wilder(pd.Series(range(8),dtype=float)).iloc[-1] == 100
    assert _rsi_wilder(pd.Series(range(8,0,-1),dtype=float)).iloc[-1] == 0
    assert _rsi_wilder(pd.Series([1.]*8)).isna().all()
    f=bars(); d=features(f); s=pd.DataFrame({"long_signal":False,"short_signal":False},index=f.index); s.iloc[10,0]=True
    a=admissions(s,d); assert a.A0.iloc[10] and not a.A.iloc[10]


def test_short_rsi_direction_is_below_50():
    f=bars(); d=features(f); i=730
    s=pd.DataFrame({"long_signal":False,"short_signal":False},index=f.index); s.iloc[i,1]=True
    d.loc[d.index[i],["ready","bb_prior_run3_in12"]]=True
    d.loc[d.index[i],"bb_width"]=d.bb_width.iloc[i-1]*1.1
    d.loc[d.index[i],"rsi6"]=45
    assert admissions(s,d).D.iloc[i]
    d.loc[d.index[i],"rsi6"]=55
    assert not admissions(s,d).D.iloc[i]


def test_gap_resets_ready_and_cannot_inherit_a_prior_squeeze_window():
    f=bars(1500); gap=pd.Series(False,index=f.index); gap.iloc[800]=True
    d=features(f,data_gap=gap)
    assert d.ready.iloc[799]
    assert not d.ready.iloc[800:1499].any()
    assert d.ready.iloc[1499]

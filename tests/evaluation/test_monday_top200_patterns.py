"""Availability and frozen-control integrity for winner-pattern research."""
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_top200_patterns import prepare, features, paired, STEP


def market():
    ix=pd.date_range('2025-01-01',periods=180,freq='5min',tz='UTC')
    close=100+np.arange(180)*.1
    return pd.DataFrame(dict(open=close-.05,high=close+.1,low=close-.1,close=close,
                             volume=np.ones(180)),index=ix)


def test_prefix_invariance_and_no_touch_bar_lookahead():
    f=market();j=150
    f.iloc[j,f.columns.get_loc('low')]=99
    f.iloc[j,f.columns.get_loc('volume')]=10
    r=dict(side=1,entry_time=str(f.index[j]),pin_time=str(f.index[j]),level=100,signal_minutes=5)
    observed=features(prepare(f),r)
    assert observed['touch_volume_peak20']
    assert not observed['touch_known_at_entry'] and not observed['volume_peak20']
    later=dict(r,entry_time=str(f.index[j+2]))
    a=features(prepare(f),later)
    b=features(prepare(f.iloc[:j+2]),later)
    assert a==b
    assert a['volume_peak20'] and a['touch_known_at_entry']
    assert pd.Timestamp(a['feature_available_at'])==pd.Timestamp(later['entry_time'])


def test_gap_prevents_admission_and_mirror_retest():
    f=market();j=150
    f.iloc[j,f.columns.get_loc('low')]=99
    r=dict(side=1,entry_time=str(f.index[j+2]),pin_time=str(f.index[j]),level=100,signal_minutes=5)
    x=features(prepare(f.drop(f.index[j+1])),r)
    assert not x['contiguous_pre_entry'] and not x['above_six'] and not x['short_ma_retest']
    f=prepare(f)
    for c in ['sma20','ema20']:f.loc[f.index[j+1],c]=114
    f.loc[f.index[j+1],['open','close','low']]=[114.2,114.5,113.9]
    assert features(f,r)['short_ma_retest']
    f.loc[f.index[j+1],'open']=113.8
    assert not features(f,r)['short_ma_retest']


def test_pair_requires_three_closed_same_week_controls():
    rows=[]
    for k in range(4):
        rows.append(dict(pair_id='a',population='signal' if k==0 else 'random',closed=True,
                         symbol='BTCUSDT',actual_entry_week='2025-01-06',side=1,bucket=-9,
                         net_r=k,net_bp=100*k))
    f=pd.DataFrame(rows)
    assert len(paired(f))==1 and paired(f).control_net_r.iloc[0]==2
    f.loc[3,'actual_entry_week']='2025-01-13'
    assert paired(f).empty
    f.loc[3,'actual_entry_week']='2025-01-06';f.loc[3,'closed']=False
    assert paired(f).empty

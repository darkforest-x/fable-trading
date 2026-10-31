"""Clock, direction and path-behavior checks for reversal labels/features."""
import numpy as np
import pandas as pd
from yoyo.evaluation.clean_reversal_labels import feature_frame,path_label,FEATURES


def frame(closes):
    c=np.asarray(closes,dtype=float)
    return pd.DataFrame(dict(open=c,high=c+.1,low=c-.1,close=c),index=pd.date_range('2026-10-26',periods=len(c),freq='15min',tz='UTC'))


def test_clean_path_beats_equal_endpoint_chop_and_failure():
    smooth=frame(100+np.arange(30)*.1)
    zigzag=frame(100+np.r_[0,np.arange(1,30)*.1+np.where(np.arange(1,30)%2,.6,-.6)])
    zigzag.iloc[-1]=smooth.iloc[-1]
    a=path_label(smooth,0,1,1);b=path_label(zigzag,0,1,1);c=path_label(smooth,0,-1,1)
    assert a['quality']>b['quality']>c['quality']==0
    assert a['max_close_drawdown_tr']==0 and b['max_close_drawdown_tr']>0
    assert pd.Timestamp(a['label_end'])==smooth.index[0]+pd.Timedelta(minutes=450)


def test_direction_mirror_is_identical_and_gap_is_not_filled():
    a=frame(100+np.arange(30)*.1);b=frame(100-np.arange(30)*.1)
    x,y=path_label(a,0,1,1),path_label(b,0,-1,1)
    for key in x:
        if key!='label_end':assert np.isclose(x[key],y[key])
    assert path_label(a.iloc[:-1],0,1,1) is None
    assert path_label(a.drop(a.index[8]),0,1,1) is None


def test_one_jump_then_flat_is_less_sustained_than_equal_endpoint_trend():
    steady=path_label(frame(100+np.arange(30)*.1),0,1,1)
    jump=path_label(frame(np.r_[100.,np.repeat(102.9,29)]),0,1,1)
    assert np.isclose(steady['net_progress_tr'],jump['net_progress_tr'])
    assert steady['quality']>jump['quality']
    assert steady['sustained_blocks_fraction']==1 and jump['sustained_blocks_fraction']==.25


def test_features_are_prefix_invariant_and_future_mutation_proof():
    rng=np.random.default_rng(21);bars=frame(100+np.cumsum(rng.normal(0,.3,300)))
    i=160;event=pd.DataFrame([dict(event_id='test',signal_open=str(bars.index[i]),side='upper',week='2026-10-26',ordinal=2,level=100,monday_high=101,monday_low=99,episode_bars=4,duration_known=True)])
    original,_=feature_frame(bars,event)
    prefix,_=feature_frame(bars.iloc[:i+1],event)
    changed=bars.copy();changed.iloc[i+1:]*=100
    event2=event.copy();event2['episode_bars']=999;event2['duration_known']=False
    future,_=feature_frame(changed,event2)
    pd.testing.assert_frame_equal(original,prefix);pd.testing.assert_frame_equal(original,future)
    assert set(original.columns)=={'event_id','tr_mean14','signal_close_price','feature_last_close'}|{'feature_'+s for s in FEATURES}


def test_no_features_cross_a_history_gap():
    bars=frame(100+np.arange(300)*.01);bars=bars.drop(bars.index[130])
    event=pd.DataFrame([dict(event_id='test',signal_open=str(bars.index[160]),side='upper',week='2026-10-26',ordinal=1,level=100,monday_high=101,monday_low=99)])
    result,excluded=feature_frame(bars,event)
    assert result.empty and excluded[0]['reason']=='incomplete_feature_lookback'

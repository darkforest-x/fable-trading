"""Synthetic lowtf V9 clock, raw-reversal and native fixed-entry parity checks."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation import spike_v8_lowtf_study as low
from yoyo.evaluation.spike_v9_eth_lowtf import admission,prepared_control,evaluate,controls


def sample(minutes=3,seed=0):
    index=pd.date_range('2025-01-04T23:00Z',periods=120,freq=f'{minutes}min')
    rng=np.random.default_rng(seed);close=100+np.cumsum(rng.normal(0,.8,len(index)))
    frame=pd.DataFrame({'open':np.r_[100,close[:-1]],'close':close,'atr':1.,'rv':2.,'ready':True},index=index)
    frame['high']=frame[['open','close']].max(axis=1)+.4;frame['low']=frame[['open','close']].min(axis=1)-.4
    frame.attrs['minutes']=minutes
    raw=pd.DataFrame({'long_signal':False,'short_signal':False},index=index)
    for j,i in enumerate(range(7,115,11)):raw.iloc[i,j%2]=True
    stream={'minutes':minutes,'name':'synthetic','venue':'test','symbol':'ETHUSDT','asset':'ETH','tick':.01}
    return frame,raw,stream


@pytest.mark.parametrize('minutes',[3,5])
def test_scheduled_sunday_and_rv_boundary_and_unknown(minutes):
    frame,raw,stream=sample(minutes)
    mask=pd.Series(True,index=frame.index)
    frame.iloc[0,frame.columns.get_loc('rv')]=50
    frame.iloc[1,frame.columns.get_loc('rv')]=50.01
    frame.iloc[2,frame.columns.get_loc('rv')]=np.nan
    got,rows=admission(frame,mask,minutes)
    assert got.iloc[0] and not got.iloc[1] and not got.iloc[2]
    close=frame.index+pd.Timedelta(minutes=minutes)
    assert not got.loc[close.dayofweek==6].any()
    assert not admission(frame,mask,minutes,None)[0].any()
    before=rows.loc[rows.signal_i<70].copy()
    frame.iloc[70:,frame.columns.get_loc('rv')]=500
    pd.testing.assert_frame_equal(before,admission(frame,mask,minutes)[1].loc[lambda d:d.signal_i<70])


@pytest.mark.parametrize('minutes',[3,5])
@pytest.mark.parametrize('seed',range(4))
def test_fixed_exit_matches_native_parent_on_each_original_entry(minutes,seed):
    frame,raw,stream=sample(minutes,seed)
    mask=raw.any(axis=1)
    _,trades=low._run(frame,raw,mask,tick=.01,minutes=minutes,end=frame.index[-1]+pd.Timedelta(minutes=minutes))
    prepared=prepared_control(frame,raw,stream)
    for row in trades.itertuples(index=False):
        fixed=evaluate(prepared,int(row.signal_i),int(row.side))
        assert bool(fixed['censored'])==bool(row.censored)
        if not row.censored:
            assert fixed['exit_i']==row.exit_i and fixed['exit_reason']==row.exit_reason
            assert fixed['net_r']==pytest.approx(row.net_r,abs=1e-10)


def test_rejected_raw_reverse_still_closes_position():
    frame,raw,stream=sample()
    frame.loc[:,['open','close']]=100.;frame['high']=100.1;frame['low']=99.9
    raw.loc[:,:]=False;raw.iloc[10,0]=True;raw.iloc[15,1]=True
    mask=pd.Series(False,index=frame.index);mask.iloc[10]=True
    _,trades=low._run(frame,raw,mask,tick=.01,minutes=3,end=frame.index[-1])
    assert len(trades)==1
    assert trades.iloc[0].exit_reason=='opposite_v6_next_open'
    assert trades.iloc[0].exit_i==16


def test_shared_event_control_choice_is_arm_independent_and_reproducible():
    frame,raw,stream=sample(3,3);prepared=prepared_control(frame,raw,stream)
    _,trades=low._run(frame,raw,raw.any(axis=1),tick=.01,minutes=3,end=frame.index[-1]+pd.Timedelta(minutes=3))
    trades['event_key']=trades.signal_i.astype(str);trades['stream']='synthetic';trades['fold']='one'
    targets=pd.concat([trades.assign(arm='v8'),trades.assign(arm='v9')],ignore_index=True)
    a=controls(prepared,targets,frame.index[0],frame.index[-1]+pd.Timedelta(minutes=3))
    b=controls(prepared,targets,frame.index[0],frame.index[-1]+pd.Timedelta(minutes=3))
    pd.testing.assert_frame_equal(a,b)
    assert a.groupby('event_key').control_signal_i.nunique().eq(1).all()
    assert (a.control_signal_i!=a.signal_i).all()
    assert a.loc[a.matched,'control_net_r'].notna().all()

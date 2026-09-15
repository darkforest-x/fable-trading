"""Synthetic entry-clock, direction, causal-render and replay controls."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_eth_yolo_entry import render_at, select_detection, replay
from yoyo.layers.l1_detection.data import add_mas
from yoyo.monitor.yolo_detector import parse_prediction, prepare_windows


def frame(n=180, minutes=15):
    index=pd.date_range('2026-01-01',periods=n,freq=f'{minutes}min',tz='UTC')
    close=100+np.sin(np.arange(n)/10)
    out=pd.DataFrame(dict(open=close,high=close+1,low=close-1,close=close,
                         volume=np.full(n,100),atr=np.full(n,1.)),index=index)
    out.attrs['minutes']=minutes
    return out


def test_last_closed_bar_and_future_mutation():
    raw=frame(); at=raw.index[150]+pd.Timedelta(minutes=15)
    a=render_at(add_mas(raw),15,at,19)
    poisoned=raw.copy(); poisoned.iloc[151:,:4]*=100
    b=render_at(add_mas(poisoned),15,at,19)
    assert a.input_pixel_sha256==b.input_pixel_sha256
    assert a.times[-1]==int(raw.index[150].value//1_000_000)
    assert a.times[-1]+15*60_000==int(at.value//1_000_000)


def test_lower_timeframe_uses_latest_closed_even_when_unaligned():
    raw=add_mas(frame(minutes=1)); at=raw.index[150]+pd.Timedelta(seconds=30)
    window=render_at(raw,1,at,18)
    assert window.times[-1]==int(raw.index[149].value//1_000_000)


def test_no_complete_or_stale_window():
    raw=add_mas(frame())
    with pytest.raises(ValueError): render_at(raw,15,raw.index[2],18)
    with pytest.raises(ValueError): render_at(raw,15,raw.index[-1]+pd.Timedelta(hours=1),18)


def test_direction_and_structure_required_ties_stable():
    base=dict(structural_pass=True,side='long',confidence=.8,window_len=19,detection_id='b')
    proposals=[base,dict(base,confidence=.99,side='short'),dict(base,confidence=.98,structural_pass=False),
               dict(base,window_len=18,detection_id='a')]
    assert select_detection(proposals,1)['detection_id']=='a'
    assert select_detection([],1) is None


def test_admission_filter_retains_raw_reverse_exit():
    f=frame(8,minutes=60)
    f[['open','close']]=100.;f['high']=100.5;f['low']=99.5
    raw=pd.DataFrame(dict(long_signal=[False]*8,short_signal=[False]*8),index=f.index)
    raw.loc[f.index[4],'long_signal']=True;raw.loc[f.index[6],'short_signal']=True
    allowed=pd.Series(False,index=f.index);allowed.iloc[4]=True
    trades=replay(dict(frame=f,raw=raw,minutes=60),allowed,.01)
    assert len(trades)==1
    assert trades.iloc[0].entry_i==5
    assert trades.iloc[0].exit_i==7
    assert trades.iloc[0].exit_reason=='opposite_v6_next_open'
    assert trades.iloc[0].net_return==pytest.approx(-.002)


def test_unclosed_last_trade_is_censored():
    f=frame(7,minutes=60);f[['open','close']]=100.;f['high']=100.5;f['low']=99.5
    raw=pd.DataFrame(dict(long_signal=[False]*7,short_signal=[False]*7),index=f.index)
    raw.loc[f.index[5],'long_signal']=True
    allowed=pd.Series(False,index=f.index);allowed.iloc[5]=True
    trades=replay(dict(frame=f,raw=raw,minutes=60),allowed,.01)
    assert len(trades)==1 and trades.iloc[0].censored


def test_research_pixels_equal_monitor_original_adapter():
    data=add_mas(frame())
    rows=[]
    for stamp,r in data.iterrows():
        row=dict(t=int(stamp.value//1_000_000),o=r.open,h=r.high,l=r.low,c=r.close,v=r.volume)
        row.update({f'{k}{p}':r[f'{k}{p}'] for p in (20,60,120) for k in ('sma','ema')})
        rows.append(row)
    original=prepare_windows(rows,'15m',rows[-1]['t'])
    for length,w in zip((18,19),original):
        actual=render_at(data,15,data.index[-1]+pd.Timedelta(minutes=15),length)
        assert actual.input_pixel_sha256==w.input_pixel_sha256


@pytest.mark.parametrize('core,post,passed',[(4,2,True),(5,9,True),(3,2,False),(6,2,False),(4,1,False),(4,10,False)])
def test_box_mapping_preserves_frozen_core_and_post_contract(core,post,passed):
    data=add_mas(frame());w=render_at(data,15,data.index[-1]+pd.Timedelta(minutes=15),19)
    b=18-post;a=b-core+1
    left,right=w.transform.x_at(a),w.transform.x_at(b)
    box=[(left+right)/2/w.transform.width,.5,(right-left)/w.transform.width,.2]
    proposal=parse_prediction([box],[0],[.8],w,'ETH-USDT-SWAP','15m')[0]
    assert proposal['core_length_bars']==core and proposal['post_bars']==post
    assert proposal['structural_pass']==passed

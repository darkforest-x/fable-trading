"""Synthetic causal contracts for the frozen V8 MA-cycle state machine."""
from __future__ import annotations
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_v8_ma_cycle_study import _attach_trade_details, _process, cycle_features


def _bars(n=290):
    index=pd.date_range('2025-01-01',periods=n,freq='h',tz='UTC')
    base=np.arange(n)*0.0+100
    out=pd.DataFrame({'open':base,'high':base+1,'low':base-1,'close':base,'atr':np.ones(n)},index=index)
    for p,off in ((20,.02),(60,0.0),(120,-.02)):
        out[f's{p}']=base+off; out[f'e{p}']=base+off+.005
    return out


def _launch_shape(frame, launch=260, *, expand=False):
    for j in range(launch-3,launch+3):
        value=100+(j-(launch-3))*.2
        frame.loc[frame.index[j],['s20','e20']]=[value+.010,value+.015]
        frame.loc[frame.index[j],['s60','e60']]=[value+.000,value+.005]
        frame.loc[frame.index[j],['s120','e120']]=[value-.010,value-.005]
    frame.loc[frame.index[launch],['open','high','low','close']]=[102,103,101.5,102.5]
    if expand:
        j=launch+1
        frame.loc[frame.index[j],['s20','e20','s60','e60','s120','e120']]=[102.0,102.1,100.8,100.9,99.5,99.6]
        frame.loc[frame.index[j],['open','high','low','close']]=[103,104,102,103.5]
    return frame


def test_failed_launch_is_prioritized_and_cannot_rearm_same_bar():
    bars=_launch_shape(_bars()); gap=pd.Series(False,index=bars.index)
    launch=260; bars.loc[bars.index[launch+1],['open','high','low','close']]=[100,101,99,100]
    got=cycle_features(bars,gap,60)
    assert got.transition.iloc[launch]=='launch'
    assert got.transition.iloc[launch+1]=='failed_launch'
    assert got.state.iloc[launch+1]=='awaiting_compression'


def test_expansion_and_gap_reset_are_explicit():
    bars=_launch_shape(_bars(),expand=True); gap=pd.Series(False,index=bars.index)
    got=cycle_features(bars,gap,60)
    assert got.transition.iloc[260]=='launch'
    assert got.transition.iloc[261]=='expansion'
    gap.iloc[270]=True
    reset=cycle_features(bars,gap,60)
    assert reset.state.iloc[270]=='unknown'
    assert reset.episode_id.iloc[270]==-1


def test_complete_cycle_reconsolidates_then_requires_new_compression_episode():
    bars=_launch_shape(_bars(),expand=True); gap=pd.Series(False,index=bars.index)
    # Three narrow, non-unanimous bars collapse the prior expansion; only the
    # following three compact bars may arm a new episode.
    for j in range(262,268):
        bars.loc[bars.index[j],['s20','e20','s60','e60','s120','e120']]=[100.01,100.015,100.0,100.005,99.99,99.995]
        bars.loc[bars.index[j],['open','high','low','close']]=[100,100.2,99.8,100]
    got=cycle_features(bars,gap,60)
    assert got.transition.iloc[264]=='reconsolidation'
    assert got.transition.iloc[267]=='armed'
    assert got.episode_id.iloc[267] > got.episode_id.iloc[261]


def test_prefix_features_do_not_change_when_future_bars_change():
    bars=_launch_shape(_bars()); gap=pd.Series(False,index=bars.index); cut=270
    full=cycle_features(bars,gap,60)
    changed=bars.copy(); changed.iloc[cut:,changed.columns.get_loc('close')]+=50
    changed.iloc[cut:,changed.columns.get_loc('s20')]+=50
    prefix=cycle_features(changed.iloc[:cut].copy(),gap.iloc[:cut].copy(),60)
    cols=['state','state_direction','episode_id','launch_i','width_close','compression_threshold','causal_volatility_bucket']
    pd.testing.assert_frame_equal(full.iloc[:cut][cols],prefix[cols])


def test_armed_episode_expires_after_twelve_bars_without_new_compression():
    bars=_bars(); gap=pd.Series(False,index=bars.index)
    got=cycle_features(bars,gap,60)
    armed=np.flatnonzero(got.transition.eq('armed').to_numpy())[0]
    bars.iloc[armed+1:,bars.columns.get_indexer(['s20','e20'])]+=5
    expired=cycle_features(bars,gap,60)
    assert 'expired' in expired.transition.iloc[armed+1:armed+15].tolist()


def test_future_milestone_cannot_leave_episode_or_precede_confirmation():
    index=pd.date_range('2025-01-01',periods=5,freq='h',tz='UTC')
    feat=pd.DataFrame({'episode_id':[0,0,1,1,1],'state':['launch','expansion','launch','expansion','expansion'],
                       'state_direction':[1,1,1,1,1],'order_long':[0,9,0,12,12],'order_short':[0]*5},index=index)
    bars=pd.DataFrame({'open':[100,101,102,103,104],'high':[101]*5,'low':[99]*5},index=index)
    feat.attrs['bars']=bars; feat.attrs['gap']=pd.Series(False,index=index)
    events=pd.DataFrame([{'trade_id':'x','stream_key':'s','signal_bar_open':index[0],'period':'development','side':1,
                          'timeframe_min':60,'entry_time':index[1],'entry_price':100.,'exit_time':index[4],
                          'initial_stop':90.,'initial_risk':10.}])
    got=_process(events,feat)
    expansion=got.loc[got.milestone_target.eq('expansion')].iloc[0]
    order12=got.loc[got.milestone_target.eq('order12')].iloc[0]
    assert expansion.status=='milestone' and expansion.delay_bars==1
    assert order12.status=='episode_ended'  # order12 exists only in later episode 1


def test_same_entry_schema_keeps_unexecuted_events_without_trade_reference(tmp_path):
    replay=tmp_path/'replay'; streams=replay/'streams'; streams.mkdir(parents=True)
    pd.DataFrame([{'trade_id':'executed','arm':'v7','entry_price':1.,'initial_stop':.9,'initial_risk':.1},
                  {'trade_id':'executed','arm':'v8','entry_price':2.,'initial_stop':1.8,'initial_risk':.2}]).to_csv(streams/'s.trades.csv.gz',index=False)
    # These are the relevant genuine same_entry_evidence fields: executed V8
    # rows have a trade ID, while rejected/blocked V8 events retain NaN.
    events=pd.DataFrame([{'stream_key':'s','trade_id':'executed','signal_bar_open':'2025-01-01T00:00:00Z','side':1,'executed':True,'closed':True,'scoring_closed':True},
                         {'stream_key':'s','trade_id':np.nan,'signal_bar_open':'2025-01-01T01:00:00Z','side':-1,'executed':False,'closed':False,'scoring_closed':False}])
    got=_attach_trade_details(events,replay)
    assert len(got)==2
    assert got.loc[0,'entry_price']==2.
    assert pd.isna(got.loc[1,'entry_price'])

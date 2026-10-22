"""Causal morphology and outcome-group boundaries, without native replays."""
from types import SimpleNamespace

import numpy as np
import pandas as pd

from yoyo.evaluation.range_v237_winner_shapes import stop_bounds,chronological_masks,features


def test_stop_candle_ambiguity_and_entry_candle_exclusion():
    bars=np.array([[100,200,50,100],[100,109,91,100],[100,112,89,95]],float)
    r=SimpleNamespace(reason=1,entry_i=0,exit_i=2,side=1,entry=100,risk=10)
    assert stop_bounds(bars,r)==(.9,1.2)
    bars[2,0]=89
    assert stop_bounds(bars,r)==(.9,.9)


def test_short_mirror_and_exact_one_r():
    bars=np.array([[100,200,50,100],[100,109,91,100],[100,112,90,110]],float)
    r=SimpleNamespace(reason=1,entry_i=0,exit_i=2,side=-1,entry=100,risk=10)
    assert stop_bounds(bars,r)==(.9,1.)
    r.reason=2
    assert np.isnan(stop_bounds(bars,r)).all()


def test_split_purges_open_and_cross_split_outcomes():
    d=pd.DataFrame(dict(entry_time=['2025-01-01Z'.replace('Z','T00:00Z')]*3+['2025-01-04T00:00Z'],
        exit_time=['2025-01-02T00:00Z','2025-01-03T00:00Z','2025-01-05T00:00Z','2025-01-05T00:00Z'],reason=[1,1,0,1]))
    first,later=chronological_masks(d,'2025-01-03T00:00Z')
    assert first.tolist()==[True,False,False,False]
    assert later.tolist()==[False,False,False,True]


def test_feature_prefix_ignores_future_prices_and_outcome_fields():
    rng=np.random.default_rng(8);n=5000
    close=100+np.cumsum(rng.normal(0,.05,n));opened=np.r_[close[0],close[:-1]]
    frame=pd.DataFrame(dict(open=opened,high=np.maximum(opened,close)+.1,
        low=np.minimum(opened,close)-.1,close=close),index=pd.date_range('2025-01-01T00:05Z',periods=n,freq='5min'))
    e=4003;j=3999
    row=pd.DataFrame([dict(entry_i=e,lane=5,side=1,original_close_ns=frame.index[j].value,
        event_id='test',mode='causal',entry=close[e],risk=1.)])
    full=features(frame,row,.01)
    row['net_r']=100000
    prefix=features(frame.iloc[:e+1],row,.01)
    pd.testing.assert_frame_equal(full,prefix)

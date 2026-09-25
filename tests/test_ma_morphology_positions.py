"""Behavioral regressions for actual positions, inherited geometry and clocks."""
import hashlib

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets import ma_profit_dataset as renderer
from yoyo.datasets.ma_morphology_positions import render_positions
from yoyo.datasets.ma_morphology_training_package import _audit_position_row, MorphologyTrainingError
from yoyo.datasets.ma_morphology_background import screen_window


def sample():
    time=pd.date_range('2025-01-01',periods=1300,freq='3min',tz='UTC')
    price=100+np.arange(1300)*.001+np.sin(np.arange(1300)/8)*.03
    frame=pd.DataFrame({'open_time':time,'open':price,'high':price+.04,'low':price-.04,'close':price+.01,'volume':100.})
    png,box,_=renderer._window_asset(frame,core_start_i=1211,core_end_i=1215,pre_bars=9,post_bars=5,support_start_i=0,price_scale=renderer.VISIBLE_RANGE_PRICE_SCALE)
    label=renderer.label_line({'positive':True,'class_id':0,'box':box})
    row={'event_id':'fixture','class_id':0,'bar_minutes':3,'core_bars':5,'core_start_time':time[1211].isoformat(),'core_end_time':time[1215].isoformat(),'image_sha256':hashlib.sha256(png).hexdigest()}
    return frame,row,label


def test_three_windows_move_same_physical_box_at_equal_candle_density():
    frame,row,label=sample();views=render_positions(frame,row,label)
    assert {v['visible_bars'] for v in views.values()} == {21}
    assert [v['post_bars'] for v in views.values()] == [5,8,11]
    assert len({(v['box']['core_relative_left'],v['box']['core_relative_right'],v['box']['price_high'],v['box']['price_low']) for v in views.values()})==1
    centers=[float(v['label'].split()[1]) for v in views.values()]
    assert centers[0]-centers[1]>.14 and centers[1]-centers[2]>.14
    assert len({v['decision_at_utc'] for v in views.values()})==3


def test_each_view_ignores_prices_after_its_own_endpoint():
    frame,row,label=sample();before=render_positions(frame,row,label)
    changed=frame.copy();changed.loc[1221:,['open','high','low','close']]*=1.2
    after=render_positions(changed,row,label)
    assert before['R5']==after['R5']
    assert before['R8']['png']!=after['R8']['png']
    changed=frame.copy();changed.loc[1227:,['open','high','low','close']]*=2
    assert render_positions(changed,row,label)==before


def test_position_gate_rejects_constant_anchor_false_clock_and_detached_box():
    frame,row,label=sample();r=render_positions(frame,row,label)['R8']
    clean={**row,**r,'variant':'R8','visible_end_close_time_utc':r['decision_at_utc'],'original_decision_at_utc':frame.open_time.iloc[1221].isoformat()}
    _audit_position_row(clean)
    for edit in ({'post_bars':5},{'decision_at_utc':clean['original_decision_at_utc']},{'box':{**r['box'],'bar_right':r['box']['bar_right']+2}}):
        with pytest.raises(MorphologyTrainingError):_audit_position_row({**clean,**edit})


def test_expanded_negative_screen_reads_the_new_visible_endpoint_only():
    frame,row,_=sample()
    # A NaN in newly visible context must now reject; it never affects old post5.
    before=screen_window(frame,core_start_i=1211,core_end_i=1215,bar_minutes=3)
    frame.loc[1224,'close']=np.nan
    assert screen_window(frame,core_start_i=1211,core_end_i=1215,bar_minutes=3)==before
    extended=screen_window(frame,core_start_i=1211,core_end_i=1215,bar_minutes=3,post_bars=11)
    assert not extended['accepted'] and 'nonfinite_ohlc_in_support' in extended['reasons']
    with pytest.raises(ValueError):screen_window(frame,core_start_i=1211,core_end_i=1215,bar_minutes=3,post_bars=2)

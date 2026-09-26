"""Replay images must preserve training pixels and each observation cutoff."""
import hashlib

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets.ma_morphology_early_dataset import render_known_prefixes
from yoyo.evaluation.ma_early_replay import render_window, project_target, iou


def fixture():
    close=100+np.sin(np.arange(1240)/30)*.2
    frame=pd.DataFrame({'open_time':pd.date_range('2025-01-01',periods=1240,freq='3min',tz='UTC'),
        'open':close-.03,'high':close+.1,'low':close-.1,'close':close,'volume':100.})
    row={'core_bars':5,'bar_minutes':3,'class_id':0}
    actual={'bar_left':8.65,'bar_right':13.35,'price_high':float(frame.high.iloc[1211:1216].max()),
            'price_low':float(frame.low.iloc[1211:1216].min())}
    return frame,row,actual


@pytest.mark.parametrize('post',[0,1,2,5])
def test_generic_label_blind_replay_equals_frozen_training_renderer(post):
    frame,row,actual=fixture()
    expected=render_known_prefixes(frame,row,actual,post)['P9']
    png,meta=render_window(frame,1215+post,expected['visible_bars'],3)
    assert png==expected['png']
    assert meta['decision_at_utc']==expected['decision_at_utc']
    row['box']=expected['box']
    assert project_target(row,meta)==pytest.approx(expected['box']['pixel_box'])


def test_future_mutations_cannot_affect_pixels_or_price_scale():
    frame,_,_=fixture();original=render_window(frame,1216,14,3)
    frame.loc[1217:,['open','high','low','close']]*=99
    assert render_window(frame,1216,14,3)==original
    # A wrong observation endpoint must produce a different, later input.
    assert hashlib.sha256(render_window(frame,1217,14,3)[0]).digest()!=hashlib.sha256(original[0]).digest()


def test_missing_warmup_and_visible_gaps_are_not_padded_or_filled():
    frame,_,_=fixture()
    with pytest.raises(ValueError,match='warmup'):render_window(frame,1210,17,3)
    frame.loc[1210,'open_time']+=pd.Timedelta(minutes=1)
    with pytest.raises(ValueError,match='gapped'):render_window(frame,1216,14,3)


def test_box_overlap_does_not_credit_disjoint_or_shifted_targets():
    assert iou([0,0,10,10],[0,0,10,10])==1
    assert iou([0,0,10,10],[10,0,20,10])==0
    assert iou([0,0,10,10],[5,0,15,10])==pytest.approx(1/3)

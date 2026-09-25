"""Review-only future expansion must preserve the actual training-label geometry."""
import hashlib

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets import ma_profit_dataset as renderer
from yoyo.datasets.ma_morphology_future_review import review_assets


def sample(positive=True):
    time=pd.date_range('2025-01-01',periods=1300,freq='3min',tz='UTC')
    price=100+np.arange(1300)*.001+np.sin(np.arange(1300)/8)*.03
    frame=pd.DataFrame({'open_time':time,'open':price,'high':price+.04,'low':price-.04,'close':price+.01,'volume':100.})
    png,box,_=renderer._window_asset(frame,core_start_i=1211,core_end_i=1215,pre_bars=7,post_bars=5,support_start_i=0,price_scale=renderer.VISIBLE_RANGE_PRICE_SCALE)
    label=renderer.label_line({'positive':True,'class_id':0,'box':box}) if positive else ''
    row={'event_id':'test','bar_minutes':3,'core_bars':5,'variant':'P7','class_id':0 if positive else None,
         'core_start_time':time[1211].isoformat(),'core_end_time':time[1215].isoformat(),
         'decision_at_utc':time[1221].isoformat(),'image_sha256':hashlib.sha256(png).hexdigest()}
    return frame,row,label


def test_future_mutation_cannot_move_original_box_or_change_training_crop():
    frame,row,label=sample();before=review_assets(frame,row,label)
    changed=frame.copy();changed.loc[1221:,['open','high','low','close']]*=1.2
    after=review_assets(changed,row,label)
    assert before['box']==after['box']
    assert before['training_bars']==17 and before['review_bars']==57
    assert before['box']['bar_left']<7<before['box']['bar_right']<12
    assert before['projected_box'][2]<before['cutoff_x']
    assert not np.array_equal(np.asarray(before['image']),np.asarray(after['image']))


def test_background_has_no_invented_target_box():
    frame,row,label=sample(False);result=review_assets(frame,row,label)
    assert result['box'] is None and result['projected_box'] is None


def test_missing_future_bar_is_rejected_and_wrong_training_bytes_fail_closed():
    frame,row,label=sample()
    with pytest.raises(ValueError,match='Incomplete/gapped'):
        review_assets(frame.drop(index=1230),row,label)
    row['image_sha256']='0'*64
    with pytest.raises(ValueError,match='replay SHA mismatch'):
        review_assets(frame,row,label)

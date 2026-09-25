"""Low-latency dataset regressions: timing, causality, geometry and negatives."""
import hashlib

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets import ma_profit_dataset as renderer
from yoyo.datasets.ma_morphology_early_dataset import earliest_launch, early_negative_screen, render_known_prefixes, render_views
from yoyo.datasets.ma_morphology_future_review import label_coordinates, transform
from yoyo.datasets.ma_morphology_training_package import _audit_early_row, MorphologyTrainingError


HARD={'ma_envelope_atr_max':1.5,'ma_spread_end_atr_max':1.1,'max_body_atr_max':1.2,
      'candle_envelope_atr_max':2.8,'minimum_close_to_ma_atr_max':1.0}


def sample():
    t=pd.date_range('2025-01-01',periods=1240,freq='3min',tz='UTC')
    f=pd.DataFrame({'open_time':t,'open':100.,'high':100.1,'low':99.9,'close':100.,'volume':100.})
    row={'event_id':'fixture','class_id':0,'sample_kind':'positive','core_bars':5,'bar_minutes':3,
         'core_start_time':t[1211].isoformat(),'core_end_time':t[1215].isoformat()}
    return f,row


def actual_geometry(frame,row):
    png,box,_=renderer._window_asset(frame,core_start_i=1211,core_end_i=1215,pre_bars=9,post_bars=5,
        support_start_i=0,price_scale=renderer.VISIBLE_RANGE_PRICE_SCALE)
    row['image_sha256']=hashlib.sha256(png).hexdigest()
    label=renderer.label_line({'positive':True,'class_id':0,'box':box})
    visible=renderer.add_hl2_mas(frame.iloc[:1221]).iloc[1202:].reset_index(drop=True)
    actual=label_coordinates(label,transform(visible,1280,742))
    return label,actual


def test_first_observation_varies_by_case_and_does_not_wait_for_layout():
    for post in (1,2,4,5):
        f,row=sample();f.loc[1215+post,['high','close']]=[101.1,101.]
        first,evidence=earliest_launch(f,row)
        assert first==post and len(evidence)==post
        changed=f.copy();changed.loc[1216+post:,['open','high','low','close']]*=4
        assert earliest_launch(changed,row)==(first,evidence)
    f,row=sample()
    assert earliest_launch(f,row)[0] is None


def test_full_edge_candles_original_box_and_each_own_time_boundary():
    f,row=sample();label,box=actual_geometry(f,row)
    for post in (0,1,2,5):
        views=render_views(f,row,label,post)
        assert len({v['decision_at_utc'] for v in views.values()})==1
        changed=f.copy();changed.loc[1216+post:,['open','high','low','close']]*=3
        assert render_known_prefixes(changed,row,box,post)==views
        for key,v in views.items():
            x0,y0,x1,y1=v['box']['pixel_box']
            assert 0<=x0<x1<=1280 and 0<=y0<y1<=742
            assert v['chart_x_left']-v['candle_half_w']>=12
            assert v['chart_x_left']+v['chart_plot_w']+v['candle_half_w']<=1268
            if post:
                entry={**row,**v,'variant':key,'visible_end_close_time_utc':v['decision_at_utc'],
                       'source_replay_sha_matches':True,'earliest_launch_post':post}
                _audit_early_row(entry)
                entry['decision_at_utc']=f.open_time.iloc[1226].isoformat()
                with pytest.raises(MorphologyTrainingError,match='clock'):_audit_early_row(entry)


def test_negative_extra_veto_is_causal_and_rejects_visible_dense_launch():
    f,row=sample()
    before=early_negative_screen(f,row,1,HARD)
    assert before['accepted']
    f.loc[1217:,['open','high','low','close']]*=4
    assert early_negative_screen(f,row,1,HARD)==before
    f.loc[1216,['high','close']]=[101.1,101.]
    after=early_negative_screen(f,row,1,HARD)
    assert not after['accepted'] and after['ambiguous']

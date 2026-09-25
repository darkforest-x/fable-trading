"""Early observation rendering must preserve clocks and inherited geometry."""
import hashlib

import numpy as np
import pandas as pd

from yoyo.datasets import ma_profit_dataset as renderer
from yoyo.datasets.ma_morphology_early_review import render_prefix, overlay
from yoyo.datasets.ma_morphology_future_review import label_coordinates, transform


def sample():
    time=pd.date_range('2025-01-01',periods=1240,freq='3min',tz='UTC')
    price=100+np.arange(1240)*.001+np.sin(np.arange(1240)/8)*.03
    frame=pd.DataFrame({'open_time':time,'open':price,'high':price+.04,'low':price-.04,'close':price+.01})
    png,box,_=renderer._window_asset(frame,core_start_i=1211,core_end_i=1215,pre_bars=9,
        post_bars=5,support_start_i=0,price_scale=renderer.VISIBLE_RANGE_PRICE_SCALE)
    label=renderer.label_line({'positive':True,'class_id':0,'box':box})
    visible=renderer.add_hl2_mas(frame.iloc[:1221]).iloc[1202:].reset_index(drop=True)
    actual=label_coordinates(label,transform(visible,1280,742))
    row={'core_bars':5,'variant':'P9','bar_minutes':3,'core_start_time':time[1211].isoformat(),'core_end_time':time[1215].isoformat()}
    return frame,row,actual,png


def test_each_prefix_ignores_all_later_prices():
    frame,row,box,_=sample()
    for post in range(6):
        before=render_prefix(frame,row,box,post)
        changed=frame.copy()
        changed.loc[1216+post:,['open','high','low','close']]*=2
        assert render_prefix(changed,row,box,post)==before


def test_actual_box_and_old_pixels_survive_earlier_crops(tmp_path):
    frame,row,box,old_png=sample()
    for post in range(6):
        v=render_prefix(frame,row,box,post)
        assert v['delay_after_core_close_minutes']==3*post
        assert v['box_inside_canvas'] is (post > 0)
        visible=renderer.add_hl2_mas(frame.iloc[:1216+post]).iloc[1202:].reset_index(drop=True)
        tf=transform(visible,1280,742)
        x0,y0,x1,y1=v['pixel_box']
        assert np.isclose((x0-tf.left)/tf.plot_w*(tf.n_bars-1),box['bar_left'])
        assert np.isclose((x1-tf.left)/tf.plot_w*(tf.n_bars-1),box['bar_right'])
        assert np.isclose(tf.price_max-(y0-tf.top)/tf.plot_h*(tf.price_max-tf.price_min),box['price_high'])
        overlay(v,'fixture',tmp_path/f'post{post}.png')
    assert hashlib.sha256(v['png']).hexdigest()==hashlib.sha256(old_png).hexdigest()


def test_old_empty_label_does_not_automatically_adjudicate_early_negative():
    frame,row,_,_=sample()
    v=render_prefix(frame,row,None,0)
    assert v['pixel_box'] is None
    assert v['class_at_this_prefix']=='unadjudicated'
    assert not v['training_eligible']

"""Behavioural checks for the dual-sided serial MA filter experiment."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_ma_launch_study as study


def prepared():
    n=110
    f=pd.DataFrame({'open':100.,'high':100.2,'low':99.8,'close':100.,'atr':1.},
                   index=pd.date_range('2025-01-01',periods=n,freq='15min',tz='UTC'))
    sides=np.zeros(n,int);sides[[10,30,90]]=1;sides[[50,70]]=-1
    return study.source.prepared_arm(f,np.zeros(n,bool),sides,'test:15m',
                                    {'symbol':'TEST','asset':'TEST','timeframe':'15m'},15,.01)


def test_filter_releases_occupancy_for_later_candidate():
    p=prepared()
    base,_=study.execute_arm(p,p.raw_side!=0,'baseline')
    mask=np.zeros(len(p.frame),bool);mask[30]=True
    filtered,_=study.execute_arm(p,mask,'ma_hard')
    assert 10 in base.signal_i.to_list() and 30 not in base.signal_i.to_list()
    assert filtered.signal_i.to_list()==[30]
    # A rejected short still closes the long: the filter must not rewrite exits.
    assert filtered.iloc[0].exit_i==51 and filtered.iloc[0].side==1
    assert np.array_equal(p.raw_side,prepared().raw_side)


def test_baseline_matches_published_engine_and_keeps_both_sides():
    from dataclasses import replace
    p=prepared();mask=p.raw_side!=0
    ours,_=study.execute_arm(p,mask,'baseline')
    expected,_,_=study.shared.engine.replay_serial(p.context,arm='v8',enable_be=False,
                                                  prepared=replace(p,allowed=mask))
    assert set(ours.side)=={-1,1}
    pd.testing.assert_frame_equal(ours[expected.columns],expected)


def test_empty_entry_mask_still_returns_schema():
    p=prepared();trades,_=study.execute_arm(p,np.zeros(len(p.frame),bool),'ma_grade_a')
    assert trades.empty and {'arm','event_key','net_r','censored'} <= set(trades.columns)


def test_receipt_detects_hash_drift(tmp_path):
    artifact=tmp_path/'trades.csv.gz';artifact.write_bytes(b'original')
    receipt={'run_identity':'abc','input_sha256':'def','files':{'trades.csv.gz':study.source.digest(artifact)}}
    (tmp_path/'receipt.json').write_text(json.dumps(receipt))
    assert study.validate_receipt(tmp_path,'abc','def')==receipt
    artifact.write_bytes(b'changed')
    with pytest.raises(ValueError,match='artifact drift'):
        study.validate_receipt(tmp_path,'abc','def')
    with pytest.raises(ValueError,match='identity drift'):
        study.validate_receipt(tmp_path,'other','def')

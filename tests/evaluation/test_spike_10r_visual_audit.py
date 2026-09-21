"""Chronology and sampling controls for the human visual audit pack."""
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from yoyo.evaluation import spike_10r_visual_audit as v


def test_future_mutation_cannot_enter_causal_view():
    idx=pd.date_range('2025-01-01',periods=160,freq='h',tz='UTC')
    f=pd.DataFrame({c:np.arange(160)+100. for c in v.COLS},index=idx)
    end=idx[128]+pd.Timedelta(hours=1)
    before,status=v.causal_view(f,end,60)
    f.loc[f.index>=end,:]=1e8
    after,changed=v.causal_view(f,end,60)
    assert status==changed=='ready'
    assert len(before)==128 and before.index[-1]==idx[128]
    assert_frame_equal(before,after)


def sample():
    times=pd.to_datetime(['2025-01-01','2025-01-01 01:00','2025-01-05','2025-01-06','2025-04-05','2025-01-07'],utc=True,format='mixed')
    return pd.DataFrame(dict(event_key=list('abcdef'),stream_key=['same']*5+['other'],
        available_at=times,timeframe_min=60,atr_fraction=.01,valid_entry=True,censored=False,
        net_r=[12.,0.,-1.,2.,-1.,-1.]))


def test_match_same_stream_quarter_bucket_distance_no_winners_or_reuse():
    c=sample();c.loc[3,'atr_fraction']=.08
    cases,pairs=v.select_cases(c)
    assert pairs.control_event_key.tolist()==['c']
    assert cases.event_key.tolist()==['a','c']
    assert_frame_equal(cases,v.select_cases(c)[0])
    c.loc[1,'net_r']=11.
    cases,pairs=v.select_cases(c)
    assert pairs.control_event_key.dropna().tolist()==['c']
    assert len(cases)==3


def test_blinded_repeat_ids_are_unique_deterministic_and_preserve_originals():
    c=sample();a=v.blinded_order(c,3);b=v.blinded_order(c,3)
    assert_frame_equal(a,b)
    assert len(a)==9 and a.repeat.sum()==3 and a.visual_id.is_unique
    assert set(a.loc[~a.repeat,'event_key'])==set(c.event_key)

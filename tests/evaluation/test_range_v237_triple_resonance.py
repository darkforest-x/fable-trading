"""Inclusive5H positions, independent close availability and same-side gates."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.range_v237_triple_resonance import match_hour,HOUR


def event(q=10*HOUR):
    return pd.DataFrame([dict(event_id='a',matched_ns=q,side=-1,
        publication_ns=q+HOUR//4,original_close_ns=q-HOUR//4)])


@pytest.mark.parametrize('offset,expected',[(-6,'rejected'),(-5,'matched'),(0,'matched'),(5,'matched'),(6,'rejected')])
def test_inclusive_five_hour_open_distance(offset,expected):
    opened=np.array([(10+offset)*HOUR]);closed=opened+HOUR
    r=match_hour(event(),opened,closed,np.array([-1]),30*HOUR).iloc[0]
    assert r.status==expected
    if expected=='matched':assert r.publication_ns==max(10*HOUR+HOUR//4,closed[0])


def test_owner_example_one_to_six_is_included_and_five_minute_close_not_entry():
    q=6*HOUR;r=event(q);r.loc[0,'original_close_ns']=5*HOUR+3*HOUR//4
    x=match_hour(r,np.array([HOUR]),np.array([2*HOUR]),np.array([-1]),10*HOUR).iloc[0]
    assert x.status=='matched' and x.publication_ns==q+HOUR//4 and x.extra_wait_minutes==0


def test_wrong_side_rejected_and_earliest_same_side_used():
    opened=np.array([5,7,9,11])*HOUR
    r=match_hour(event(),opened,opened+HOUR,np.array([1,-1,-1,-1]),30*HOUR).iloc[0]
    assert r.hour_open_ns==7*HOUR
    assert match_hour(event(),opened,opened+HOUR,np.ones(4,int),30*HOUR).status.iloc[0]=='rejected'


def test_future_hour_cannot_be_known_before_its_close():
    opened=np.array([15*HOUR]);closed=opened+HOUR
    r=match_hour(event(),opened,closed,np.array([-1]),15*HOUR+HOUR//2).iloc[0]
    assert r.status=='pending'
    r=match_hour(event(),opened,closed,np.array([-1]),16*HOUR).iloc[0]
    assert r.status=='matched' and r.publication_ns==16*HOUR
    assert r.extra_wait_minutes==345


def test_missing_hour_window_matures_on_hourly_not_quarterly_clock():
    none=np.array([],dtype=np.int64)
    r=match_hour(event(10*HOUR+HOUR//4),none,none,none,16*HOUR).iloc[0]
    assert r.status=='rejected' and r.window_end_ns==16*HOUR

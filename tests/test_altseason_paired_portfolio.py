"""Matching availability must not select by future trade profit."""
import pandas as pd
import pytest
from yoyo.evaluation.altseason_paired_portfolio import matched_sets


def fixture():
    common=dict(valid=True,venue='gate',asset='A',instrument='gate:A',minutes=60,arm='focus_md',exit_rule='md',decision_time=pd.Timestamp('2026-08-05T12:00Z'))
    actual=pd.DataFrame([dict(common,event_id='a',net_return=-.3),dict(common,event_id='b',net_return=3.)])
    controls=pd.DataFrame([dict(common,event_id='c',matched_event_id='a',control_number=0)])
    return actual,controls


def test_membership_depends_on_control_identity_not_winner():
    a,c=fixture();_,paired,_=matched_sets(a,c)
    assert paired.event_id.tolist()==['a']
    a['net_return']=[999,-999]
    assert matched_sets(a,c)[1].event_id.tolist()==['a']


def test_empty_match_is_not_fabricated_and_duplicate_or_crossweek_fails():
    a,c=fixture()
    assert len(matched_sets(a,c.iloc[:0])[1])==0
    with pytest.raises(ValueError):matched_sets(a,pd.concat([c,c]))
    c['decision_time']=pd.Timestamp('2026-09-01T12:00Z')
    with pytest.raises(ValueError):matched_sets(a,c)

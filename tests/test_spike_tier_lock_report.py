"""Triple-arm denominator and frozen-risk validation without market history."""
import pandas as pd
import pytest
from yoyo.evaluation.spike_tier_lock_report import triples, normalize, compare_pair, scoped


def _rows():
    rows=[]
    for rule in ('baseline','be05','tier'):
        for key in ('a','b'):
            rows.append(dict(system='v8',mode='fixed',rule=rule,event_key=key,venue='x',symbol='X',
                timeframe_min=30,side=1,entry_time='2025-09-09T00:00:00Z',exit_time='2025-09-09T02:00:00Z',
                entry_price=100.,initial_stop=98.,initial_risk=2.,exit_price=100.,net_r=-.1,net_return=-.002,
                censored=key=='b' and rule=='baseline'))
    return normalize(pd.DataFrame(rows))


def test_newly_closed_trade_does_not_enter_three_way_primary():
    f=triples(_rows())
    assert f.all_closed.tolist()==[True,False]
    assert compare_pair(f,'baseline','tier')['joint_closed']==1
    assert compare_pair(f,'be05','tier')['joint_closed']==2


def test_changed_initial_risk_cannot_masquerade_as_exit_improvement():
    f=_rows();f.loc[f.rule=='tier','initial_risk']=1.
    with pytest.raises(ValueError,match='economic identity'):
        triples(f)


def test_cross_cut_exit_is_excluded_from_earlier_comparison():
    f=triples(_rows());f.loc[:,'exit_time_tier']=pd.Timestamp('2025-09-11',tz='UTC')
    slices=list(scoped(f,('exit_time_baseline','exit_time_be05','exit_time_tier')))
    earlier=[part for scope,part in slices if scope==dict(period='earlier',timeframe_min='all',side_group='all')]
    assert len(earlier)==1 and earlier[0].empty

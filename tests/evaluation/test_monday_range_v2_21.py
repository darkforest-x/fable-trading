"""Regression fixtures for the owner's Oct20 alternative-level counterexample."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.monday_range_v2_21_reference import select_matches


def case():
    # Actual OKX 15m candles, Oct20 21:15..23:00 Shanghai, close-time index.
    values=[
        [81378,81500,81358.6,81380.9],
        [81380.8,81743.2,81273,81613.1],
        [81613.2,82149,81605.1,82124.3],
        [82124.3,83218.5,82066.5,83202.7],
        [83202.7,83333,82801.4,82949.9],
        [82949.9,83298.2,82860,83086.4],
        [83086.4,83436.9,82950.2,83231.6],
        [83231.6,83273.6,82152.5,82382.3],
    ]
    q=pd.DataFrame(values,columns=['open','high','low','close'],
        index=pd.date_range('2026-10-20T21:30+08:00',periods=8,freq='15min'))
    arrows=np.array([0,0,0,0,0,-1,0,0])
    grid=np.tile([81166.,83149.8,83407.694],(len(q),1))
    names=['0:up','0:down','1.13:up']; weeks=np.array(['2026-10-19']*len(q))
    return q,arrows,grid,names,weeks


def test_previous_body_rejects_monday_high_but_preserves_later_fib():
    result=select_matches(*case(),before=5,after=0)
    assert len(result)==1
    r=result[0]
    assert (r['touch_i'],r['entry_i'],r['key'])==(6,6,'1.13:up')
    assert r['entry']==83231.6 and r['stop']==83603.4


def test_post_confirmation_uses_later_close_and_never_retroactive_entry():
    r=select_matches(*case(),before=5,after=2)[0]
    assert (r['touch_i'],r['entry_i'],r['key'])==(6,7,'1.13:up')
    assert r['entry']==82382.3 and r['stop']==83601.7


@pytest.mark.parametrize('after',[0,1,2])
def test_final_decision_is_stable_for_every_available_prefix(after):
    q,a,p,n,w=case(); full=select_matches(q,a,p,n,w,before=5,after=after)
    for end in range(1,len(q)+1):
        got=select_matches(q.iloc[:end],a[:end],p[:end],n,w[:end],before=5,after=after)
        assert got==[r for r in full if r['entry_i']<end]


def test_disabling_new_windows_restores_original_first_match():
    r=select_matches(*case(),before=0,after=0)[0]
    assert (r['touch_i'],r['entry_i'],r['key'])==(5,5,'0:down')


def test_body_boundary_equality_invalidates_candidate():
    q,a,p,n,w=case()
    q.iloc[0,q.columns.get_loc('close')]=83407.694
    assert select_matches(q,a,p,n,w,before=5,after=2)==[]


def test_removed_configured_fib_is_not_silently_reintroduced():
    q,a,p,n,w=case()
    assert select_matches(q,a,p[:,:2],n[:2],w,before=5,after=2)==[]


def test_monday_low_retest_allows_entry_below_boundary():
    q,a,p,n,w=case()
    q.loc[:,:]=[99,101,98,99.5]
    p[:,:]=[100,110,111.3]
    r=select_matches(q,a,p,n,w,before=5,after=2)[0]
    assert r['key']=='0:up' and r['entry']<100


def test_short_extension_below_monday_low_is_rejected():
    q,a,p,n,w=case()
    q.loc[:,:]=[97,99,96,97.5]
    p[:,:]=[100,110,98]
    assert select_matches(q,a,p,['0:up','0:down','1.13:down'],w,before=5,after=2)==[]

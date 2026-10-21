"""Midpoint-side eligibility at candidate time, with causal matching preserved."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.monday_range_v2_31_reference import select_matches


def case(side=-1, level=105., touch=5):
    body = level - 1.5 if side == -1 else level + 1.
    q = pd.DataFrame([[body, body+.8, body-.3, body+.5]]*9,
                     columns=['open','high','low','close'],
                     index=pd.date_range('2026-10-20T21:30+08:00', periods=9, freq='15min'))
    q.iloc[touch, q.columns.get_loc('high' if side == -1 else 'low')] = level-side
    a = np.zeros(9, dtype=int); a[5] = side
    p = np.tile([100.,110.,level], (9,1))
    return q,a,p,['0:up','0:down','test'],np.array(['2026-10-19']*9)


@pytest.mark.parametrize('side,level,allowed', [(-1,104.,False),(-1,105.,True),(-1,106.,True),
                                               (1,104.,True),(1,105.,True),(1,106.,False)])
@pytest.mark.parametrize('touch', [3,4,5,6,7])
def test_midpoint_is_inclusive_at_every_touch_offset(side,level,allowed,touch):
    rows=select_matches(*case(side,level,touch),buffer=0)
    assert bool(rows) == allowed
    if allowed:
        assert rows[0]['entry_i'] == max(5,touch)
        assert rows[0]['level'] == level


@pytest.mark.parametrize('side,level,allowed', [(-1,103.82,False),(-1,106.18,True),
                                               (1,103.82,True),(1,106.18,False)])
def test_fib_label_does_not_replace_price_side(side,level,allowed):
    q,a,p,n,w=case(side,level)
    n[2]='0.618:down' if level<105 else '0.618:up'
    assert bool(select_matches(q,a,p,n,w)) == allowed


def test_rejected_touch_does_not_consume_arrow_before_later_valid_touch():
    q,a,p,n,w=case(-1,104.,5)
    p=np.tile([100.,110.,104.,105.],(9,1)); n+=['0.5:up']
    q.iloc[6,q.columns.get_loc('high')]=106.
    row=select_matches(q,a,p,n,w)[0]
    assert (row['touch_i'],row['entry_i'],row['level']) == (6,6,105.)


def test_rejected_first_key_does_not_hide_valid_key_on_same_touch():
    q,a,p,n,w=case(-1,104.,5)
    q.iloc[5,q.columns.get_loc('high')]=106.
    p=np.tile([100.,110.,104.,105.],(9,1)); n+=['0.5:up']
    assert select_matches(q,a,p,n,w)[0]['level'] == 105.


def test_earlier_touch_uses_its_own_midpoint_even_if_range_later_changes():
    q,a,p,n,w=case(-1,105.,3)
    p[4:,1]=120.
    assert select_matches(q,a,p,n,w)[0]['touch_i'] == 3


@pytest.mark.parametrize('body_i', [0,4,6])
def test_previous_and_pairing_body_touch_still_rejects(body_i):
    q,a,p,n,w=case(-1,105.,7)
    q.iloc[body_i]=[103.5,106.,103.,105.]
    assert select_matches(q,a,p,n,w) == []


@pytest.mark.parametrize('touch', [2,8])
def test_outside_window_remains_invalid(touch):
    assert select_matches(*case(touch=touch)) == []


@pytest.mark.parametrize('side', [-1,1])
@pytest.mark.parametrize('touch', [3,4,5,6,7])
def test_every_prefix_preserves_admissions(side,touch):
    q,a,p,n,w=case(side,105.,touch)
    full=select_matches(q,a,p,n,w)
    for end in range(1,len(q)+1):
        assert select_matches(q.iloc[:end],a[:end],p[:end],n,w[:end]) == [r for r in full if r['entry_i']<end]

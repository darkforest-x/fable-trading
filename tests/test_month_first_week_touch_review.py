import pandas as pd
import pytest
from yoyo.evaluation.month_first_week_touch_review import bounds,census,contiguous,reaction,HOUR


def frame(start,end,high=110.,low=100.):
    i=pd.date_range(start,end,freq='h',inclusive='left')
    return pd.DataFrame(dict(open=105.,high=high,low=low,close=105.,volume=1.),index=i)


def test_dst_calendar_and_first_monday():
    st,end,_=bounds('2024-03')
    assert str(st)=='2024-03-04 05:00:00+00:00'
    assert (end-st)/HOUR==167
    st,end,_=bounds('2027-11')
    assert (end-st)/HOUR==169
    assert bounds('2026-10')[0]==pd.Timestamp('2026-10-05T04:00Z')


def test_anchor_must_be_complete_and_counts_begin_after_ready():
    st,ready,expiry=bounds('2026-10')
    f=frame(st,expiry)
    f.loc[ready-HOUR,'high']=150. # Final source-week high known only at ready.
    f.loc[ready,'high']=181. # upper = 180.9
    f.loc[ready+HOUR,'high']=200. # repeated same-side touch is not another event
    f.loc[expiry-HOUR,'low']=69. # lower = 69.1
    m,e=census({'BTC':f},{'asof_exclusive':str(expiry)})
    assert len(e)==2
    assert set(e.side)=={'upper','lower'}
    assert e.iloc[0].touch==str(ready)
    assert e.iloc[0].level==pytest.approx(180.9)
    assert m.iloc[0].complete_month
    m,e=census({'BTC':f.drop(st)},{'asof_exclusive':str(expiry)})
    assert e.empty
    assert m.iloc[0].status == 'missing_anchor'


def test_reaction_omits_touch_hour_and_censors_missing_hours():
    t=pd.Timestamp('2026-10-15T00:00Z');f=frame(t,t+25*HOUR)
    f.loc[t,['high','low']]=[180.,10.] # Cannot order these intrabar extremes.
    r=reaction(f,t,100.,'lower',24)
    assert r['inward_pct']==pytest.approx(10.)
    assert r['outward_pct']==0
    assert not reaction(f.drop(t+2*HOUR),t,100.,'lower',24)['complete']


def test_expired_level_cannot_create_event():
    st,ready,expiry=bounds('2026-10');f=frame(st,expiry+24*HOUR)
    f.loc[expiry,'high']=200.
    m,e=census({'BTC':f},{'asof_exclusive':str(expiry+24*HOUR)})
    assert e.empty
    assert not m.iloc[0].upper_hit

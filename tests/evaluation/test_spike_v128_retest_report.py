"""Regression cases for changing delayed-return denominators and temporal censoring."""
import pandas as pd
from yoyo.evaluation.spike_v128_retest_report import tail_counts, cohort


def test_tail_requires_realized_high_r_not_just_reentry():
    base=pd.DataFrame({'trade_key':['a','b','c'],'net_r':[6.,7.,8.]})
    treated=pd.DataFrame({'trade_key':['a','b','new'],'net_r':[-1.,6.,9.]})
    r=tail_counts(base,treated,5)
    assert r['baseline_high_r']==3 and r['reentered']==2 and r['still_high_r']==1
    assert r['reentered_lost_high_r']==1 and r['not_reentered']==1 and r['new_high_r']==1
    assert r['still_high_r']+r['new_high_r']==r['treated_high_r']


def test_earlier_request_must_be_resolved_before_split():
    df=pd.DataFrame({'anchor_close':pd.to_datetime(['2026-08-22']*2+['2026-08-24'],utc=True),
                     'decision_time':pd.to_datetime(['2026-08-22','2026-08-24','2026-08-25'],utc=True),
                     'exit_time':pd.to_datetime([None,None,'2026-08-26'],utc=True)})
    split=pd.Timestamp('2026-08-23',tz='UTC')
    assert cohort(df,'earlier',split).index.tolist()==[0]
    assert cohort(df,'cross_split',split).index.tolist()==[1]
    assert cohort(df,'later',split).index.tolist()==[2]

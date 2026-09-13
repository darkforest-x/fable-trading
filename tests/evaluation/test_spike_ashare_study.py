"""Synthetic matching and summary checks for the fixed four-cell study."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.spike_ashare_study import metrics, paired_test, random_mask


def test_control_matches_counts_inside_quarter_and_volatility_bucket():
    frame=pd.DataFrame(dict(decision_date=pd.bdate_range('2024-01-01',periods=100).strftime('%Y-%m-%d'),
                            in_window=True,ready_v1=True,long_v1=False,vol_bucket=np.arange(100)%4))
    frame.loc[[2,5,16,71,83],'long_v1']=True
    a,missing=random_mask(frame,'v1','fixed')
    b,_=random_mask(frame,'v1','fixed')
    pd.testing.assert_series_equal(a,b)
    assert not (a & frame.long_v1).any() and missing==0
    quarter=pd.to_datetime(frame.decision_date).dt.to_period('Q')
    actual=frame.loc[frame.long_v1].groupby([quarter[frame.long_v1],frame.loc[frame.long_v1,'vol_bucket']]).size()
    control=frame.loc[a].groupby([quarter[a],frame.loc[a,'vol_bucket']]).size()
    pd.testing.assert_series_equal(actual,control)


def test_unavailable_matching_is_counted_not_filled_from_other_buckets():
    f=pd.DataFrame(dict(decision_date=['2024-01-01','2024-01-02'],in_window=True,
                        ready_v1=True,long_v1=[True,False],vol_bucket=[0,1]))
    mask,missing=random_mask(f,'v1','x')
    assert missing==1 and not mask.any()


def test_open_marks_cannot_inflate_realized_metrics():
    frame=pd.DataFrame(dict(censored=[False,False,True],net_r=[1,-1,100],
                            net_return=[.1,-.1,10],gross_return=[.102,-.098,10.002]))
    m=metrics(frame)
    assert m['closed_trades']==2 and m['open_trades']==1
    assert m['mean_net_return']==0 and m['sum_net_r']==0
    assert m['win_rate']==.5 and m['profit_factor']==1


def test_cluster_pvalue_requires_both_arms_and_preserves_null():
    a=pd.DataFrame(dict(code=['a','b'],censored=False,net_return=[.1,.1]))
    assert paired_test(a,a)==(1.,2)
    assert paired_test(a,a.iloc[:0])==(None,0)
    b=a.copy();b.net_return=-.1
    p,n=paired_test(a,b)
    assert n==2 and .2<p<.3  # only two independent clusters

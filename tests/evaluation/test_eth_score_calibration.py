"""Temporal calibration, frozen economic gates and shrinkage semantics."""
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_score_calibration_data import clocks,bias_delta,rule_mask,rules


def test_calibration_purges_whole_week_crossing_test_boundary():
    d=pd.DataFrame(dict(week=['2025-03-24','2025-03-31','2025-04-07'],
        week_start=pd.to_datetime(['2025-03-24T04:00Z','2025-03-31T04:00Z','2025-04-07T04:00Z']),
        expiry_dt=pd.to_datetime(['2025-03-31T04:00Z','2025-04-07T04:00Z','2025-04-14T04:00Z']),complete=True))
    cal,test=clocks(d,pd.Timestamp('2025-04-01',tz='America/New_York'))
    assert list(cal.week)==['2025-03-24'] and list(test.week)==['2025-04-07']


def test_intercept_bias_weights_weeks_not_number_of_repeated_touches():
    d=pd.DataFrame(dict(week=['a']*10+['b'],score=0.,net_r=[1.]*10+[-1.]))
    assert bias_delta(d,4.)==0.
    d.loc[d.week.eq('b'),'net_r']=2.
    assert bias_delta(d,4.)==.5


def test_rules_ignore_labels_and_zero_gate_is_strict():
    d=pd.DataFrame(dict(score=[-.5,0.,.5,1.],net_r=[100.,100.,-100.,-100.]))
    original=rules(d);d.net_r*=-1
    assert original==rules(d)
    np.testing.assert_array_equal(rule_mask(d,original[1]),[False,False,True,True])

"""Mature labels and rule selection must be measurable at the month boundary."""
import json
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_10r_adaptive as a
from yoyo.evaluation import spike_10r_search as s


def test_month_history_waits_for_exit_bar_close_and_drops_crossing():
    c=pd.DataFrame(dict(available_at=pd.to_datetime(['2025-04-01']*4,utc=True),
        exit_time=pd.to_datetime(['2025-06-30T23:00Z','2025-06-30T23:30Z','2025-07-01T00:00Z',None],utc=True),
        timeframe_min=[60]*4,valid_entry=[True]*4,censored=[False,False,False,True]))
    assert a.monthly_history(c,pd.Timestamp('2025-07-01',tz='UTC')).tolist()==[True,False,False,False]


def fixture():
    time=pd.date_range('2024-10-01',periods=730,freq='12h',tz='UTC')
    c=pd.DataFrame(dict(available_at=time,entry_time=time,exit_time=time+pd.Timedelta(hours=1),
        timeframe_min=60,valid_entry=True,censored=False,net_r=np.where(np.arange(730)%7==0,12.,-1.),
        asset=[f'A{i%40}' for i in range(730)],net_return=.01))
    m=np.zeros((730,120),bool)
    m[:,0]=True
    m[:,1]=np.arange(730)%2==0
    return c,m


def cfg():
    return dict(selection_min_closed=5,selection_min_gt10=1,selection_min_gt10_recall=.01,
        selection_min_assets=1,selection_min_active_months=1,selection_min_positive_asset_months=1,
        coverage_floors=[.01])


def test_target_month_future_and_unresolved_outcomes_never_choose_rule():
    c,m=fixture()
    month=pd.Timestamp('2025-07-01',tz='UTC')
    before=a.choose_month(c,m,month,cfg())
    changed=c.copy()
    changed.loc[~a.monthly_history(c,month),'net_r']=1e6
    assert a.choose_month(changed,m,month,cfg())==before
    assert pd.Timestamp(before['max_label_available_at'])<=month


def test_objective_matches_fixed_selection_on_same_mature_rows():
    c,m=fixture()
    history=s.early_mask(c).to_numpy()
    _,selected=s.search_earlier(c,m,cfg())
    got=a.rank_history(c.loc[history],m[history],cfg())
    assert got['rule']==selected[0]['choice']['rule']
    assert got['terms']==selected[0]['choice']['terms']
    assert got['precision']==selected[0]['choice']['precision']

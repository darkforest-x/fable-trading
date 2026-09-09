"""Frozen summaries keep calendar labels and future marks out of decisions."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.altseason_research import add_regime,holm,opportunity_audit,rank_auc,scope_rows


def test_regime_uses_only_completed_day_even_when_future_changes():
    e=pd.DataFrame({'decision_time':pd.to_datetime(['2026-08-01T12:00Z'])})
    r=pd.DataFrame({'time':pd.to_datetime(['2026-08-01T00:00Z','2026-08-02T00:00Z']),
                    'breadth':[.2,.9],'md_positive':[.3,.9],'n_assets':[100,100]})
    assert add_regime(e,r).regime_breadth.iloc[0]==.2
    r.loc[1,'breadth']=0.
    assert add_regime(e,r).regime_breadth.iloc[0]==.2


def test_holm_and_auc_ties():
    assert holm([.01,.03,np.nan]).tolist()[:2]==[.02,.03]
    assert rank_auc([1,1,1,1],[0,1,0,1])==.5
    assert rank_auc([1,2,3,4],[0,0,1,1])==1.


def test_unknown_peak_bar_exit_not_claimed_to_capture_peak():
    t=pd.Timestamp('2026-08-01T12:00Z')
    c=pd.DataFrame([dict(venue='gate',symbol='A',asset='A',instrument='gate:A',kind='day',window_start=t-pd.Timedelta(hours=12),
        window_end=t+pd.Timedelta(hours=12),peak_time=t,peak_return=1.,close_return=.2)])
    e=pd.DataFrame([dict(instrument='gate:A',valid=True,minutes=60,arm='focus_sma60',entry_time=t-pd.Timedelta(hours=2),
        exit_time=t+pd.Timedelta(hours=1),exit_time_lower=t,exit_time_upper=t+pd.Timedelta(hours=1),event_id='1')])
    got=opportunity_audit(c,e)
    assert got.loc[got.arm.eq('focus_sma60'),'result'].iloc[0]=='peak_bar_order_unknown'


def test_scaled_symbol_not_silently_merged_in_combined_account():
    f=pd.DataFrame({'venue':['binance','gate'],'asset':['1000PEPE','PEPE']})
    assert len(scope_rows(f,'binance'))==1
    assert scope_rows(f,'combined').asset.tolist()==['PEPE']

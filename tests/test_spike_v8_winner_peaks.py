"""Peak diagnostics must not mistake post-exit extrema for held exposure."""
import pandas as pd
import pytest
from yoyo.evaluation.spike_v8_winner_peaks import profile_trade,threshold_table


def fixture(open_exit=False):
    frame=pd.DataFrame({'open':[100.,108.,101.,100.], 'high':[112.,116.,150.,900.],
        'low':[99.,105.,90.,1.], 'close':[108.,110.,120.,500.], 'atr':[5.,5.,5.,5.]},
        index=pd.date_range('2024-01-01',periods=4,freq='15min',tz='UTC'))
    row=dict(signal_i=-1,entry_i=0,exit_i=2,entry_time=str(frame.index[0]),exit_time=str(frame.index[2]),
        side=1,entry_price=100.,initial_stop=90.,initial_risk=10.,exit_price=101.,exit_reason='opposite_v6_next_open' if open_exit else 'initial_stop',
        exit_at_open=open_exit,gross_r=.1,net_r=.08,cost_r=.02,mfe_r=1.6,final_protection=90.,censored=False)
    if not open_exit:row.update(exit_price=90.,gross_r=-1.,net_r=-1.02)
    return frame,row


def test_stop_bar_only_changes_possible_upper_bound():
    frame,row=fixture();result,trace=profile_trade(frame,row)
    assert result['peak_known_gross_r']==pytest.approx(1.6)
    assert result['peak_possible_upper_r']==pytest.approx(5.)
    assert len(trace)==2 and result['giveback_r']==pytest.approx(2.6)
    frame.loc[frame.index[2],'high']=900.
    modified,_=profile_trade(frame,row)
    assert modified['peak_known_gross_r']==result['peak_known_gross_r']


def test_open_exit_excludes_whole_exit_bar_and_all_later_bars():
    frame,row=fixture(True);before,trace=profile_trade(frame,row)
    frame.loc[frame.index[2],['high','low','close','atr']]=100000.
    frame.iloc[3:]=100000.
    after,after_trace=profile_trade(frame,row)
    assert before==after and trace==after_trace
    assert before['peak_possible_upper_r']==before['peak_known_gross_r']


def test_actual_exit_fill_can_exceed_legacy_mfe():
    frame,row=fixture(True);row.update(exit_price=120.,gross_r=2.,net_r=1.98)
    frame.loc[frame.index[2],'open']=120.
    result,_=profile_trade(frame,row)
    assert result['peak_known_gross_r']==2 and result['mfe_r']==1.6
    assert result['giveback_r']==0 and not result['peak_is_intrabar']


def test_unknown_exit_bar_does_not_count_as_confirmed_threshold_hit():
    frame,row=fixture();result,_=profile_trade(frame,row)
    df=pd.DataFrame([result]);rows=threshold_table(df,'synthetic')
    r=next(r for r in rows if r['basis']=='gross' and r['level']==3)
    assert r['reached']==0 and r['possible_only']==1

"""Behavioral examples for causal close activation and native stop ordering."""
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_wick_r_engine import execute


def run(bars,side=1,stop=.05,target=2):
    f=pd.DataFrame(bars,columns=['open','high','low','close'],index=pd.date_range('2026-01-01',periods=len(bars),freq='5min',tz='UTC'))
    r=100-bars[0][2] if side==1 else bars[0][1]-100
    e=pd.DataFrame([dict(entry_i=0,touch_end_i=0,confirm_i=1,end_i=len(f)-1,entry=100.,side=side,wick_r=r)])
    return execute(f,e,[stop],[target])[0].iloc[0]


def test_target_does_not_exist_before_touch_close():
    r=run([[101,110,99,100.5],[100.5,101,99.8,99.9],[99.9,100,99,99.5]],target=2)
    assert r.reason==11 and r.exit_i==1


def test_target_already_passed_at_activation_is_taker_close():
    r=run([[101,105,99,104],[104,106,103,105]],target=2)
    assert r.reason==14 and r.exit_price==104 and r.fee_exit_rate==.0005


def test_initial_stop_uses_percentage_not_final_wick():
    r=run([[101,102,90,101],[101,102,100.5,101]],stop=.02)
    assert r.reason==12 and r.exit_price==98 and r.exit_i==0


def test_fee_breakeven_neutral_for_both_sides():
    long=run([[101,101,99,100.5],[100.5,101,100.1,100.8],[100.8,101,100,100.5]])
    short=run([[99,101,99,99.5],[99.5,99.9,99,99.2],[99.2,100,99,99.5]],side=-1)
    for r in [long,short]:
        assert r.reason==5 and r.confirmed and r.fee_be_armed
        assert abs(r.net_bp)<1e-8 and abs(r.net_after_slip_bp+10)<1e-8


def test_fee_stop_cannot_activate_before_close_covers_fees():
    r=run([[101,101,99,100.2],[100.2,101,100.01,100.02],[100.02,101,99.9,100.5]])
    assert r.reason==6 and not r.fee_be_armed and abs(r.net_bp+7)<1e-8


def test_native_conservative_ambiguous_bar():
    r=run([[101,101,99,100.5],[100.5,103,97,101]],stop=.02,target=2)
    assert r.reason==12 and r.ambiguous and r.exit_price==98


def test_open_target_precedes_later_stop():
    r=run([[101,101,99,100.5],[103,104,97,101]],stop=.02,target=2)
    assert r.reason==4 and not r.ambiguous and r.exit_price==102


def test_gap_stop_receives_worse_open():
    r=run([[101,101,99,100.5],[97,101,96,100]],stop=.02,target=2)
    assert r.reason==12 and r.exit_price==97


def test_censored_mark_is_not_closed_profit():
    r=run([[101,101,99,100.5],[100.5,101,100.2,100.9],[101,101.5,100.8,101.2]],target=10)
    assert r.reason==0 and not r.closed and pd.isna(r.net_bp)
    assert np.isfinite(r.censored_mark_bp)


def test_broadcast_columns_match_individual_framework_calls():
    f=pd.DataFrame([[101,101,99,100.5],[100.5,103,97,101]],columns=['open','high','low','close'],index=pd.date_range('2026-01-01',periods=2,freq='5min',tz='UTC'))
    e=pd.DataFrame([dict(entry_i=0,touch_end_i=0,confirm_i=1,end_i=1,entry=100.,side=1,wick_r=1.)])
    batch,_=execute(f,e,[.02,.05],[1,2,3])
    for r in batch.itertuples():
        single,_=execute(f,e,[r.stop_pct/100],[r.target_r])
        assert single.iloc[0].reason==r.reason
        assert abs(single.iloc[0].net_bp-r.net_bp)<1e-8


def test_prepare_preserves_ledger_and_censors_gaps():
    from yoyo.evaluation.monday_wick_r_study import prepare
    f=pd.DataFrame([[101,101,99,100.5],[101,102,100.5,101]],columns=['open','high','low','close'],
                   index=pd.DatetimeIndex(['2026-01-01T00:00Z','2026-01-01T00:10Z']))
    ledger=pd.DataFrame([dict(trade_id='a',event_id='a',reclaim_minutes=5,exit_mode='near_monday_edge',
        entry_i=0,end_i=1,side=1,entry=100.,population='signal',net_bp=0.,reason=0,exit_i=1,closed=False)])
    e,a=prepare(f,ledger,dict(symbol='SYNTH',execution_minutes=5))
    assert ledger.end_i.iloc[0]==1 and e.end_i.iloc[0]==0
    assert e.confirm_i.iloc[0]>e.end_i.iloc[0]

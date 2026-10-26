"""Native absolute-target and risk-budget sizing behavior, synthetic bars only."""
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_confirmed_keypoints_engine import execute,planned_loss,TARGETS
from yoyo.evaluation.monday_confirmed_keypoints_study import target_matrix
from yoyo.evaluation.monday_confirmed_r_engine import execute as old_execute


def frame(rows):
    return pd.DataFrame(rows,columns=['open','high','low','close'],index=pd.date_range('2026-01-06',periods=len(rows),freq='5min',tz='UTC'))


def events(f,entry=100.,stop=99.,side=1):
    return pd.DataFrame([dict(entry_i=0,end_i=len(f)-1,entry=entry,entry_quote=entry,stop=stop,risk=side*(entry-stop),risk_pct=100*side*(entry-stop)/entry,side=side,entry_ns=f.index[0].value)])


def test_planned_loss_limits_ordinary_stop_including_both_fees_and_slippage():
    for side,stop,bars,targets in [(1,99,[[100,100.1,98,99]],[[101,102,103,104,105]]),(-1,101,[[100,102,99.9,101]],[[99,98,97,96,95]])]:
        f=frame(bars);t,fills=execute(f,events(f,stop=stop,side=side),np.array(targets,float))
        assert t.reason.eq(1).all()
        np.testing.assert_allclose(t.pnl_usdt,-100,atol=1e-7)
        np.testing.assert_allclose(t.budget_r,-1,atol=1e-9)
        assert len(fills)==20


def test_gap_may_exceed_planned_loss():
    f=frame([[100,100.1,99.5,100],[97,100,96,98]])
    t,_=execute(f,events(f),np.array([[101,102,103,104,105.]]))
    assert t.reason.eq(1).all() and t.budget_r.lt(-1).all()


def test_already_passed_and_fee_insufficient_targets_have_no_orders():
    f=frame([[100,102,99.5,101]])
    t,fills=execute(f,events(f),np.array([[99,100,100.01,101,102.]]))
    assert (~t.entered.iloc[:6]).all() and t.pnl_usdt.iloc[:6].eq(0).all()
    assert t.skip_reason.iloc[4]=='insufficient_fee_space'
    assert len(fills)==8 and fills.trade_row.min()==6


def test_absolute_targets_match_existing_native_R_targets():
    f=frame([[100,100.5,99.8,100.3],[100.3,108,100.05,107]])
    ev=events(f);new,_=execute(f,ev,np.array([[101,102,103,105,107.]]))
    old,_=old_execute(f,ev,[1,2,3,5,7])
    np.testing.assert_allclose(new.net_bp,old.net_bp,atol=1e-7)
    np.testing.assert_array_equal(new.reason,old.reason)
    np.testing.assert_array_equal(new.exit_i,old.exit_i)


def test_budget_changes_quantity_not_percentage_return():
    f=frame([[100,100.5,99.5,100],[100,106,99.5,105]])
    a,_=execute(f,events(f),np.array([[101,102,103,104,105.]]),budget=100)
    b,_=execute(f,events(f),np.array([[101,102,103,104,105.]]),budget=250)
    np.testing.assert_allclose(b.quantity,2.5*a.quantity)
    np.testing.assert_allclose(b.pnl_usdt,2.5*a.pnl_usdt,atol=1e-7)
    np.testing.assert_allclose(b.net_bp,a.net_bp,atol=1e-7)
    np.testing.assert_allclose(b.budget_r,a.budget_r,atol=1e-7)


def test_frozen_target_join_preserves_control_geometry():
    ev=pd.DataFrame([dict(event_id='x',population='signal',control_id=0),dict(event_id='x',population='random',control_id=1)])
    rows=[]
    for e,offset in zip(ev.to_dict('records'),[0,10]):
        rows.extend([dict(e,reclaim_minutes=5,exit_mode=name,target_price=101+i+offset) for i,name in enumerate(TARGETS)])
    result=target_matrix(ev,pd.DataFrame(rows[::-1]))
    np.testing.assert_array_equal(result,[[101,102,103,104,105],[111,112,113,114,115]])


def test_censored_budget_marks_never_become_realized_profit():
    f=frame([[100,100.01,99.5,100]])
    t,_=execute(f,events(f),np.array([[101,102,103,104,105.]]))
    assert t.entered.all() and not t.resolved.any() and t.budget_r.isna().all()
    assert t.boundary_mark_budget_r.notna().all()


def test_native_same_bar_stop_priority_and_delayed_BE():
    f=frame([[100,103,98,102]])
    t,_=execute(f,events(f),np.array([[101,102,103,104,105.]]))
    assert t.reason.eq(1).all() and not t.be_armed.any()
    assert t.ambiguous.iloc[:6].all()

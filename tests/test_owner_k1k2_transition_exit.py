"""Synthetic paired accounting; preserve unknowns and the original denominator."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.owner_k1k2_transition_exit import comparison, matched_comparison, ledger, changes


def trades(ids=("a",), returns=(.001,)):
    t = pd.Timestamp("2023-01-02T00:00:00Z")
    return pd.DataFrame([dict(event_id=i,decision_time=t,fold="2023H1",direction=1,
        initial_stop=90.,signal_atr=2.,entry_price=100.,entry_time=t,exit_time=t+pd.Timedelta(minutes=5),
        closed=True,outcome="colour_exit",hold_minutes=5.,net_return=v,gross_return=v+.002)
        for i,v in zip(ids,returns)])


def test_same_fills_cancel_cost_in_exit_comparison():
    a=trades(); b=trades(returns=(.004,))
    d=comparison(a,b)
    assert d.delta_net.iloc[0] == pytest.approx(.003)
    assert d.delta_saved_cost.iloc[0] == pytest.approx(0)
    assert changes(d)["improved"] == 1


@pytest.mark.parametrize("arm",["state","transition"])
@pytest.mark.parametrize("reason",["entry_missing","entry_invalid","data_gap_censored"])
def test_unknown_path_is_not_cash_or_silently_omitted(arm,reason):
    a,b=trades(),trades()
    f=a if arm=="state" else b
    f.loc[0,["closed","outcome","net_return","gross_return"]]=[False,reason,np.nan,np.nan]
    d=comparison(a,b)
    assert len(d)==1 and not d.known.iloc[0]
    assert np.isnan(d.delta_net.iloc[0])
    assert changes(d)["unknown"] == 1


def test_observed_invalid_risk_is_known_no_fill():
    a,b=trades(),trades()
    for f in [a,b]: f.loc[0,["closed","outcome","net_return","gross_return"]]=[False,"entry_invalid_risk",np.nan,np.nan]
    d=comparison(a,b)
    assert d.known.iloc[0] and d.delta_net.iloc[0]==0


@pytest.mark.parametrize("col,val",[("initial_stop",91.),("direction",-1),("entry_price",101.),("fold","2023H2")])
def test_no_other_entry_dimension_may_drift(col,val):
    a,b=trades(),trades(); b.loc[0,col]=val
    with pytest.raises(AssertionError): comparison(a,b)


def test_missing_or_duplicate_event_rejected():
    a=trades()
    with pytest.raises(ValueError): comparison(a,trades(ids=("b",)))
    with pytest.raises(pd.errors.MergeError): comparison(a,pd.concat([a,a]))


def paired_fixture():
    c=comparison(trades(),trades(returns=(.004,)))
    r=comparison(trades(("r1","r2","r3"),(.001,.001,.001)),trades(("r1","r2","r3"),(.002,.002,.002)))
    assign=pd.DataFrame([dict(event_id="a",match_status="matched")])
    req=pd.DataFrame(dict(event_id=["r1","r2","r3"],parent_event_id="a"))
    return c,r,assign,req


def test_original_three_controls_define_incremental_excess():
    c,r,a,q=paired_fixture(); p=matched_comparison(c,r,a,q)
    assert p.controls.iloc[0]==3
    assert p.delta_excess_net.iloc[0] == pytest.approx(.002)
    assert p.delta_excess_saved_cost.iloc[0] == pytest.approx(0)


def test_unknown_control_invalidates_but_preserves_whole_pair():
    c,r,a,q=paired_fixture(); r.loc[1,"known"]=False
    r.loc[1,"transition_net"]=np.nan
    p=matched_comparison(c,r,a,q)
    assert len(p)==1 and p.controls.iloc[0]==3 and not p.complete_pair.iloc[0]
    assert p.filter(regex="excess").isna().all().all()


def test_two_controls_cannot_be_reweighted_as_three():
    c,r,a,q=paired_fixture()
    with pytest.raises(ValueError): matched_comparison(c,r.iloc[:2],a,q.iloc[:2])


def test_unknown_entry_blocks_later_ledger_position_conservatively():
    f=trades(("a","b"),(.001,.001))
    f.loc[0,["closed","outcome","exit_time"]]=[False,"entry_missing",pd.NaT]
    l=ledger(f)
    assert l.portfolio_selected.tolist()==[True,False]
    assert l.outcome.iloc[0]=="entry_missing"

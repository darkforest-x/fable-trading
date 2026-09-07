"""Synthetic causal-window, strict-gate and original-unit accounting contracts."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.owner_k1k2_genuine_flow import (
    contribution, gate_events, load_month, month_inference, paired_contrasts, windows_for,
)


def events():
    end = pd.Timestamp("2023-01-02T03:00Z")
    return pd.DataFrame([dict(event_id="case", decision_time=end, direction=1, gap_bars=2,
        k1_decision_time=end-pd.Timedelta(hours=2), fold="2023H1")])


def flow(total=100, delta=20):
    start = pd.Timestamp("2023-01-02T01:00Z")
    t = pd.date_range(start, periods=25, freq="5min")
    return pd.DataFrame(dict(open_time=t,earliest_available_at=t+pd.Timedelta(minutes=5),
        quote_volume=total,taker_buy_quote_volume=(total+delta)/2,
        taker_sell_quote_volume=(total-delta)/2,delta_quote_volume=delta,trade_count=1 if total else 0))


@pytest.mark.parametrize("sign",[-1,1])
@pytest.mark.parametrize("amount",[0,1e-12,20,100])
def test_strict_directional_gate_and_future_bar_exclusion(sign,amount):
    e=events(); e.direction=sign
    f=flow(delta=amount)
    out,w=gate_events(f,e)
    assert bool(out.flow_pass.iloc[0]) == (sign*amount>0)
    assert w.available_bars.iloc[0]==24
    f.loc[24,"delta_quote_volume"]=np.nan
    same,_=gate_events(f,e)
    pd.testing.assert_frame_equal(out,same)


def test_volume_weighted_not_mean_ratios():
    f=flow(total=1,delta=-1)
    f.loc[0,["quote_volume","taker_buy_quote_volume","taker_sell_quote_volume","delta_quote_volume"]]=[100,100,0,100]
    out,_=gate_events(f,events())
    assert out.flow_pass.iloc[0]
    assert out.directional_imbalance.iloc[0]==pytest.approx(77/123)


@pytest.mark.parametrize("kind",["zero","missing","late"])
def test_unknown_or_zero_not_fabricated_activity(kind):
    f=flow()
    if kind=="zero": f=flow(0,0)
    if kind=="missing": f=f.iloc[1:]
    if kind=="late": f.loc[0,"earliest_available_at"]=pd.Timestamp("2023-01-02T03:05Z")
    # The shared aligner validates theoretical close boundaries separately.
    if kind=="late":
        with pytest.raises(ValueError): gate_events(f,events())
    else:
        out,_=gate_events(f,events())
        assert not out.flow_pass.iloc[0]
        assert not out.flow_defined.iloc[0]
        assert pd.isna(out.directional_imbalance.iloc[0])


def test_k1_chosen_before_flow_and_clock_agreement():
    e=events(); e.k1_decision_time-=pd.Timedelta(hours=1)
    with pytest.raises(ValueError,match="disagreement"): windows_for(e)


def test_replay_recomputes_own_gate_metadata_without_suffix_collision():
    first,w=gate_events(flow(),events())
    corrupted=first.copy(); corrupted.flow_defined=False; corrupted.directional_imbalance=-99
    again,aw=gate_events(flow(),corrupted)
    pd.testing.assert_frame_equal(first,again)
    pd.testing.assert_frame_equal(w,aw)


def trades():
    return events().assign(closed=True,outcome="colour_exit",gross_return=.001,net_return=-.001,
        flow_pass=False,flow_defined=True)


def test_gate_off_saves_entire_fee_not_fee_charged_to_cash():
    t=trades()
    assert contribution(t).iloc[0]==-.001
    assert contribution(t,True).iloc[0]==0
    assert contribution(t,True,True).iloc[0]==0
    net_change=contribution(t,True)-contribution(t)
    gross_change=contribution(t,True,True)-contribution(t,gross=True)
    assert (net_change-gross_change).iloc[0]==pytest.approx(.002)


def paired_fixture():
    case=trades()
    controls=pd.concat([case.assign(event_id="r"+str(i),parent_event_id="case",flow_pass=(i==0)) for i in range(3)],ignore_index=True)
    assignment=pd.DataFrame([dict(event_id="case",match_status="matched")])
    return case,controls,assignment


def test_original_three_controls_keep_gateoff_zero_and_cost_identity():
    c,r,a=paired_fixture()
    p=paired_contrasts(c,r,a).iloc[0]
    assert p.controls==3
    assert p.incremental_excess_net==pytest.approx(.001/3)
    assert p.incremental_excess_saved_cost==pytest.approx(.002/3)
    assert p.incremental_excess_gross==pytest.approx(-.001/3)


def test_missing_control_rejected_not_averaged_over_two():
    c,r,a=paired_fixture()
    with pytest.raises(ValueError,match="all3"): paired_contrasts(c,r.iloc[:2],a)


def test_gateoff_with_unknown_baseline_never_repairs_primary_pair():
    c,r,a=paired_fixture()
    r.loc[1,["closed","outcome","gross_return","net_return"]]=[False,"right_censored",np.nan,np.nan]
    assert contribution(r,True).iloc[1]==0
    p=paired_contrasts(c,r,a).iloc[0]
    assert not p.complete_pair and pd.isna(p.incremental_excess_net)


@pytest.mark.parametrize("status",["entry_missing","entry_invalid"])
def test_missing_or_unknown_entry_is_not_known_cash(status):
    c,r,a=paired_fixture()
    r.loc[1,["closed","outcome","gross_return","net_return"]]=[False,status,np.nan,np.nan]
    assert pd.isna(contribution(r).iloc[1])
    p=paired_contrasts(c,r,a).iloc[0]
    assert not p.complete_pair and pd.isna(p.incremental_excess_net)


def test_observed_open_invalid_risk_is_known_no_fill():
    t=trades().assign(closed=False,outcome="entry_invalid_risk",net_return=np.nan,gross_return=np.nan)
    assert contribution(t).iloc[0]==0


@pytest.mark.parametrize("scale",[.1,1,10])
def test_month_cluster_scale_equivariance_and_whole_calendar(scale):
    t=pd.date_range("2023-01-01",periods=24,freq="MS",tz="UTC")
    f=pd.DataFrame(dict(decision_time=t,x=np.tile([-.001,.002,.003],8)))
    a=month_inference(f,"x",draws=199)
    f.x*=scale
    b=month_inference(f,"x",draws=199)
    assert a["calendar_months"]==24
    assert b["mean_bp"]==pytest.approx(a["mean_bp"]*scale)
    np.testing.assert_allclose(b["ci95_bp"],np.array(a["ci95_bp"])*scale)
    assert b["p_one_sided"]==a["p_one_sided"]


def test_clock_preflight_refuses_out_of_scope_before_price_load(monkeypatch):
    calls=[]
    def fake(path,**kwargs):
        calls.append(kwargs)
        assert kwargs["usecols"]==["open_time"]
        return pd.DataFrame(dict(open_time=["2026-09-03T02:00Z"]))
    monkeypatch.setattr(pd,"read_csv",fake)
    with pytest.raises(ValueError,match="timestamp"): load_month("synthetic.csv",dict(month="2023-01"))
    assert len(calls)==1


def test_inference_unknown_pair_fails_closed():
    f=pd.DataFrame(dict(decision_time=["2023-01-01T00:00Z"],x=[np.nan]))
    assert month_inference(f,"x")["status"]=="unknown_pairs"

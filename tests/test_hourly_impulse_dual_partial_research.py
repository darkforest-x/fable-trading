"""V16 frozen switch, fast-source parity and full-path accounting fixtures."""
from copy import deepcopy
import inspect
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_dual_partial_research as r
from test_hourly_impulse_launch_research import base


def test_frozen_config_matches():
    c=json.loads((r.EXPERIMENT/"config.json").read_text())
    r.verify_config(c,base())
    a,b=deepcopy(c["policies"]); a.pop("id"); b.pop("id")
    assert b.pop("fast_partial_fraction")==.5 and a==b
    assert c["parent_results"].endswith("v15/results/candidate")
    assert len(r.INPUTS)==7 and len(r.CONTEXT_INPUTS)==2
    assert len(r.SOURCES)==len(set(r.SOURCES))


@pytest.mark.parametrize("key,value",[("fast_partial_fraction",1),("management_minutes",5),
    ("exit_mode","colour"),("ma_length",20),("confirmations",2),("decision_minutes",15),
    ("launch_deadline_minutes",60),("frozen_ma_exit",True)])
def test_policy_mutation_rejected(key,value):
    c=r.frozen_config(); c["policies"][1][key]=value
    with pytest.raises(ValueError): r.verify_config(c,base())


@pytest.mark.parametrize("change",["fee","horizon","stop","fold","gate","holdout","contract"])
def test_contract_mutation_rejected(change):
    c=r.frozen_config(); b=base()
    if change=="fee": b["execution"]["cost_fraction"]=.001
    elif change=="horizon": b["execution"]["max_hours"]=24
    elif change=="stop": b["execution"]["stop_first"]=False
    elif change=="fold": b["development_folds"][0][1]="2022-01-01"
    elif change=="gate": c["selection"]["matched_coverage"]=.6
    elif change=="holdout": c["holdout_consumed"]=True
    else: c["dual_partial_contract"]["strict_open_gross_above"]=.003
    with pytest.raises(ValueError): r.verify_config(c,b)


def ledger():
    t=pd.Timestamp("2024-01-01",tz="UTC")
    b=pd.DataFrame({"event_id":["a","b","c"],"mother_decision_time":[t]*3,
        "entry_time":[t]*3,"entry_price":[100]*3,"direction":[1]*3,"initial_stop":[99]*3,
        "signal_atr":[1]*3,"risk_pct":[.01]*3,"risk_atr":[1]*3,
        "exit_time":[t+pd.Timedelta(hours=1)]*3,"exit_price":[99,104,100],
        "outcome":["hard_stop","colour_exit","source_gap"],"hold_minutes":[60]*3,
        "closed":[True,True,False],"max_favourable_r":[1,5,0],"max_adverse_r":[1,0,0],
        "net_return":[-.012,.038,np.nan],"transition_initial_state":["aligned"]*3})
    a=b.copy(); a["net_return"]=[-.004,.021,np.nan]
    a["partial_fraction"]=[.5,.5,0]; a["partial_exit_time"]=[t+pd.Timedelta(minutes=10)]*2+[pd.NaT]
    a["realised_partial_gross_return"]=[.003,.003,0]; a["exit_remaining_fraction"]=[.5,.5,1]
    return b,a


def test_mechanics_keeps_unknown_and_hurt_winner():
    out,groups,info=r.partial_mechanics(*ledger())
    assert len(out)==3 and info["partial_count"]==2 and info["partial_hurt"]==1
    assert info["partial_improved"]==1 and info["same_exit_time"]==3
    assert pd.isna(out.delta_net_bp.iloc[2]) and groups.n.sum()==3
    assert out.partial_net_contribution_bp.tolist()==[20,20,0]


@pytest.mark.parametrize("col",["exit_time","exit_price","outcome","max_favourable_r","max_adverse_r","hold_minutes"])
def test_final_path_change_rejected(col):
    b,a=ledger()
    if col=="outcome": a.loc[0,col]="colour_exit"
    elif col=="exit_time": a.loc[0,col]+=pd.Timedelta(minutes=5)
    else: a.loc[0,col]+=1
    with pytest.raises((ValueError,AssertionError)): r.assert_final_path(b,a)


@pytest.mark.parametrize("idx,value",[(0,-.02),(1,-.01)])
def test_economic_impossibility_rejected(idx,value):
    b,a=ledger(); a.loc[idx,"net_return"]=value
    with pytest.raises(ValueError): r.partial_mechanics(b,a)


def fast_fixture():
    t=pd.Timestamp("2024-01-01",tz="UTC")
    c=pd.DataFrame({"event_id":["a","b"],"mg_entry_state":["aligned","unknown"],
        "mg_entry_reason":["valid","missing_management"],"mg_entry_known":[True,False],
        "mg_entry_side":[1,np.nan],"mg_entry_ma":[99,np.nan],"mg_entry_hl2":[100,np.nan],
        "mg_entry_management_segment_id":[0,np.nan],"mg_entry_raw_segment_id":[0,np.nan],
        "mg_entry_bar_open":[t-pd.Timedelta(minutes=5),pd.NaT],"mg_entry_available_at":[t,pd.NaT]})
    a=c.rename(columns={"mg_entry_state":"partial_fast_initial_state","mg_entry_reason":"partial_fast_initial_reason",
        "mg_entry_side":"partial_fast_initial_side","mg_entry_ma":"partial_fast_initial_ma",
        "mg_entry_hl2":"partial_fast_initial_hl2","mg_entry_management_segment_id":"partial_fast_initial_management_segment_id",
        "mg_entry_raw_segment_id":"partial_fast_initial_raw_segment_id","mg_entry_bar_open":"partial_fast_initial_open_time",
        "mg_entry_available_at":"partial_fast_initial_available_at"}).copy()
    a["risk_pct"]=.01; a["risk_atr"]=1
    return c,a


def test_fast_seed_unknown_preserved_and_order_independent():
    c,a=fast_fixture(); r.assert_fast_initial(c,a.iloc[::-1])


@pytest.mark.parametrize("c",["state","reason","side","ma","hl2","management_segment_id","raw_segment_id","open_time","available_at"])
def test_fast_seed_mismatch_rejected(c):
    ctx,a=fast_fixture(); col="partial_fast_initial_"+c
    if c=="state": a.loc[0,col]="opposite"
    elif c=="reason": a.loc[0,col]="missing_management"
    elif c in ("open_time","available_at"): a.loc[0,col]+=pd.Timedelta(minutes=5)
    else: a.loc[0,col]+=1
    with pytest.raises((ValueError,AssertionError)): r.assert_fast_initial(ctx,a)


def test_source_freeze_precedes_outcome_and_candidate():
    source=inspect.getsource(r.run)
    assert source.index('"context_frozen.json"')<source.index('pin(ROOT/PARENT,INPUTS)')<source.index('old=replay_arm')<source.index('new=replay_arm')
    assert 'parent_prefix=""' in source and 'simulator=simulate_dual' in source


def test_fold_simulator_passes_correct_native_stream_only_when_enabled(monkeypatch):
    calls=[]
    class S:
        raw=object(); folds=[("a","2024-01-01","2024-02-01")]; config={"execution":{"cost_fraction":.002}}
        def featured(self,m,k,n): return (m,k,n)
    def fake(raw,mg,e,p,**kw):
        calls.append((mg,p,kw)); return e.copy()
    monkeypatch.setattr(r,"simulate_events",fake)
    entries=pd.DataFrame({"fold":["a"]})
    for p in r.POLICIES: r.simulate_dual(S(),entries,p)
    assert calls[0][0]==calls[1][0]==(15,"SMA",40)
    assert "fast_management_featured" not in calls[0][2]
    assert calls[1][2]["fast_management_featured"]==(5,"SMA",40)

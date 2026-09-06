"""V12 fixed-boundary orchestration, causal geometry, and synthetic ledgers.

No saved strategy outcomes or real source prices are read. Runner tests replace
all evidence readers and Study; temporary files contain only test fixtures.
"""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_frozen_ma_research as r
from yoyo.evaluation import hourly_impulse_launch_research as parent


def base():
    return {"execution":{"max_hours":72,"cost_fraction":.002,"stop_first":True},
            "development_folds":deepcopy(r.FOLDS),"baseline":{"synthetic":True}}


def inputs():
    """Five exact g bins plus a mirrored control with its OWN different MA."""
    times=pd.date_range("2024-01-01 01:00",periods=5,freq="h",tz="UTC")
    cases=pd.DataFrame({"event_id":[f"c{i}" for i in range(5)],"decision_time":times,
        "signal_time":times-pd.Timedelta(hours=1),"fold":"2024H1","direction":1,
        "ma":[102.,100.,99.,98.,96.],"signal_close":103.,"signal_atr":2.,"initial_stop":98.})
    control=cases.iloc[:1].copy().assign(event_id="r0",parent_event_id="c0",direction=-1,
        ma=101.,signal_close=99.,initial_stop=102.)
    control["decision_time"]+=pd.Timedelta(days=1)
    control["signal_time"]+=pd.Timedelta(days=1)
    contexts={"case":cases,"control":control}
    assignments=cases[["event_id"]].assign(match_status=["matched"]+["insufficient_exact_controls"]*4)
    raw_rows=[]
    hourly_rows=[]
    for frame in contexts.values():
        for event in frame.to_dict("records"):
            for stamp in (event["decision_time"]-pd.Timedelta(minutes=5),event["decision_time"]):
                raw_rows.append({"open_time":stamp,"open":100.,"segment_id":0,
                    "high":"do not read","low":object(),"close":np.inf,"volume":None})
            hourly_rows.append({"open_time":event["signal_time"],"ma":event["ma"],"close":event["signal_close"]})
    return contexts,assignments,pd.DataFrame(raw_rows),pd.DataFrame(hourly_rows)


def test_frozen_config_is_only_one_exit_rule_and_deep_copy():
    config=r.frozen_config()
    r.verify_config(config,base())
    old,new=deepcopy(config["policies"])
    assert old==parent.POLICIES[0]
    assert new.pop("frozen_ma_exit") is True
    new["id"]=old["id"]
    assert old==new
    assert not any("launch" in key for policy in config["policies"] for key in policy)
    comparable=deepcopy(config)
    comparable.pop("boundary_contract")
    comparable["experiment_id"]=parent.EXPERIMENT_ID
    comparable["policies"]=deepcopy(parent.POLICIES)
    assert comparable==parent.frozen_config()
    assert config["boundary_contract"]["geometry_bins"]=="negative_zero_inside_equal_stop_beyond_stop"
    assert len(config["inputs"])==9 and len(config["mother_inputs"])==4
    assert config["selection"]["matched_coverage"]==.9
    assert config["known_support"]["matched"]==154
    config["policies"][0]["ma_length"]=999
    config["boundary_contract"]["field"]="bad"
    assert r.frozen_config()["policies"][0]["ma_length"]==40
    assert r.frozen_config()["boundary_contract"]["field"]=="ma"
    assert r.replay_arm is parent.replay_arm
    assert len(r.SOURCES)==len(set(r.SOURCES))
    assert "tests/test_hourly_impulse_frozen_ma_exit.py" in r.SOURCES


@pytest.mark.parametrize("key,value",[("frozen_ma_exit",1),("frozen_ma_exit",False),
    ("frozen_ma_exit","True"),("launch_deadline_minutes",60),("management_minutes",15),
    ("decision_minutes",15),("ma_length",20),("confirmations",True),("exit_mode","colour"),
    ("entry_side_gate",True)])
def test_policy_drift_rejected(key,value):
    config=r.frozen_config();config["policies"][1][key]=value
    with pytest.raises(ValueError):r.verify_config(config,base())


@pytest.mark.parametrize("key",list(r.BOUNDARY_CONTRACT))
def test_every_boundary_contract_field_frozen(key):
    config=r.frozen_config();config["boundary_contract"][key]="drift"
    with pytest.raises(ValueError):r.verify_config(config,base())


@pytest.mark.parametrize("change",["fee","max","stop","fold","holdout","numericfalse","input","extra","gate"])
def test_scope_economics_and_support_drift(change):
    config,b=r.frozen_config(),base()
    if change=="fee":b["execution"]["cost_fraction"]=.001
    elif change=="max":b["execution"]["max_hours"]=24
    elif change=="stop":b["execution"]["stop_first"]=False
    elif change=="fold":b["development_folds"][-1][-1]="2026-01-01"
    elif change=="holdout":config["holdout_consumed"]=True
    elif change=="numericfalse":config["holdout_consumed"]=0
    elif change=="input":config["inputs"].pop("summary.json")
    elif change=="extra":config["allow_audit"]=True
    else:config["selection"]["matched_coverage"]=.6
    with pytest.raises(ValueError):r.verify_config(config,b)


def test_geometry_all_bins_own_control_ma_and_no_source_hlc_read():
    contexts,assignments,raw,_=inputs()
    originals={key:frame.copy(deep=True) for key,frame in contexts.items()}
    result=r.build_entry_geometry(raw,contexts,assignments)
    assert result.event_id.tolist()==["c0","c1","c2","c3","c4","r0"]
    assert result.geometry_bin.tolist()==["negative","zero","inside","equal_stop","beyond_stop","inside"]
    assert result.entry_distance_r.tolist()==[-1.,0.,.5,1.,2.,.5]
    assert result.entry_side.tolist()==[-1,0,1,1,1,1]
    assert result.initial_R.eq(2).all()
    assert result.entry_distance_atr.tolist()==[-1.,0.,.5,1.,2.,.5]
    assert result.matched_case.tolist()==[True,False,False,False,False,True]
    assert result.iloc[-1].ma==101. and result.iloc[-1].ma!=result.iloc[0].ma
    assert result.iloc[-1].previous_hour_close_distance_atr==1.
    for key,frame in contexts.items():pd.testing.assert_frame_equal(frame,originals[key])
    summary=r.geometry_summary(result)
    assert summary["all_cases"]=={"n":5,"geometry_bins":dict.fromkeys(r.GEOMETRY_BINS,1)}
    assert summary["matched_cases"]["n"]==1
    assert summary["unmatched_cases"]["n"]==4
    assert summary["controls"]["geometry_bins"]["inside"]==1


def test_geometry_prefix_future_mutation_timezone_and_order_invariance():
    contexts,assignments,raw,_=inputs()
    expected=r.build_entry_geometry(raw,contexts,assignments)
    suffix=raw.iloc[-1:].copy()
    suffix["open_time"]+=pd.Timedelta(days=10)
    suffix["open"]=-np.inf;suffix["segment_id"]=np.nan
    changed=pd.concat([raw,suffix],ignore_index=True)
    changed["high"]=float("nan");changed["low"]="future";changed["close"]=-1.
    result=r.build_entry_geometry(changed,contexts,assignments)
    pd.testing.assert_frame_equal(expected,result)
    for frame in contexts.values():
        for col in ("decision_time","signal_time"):frame[col]=frame[col].dt.tz_convert("Asia/Shanghai")
    result=r.build_entry_geometry(raw,contexts,assignments)
    pd.testing.assert_frame_equal(expected.drop(columns="signal_time"),result.drop(columns="signal_time"))
    assert pd.to_datetime(result.signal_time,utc=True).equals(expected.signal_time)


@pytest.mark.parametrize("column,value",[("ma",0),("ma",np.inf),("ma",np.nan),("ma",True),
    ("signal_close",0),("signal_atr",-1),("initial_stop",np.nan),("direction",0),("direction",True)])
def test_invalid_boundary_input_not_filtered(column,value):
    contexts,assignments,raw,_=inputs()
    contexts["control"][column]=value
    with pytest.raises(ValueError):r.build_entry_geometry(raw,contexts,assignments)


@pytest.mark.parametrize("mutation",["naive","numeric","late","nothour","nan","duplicateid"])
def test_invalid_signal_clock_fails_before_any_prices(mutation):
    contexts,_,_,_=inputs()
    frame=contexts["case"]
    if mutation=="naive":frame["signal_time"]=frame.signal_time.dt.tz_localize(None)
    elif mutation=="numeric":frame["signal_time"]=1730000000
    elif mutation=="late":frame["signal_time"]+=pd.Timedelta(nanoseconds=1)
    elif mutation=="nothour":
        for col in ("signal_time","decision_time"):frame[col]+=pd.Timedelta(minutes=5)
    elif mutation=="nan":frame["signal_time"]=pd.NaT
    else:frame.loc[0,"event_id"]="c1"
    with pytest.raises(ValueError):r.validate_boundary_inputs(contexts)


@pytest.mark.parametrize("mutation",["entrymissing","priormissing","segment","missingsegment","openzero",
    "opennan","openbool","risk","duplicate","order","offgrid","numeric","assignment","parent"])
def test_invalid_entry_geometry_fails_without_dropping_request(mutation):
    contexts,a,raw,_=inputs()
    if mutation=="entrymissing":raw=raw.drop(index=1)
    elif mutation=="priormissing":raw=raw.drop(index=0)
    elif mutation=="segment":raw.loc[1,"segment_id"]=2
    elif mutation=="missingsegment":raw["segment_id"]=np.nan
    elif mutation=="openzero":raw.loc[1,"open"]=0
    elif mutation=="opennan":raw.loc[1,"open"]=np.nan
    elif mutation=="openbool":raw["open"]=True
    elif mutation=="risk":raw.loc[1,"open"]=97
    elif mutation=="duplicate":raw=pd.concat([raw.iloc[:1],raw],ignore_index=True)
    elif mutation=="order":raw=raw.iloc[::-1]
    elif mutation=="offgrid":raw.loc[0,"open_time"]+=pd.Timedelta(seconds=1)
    elif mutation=="numeric":raw["open_time"]=range(len(raw))
    elif mutation=="assignment":a=a.iloc[1:]
    else:contexts["control"]["parent_event_id"]="c4"
    with pytest.raises(ValueError):r.build_entry_geometry(raw,contexts,a)


def test_rebuilt_own_signal_hour_source_and_tolerance():
    contexts,_,_,hourly=inputs()
    # A later row and an unused warmup NaN cannot change an earlier boundary.
    hourly=pd.concat([hourly,pd.DataFrame({"open_time":[pd.Timestamp("2023-12-30",tz="UTC"),
        pd.Timestamp("2025-01-01",tz="UTC")],"ma":[np.nan,-1],"close":[np.nan,-1]})],ignore_index=True)
    contexts["control"]["ma"]+=1e-11
    receipt=r.validate_boundary_source(hourly,contexts)
    assert receipt["populations"]["case"]["n"]==5
    assert receipt["populations"]["control"]["ma_matched"]==1
    assert receipt["populations"]["control"]["ma_max_abs_error"]>0
    assert receipt["saved_values_changed"] is False
    assert receipt["before_any_arm_outcomes"] is True
    assert receipt["feature_spec"]=={"minutes":60,"ma_kind":"SMA","ma_length":40,"ma_source":"HL2"}


@pytest.mark.parametrize("mutation",["controlma","controlclose","casema","missing","duplicate","nan","clock"])
def test_copied_old_context_cannot_fake_own_hourly_ma_source(mutation):
    contexts,_,_,hourly=inputs()
    if mutation=="controlma":contexts["control"]["ma"]=contexts["case"].ma.iloc[0]
    elif mutation=="controlclose":contexts["control"]["signal_close"]+=1
    elif mutation=="casema":contexts["case"]["ma"]+=1
    elif mutation=="missing":hourly=hourly.iloc[:-1]
    elif mutation=="duplicate":hourly=pd.concat([hourly,hourly.iloc[:1]],ignore_index=True)
    elif mutation=="nan":hourly.loc[0,"ma"]=np.nan
    else:hourly.loc[0,"open_time"]+=pd.Timedelta(minutes=5)
    with pytest.raises(ValueError):r.validate_boundary_source(hourly,contexts)


def trades():
    entry=pd.Timestamp("2024-01-01 01:00",tz="UTC")
    old=pd.DataFrame({"event_id":["loss","win","same","unknown"],"entry_time":entry,
        "decision_time":entry,"signal_time":entry-pd.Timedelta(hours=1),"ma":[99.,101.,99.,99.],
        "entry_price":100.,"direction":[1,-1,1,1],"initial_stop":[98.,102.,98.,98.],"signal_atr":1.,
        "risk_pct":.02,"risk_atr":2.,"closed":[True,True,True,False],
        "net_return":[-.01,.02,-.003,np.nan],"gross_return":[-.008,.022,-.001,np.nan],
        "hold_minutes":[120,90,10,5],"outcome":["transition_colour_exit"]*3+["right_censored"],
        "exit_time":[entry+pd.Timedelta(minutes=n) for n in (120,90,10,5)],
        "mfe_r":[.3,1.5,.1,.1],"mae_r":[.8,.2,.4,.1],"transition_native_diagnostic":[4,3,2,1]})
    new=old.copy()
    new["frozen_ma_enabled"]=True;new["frozen_ma_boundary"]=new.ma
    new["frozen_ma_available_at"]=entry;new["frozen_ma_entry_distance_atr"]=[1.,1.,1.,1.]
    new["frozen_ma_trigger_open_time"]=pd.Series(pd.NaT,index=new.index,dtype="datetime64[ns, UTC]")
    new["frozen_ma_trigger_available_at"]=pd.Series(pd.NaT,index=new.index,dtype="datetime64[ns, UTC]")
    new["frozen_ma_trigger_close"]=np.nan;new["frozen_ma_completed_close_count"]=[1,3,2,1]
    new["frozen_ma_status"]=["structure_exit","structure_exit","prior_exit","unknown_source"]
    new.loc[:1,"hold_minutes"]=[5,15]
    new.loc[:1,"exit_time"]=[entry+pd.Timedelta(minutes=n) for n in (5,15)]
    new.loc[:1,"frozen_ma_trigger_available_at"]=new.loc[:1,"exit_time"]
    new.loc[:1,"frozen_ma_trigger_open_time"]=new.loc[:1,"exit_time"]-pd.Timedelta(minutes=5)
    new.loc[:1,"frozen_ma_trigger_close"]=[98.5,101.5]
    new.loc[:1,"outcome"]="frozen_ma_exit"
    new.loc[:1,"net_return"]=[.001,-.002];new.loc[:1,"gross_return"]=[.003,0.]
    return old,new


def test_all_paired_mechanics_no_loser_selection_and_asymmetric_schema():
    old,new=trades()
    joined,groups,info=r.paired_mechanics(old,new)
    assert len(joined)==4 and info["total"]==4 and info["known"]==3
    assert info["frozen_ma_exits"]==2
    assert joined.difference.iloc[2]==0 and np.isnan(joined.difference.iloc[3])
    assert info["transitions"]=={"loss_to_win":1,"win_to_loss":1,"loss_to_loss":1,"unknown":1}
    assert "frozen_ma_boundary" in joined and "frozen_ma_boundary_after" not in joined
    assert {"mfe_r_before","mfe_r_after","mae_r_before","mae_r_after"}.issubset(joined)
    assert groups.n.sum()==4
    assert info["distributions"]["difference"]["unknown"]==1
    assert info["distributions"]["difference"]["outliers_removed"]==0
    assert len(info["distributions"]["difference"]["quantiles_bp"])==7


@pytest.mark.parametrize("mutation",["entry","stop","boundary","ownma","equalclose","wrongclose",
    "futuretrigger","triggergrid","available","late","sameold","hold","enabled","status","known",
    "signalavailable","same_mfe","same_mae","same_return","same_native","same_exit"])
def test_frozen_exit_and_all_original_fields_strictly_checked(mutation):
    old,new=trades()
    if mutation=="entry":new.loc[0,"entry_price"]+=1
    elif mutation=="stop":new.loc[0,"initial_stop"]-=1
    elif mutation=="boundary":new.loc[0,"frozen_ma_boundary"]-=1
    elif mutation=="ownma":new.loc[0,"ma"]-=1
    elif mutation=="equalclose":new.loc[0,"frozen_ma_trigger_close"]=99
    elif mutation=="wrongclose":new.loc[0,"frozen_ma_trigger_close"]=100
    elif mutation=="futuretrigger":new.loc[0,"frozen_ma_trigger_open_time"]+=pd.Timedelta(minutes=5)
    elif mutation=="triggergrid":new.loc[0,"frozen_ma_trigger_open_time"]+=pd.Timedelta(seconds=1)
    elif mutation=="available":new.loc[0,"frozen_ma_trigger_available_at"]+=pd.Timedelta(minutes=5)
    elif mutation in ("late","sameold"):
        old.loc[0,"exit_time"]=new.loc[0,"exit_time"]-pd.Timedelta(minutes=5 if mutation=="late" else 0)
    elif mutation=="hold":new.loc[0,"hold_minutes"]=0
    elif mutation=="enabled":new["frozen_ma_enabled"]="True"
    elif mutation=="status":new.loc[0,"frozen_ma_status"]="prior_exit"
    elif mutation=="known":new.loc[0,"closed"]=False
    elif mutation=="signalavailable":new.loc[0,"frozen_ma_available_at"]+=pd.Timedelta(minutes=5)
    elif mutation=="same_mfe":new.loc[2,"mfe_r"]+=1
    elif mutation=="same_mae":new.loc[2,"mae_r"]+=1
    elif mutation=="same_return":new.loc[2,"net_return"]+=1
    elif mutation=="same_native":new.loc[2,"transition_native_diagnostic"]+=1
    else:new.loc[2,"exit_time"]+=pd.Timedelta(minutes=5)
    with pytest.raises((AssertionError,ValueError)):r.paired_mechanics(old,new)


def test_priority_winner_may_retain_trigger_without_becoming_frozen_exit():
    old,new=trades()
    new.loc[2,"frozen_ma_trigger_open_time"]=new.loc[2,"exit_time"]-pd.Timedelta(minutes=5)
    new.loc[2,"frozen_ma_trigger_available_at"]=new.loc[2,"exit_time"]
    new.loc[2,"frozen_ma_trigger_close"]=98.
    new.loc[0,"net_return"]=0
    joined,_,info=r.paired_mechanics(old,new)
    assert joined.loc[2,"mechanism_group"]=="original_exit_retained"
    assert joined.loc[0,"win_loss_transition"]=="includes_flat"
    assert info["frozen_ma_exits"]==2


def test_monthly_has_all48_rows_and_empty_months_unknown():
    episode=pd.DataFrame({"mother_decision_time":pd.to_datetime(["2023-01-01","2024-12-01"],utc=True),
        "fold":["2023H1","2024H2"],"observed":[True,False],"episode_net_return":[.001,np.nan]})
    table=r.monthly_case_table(episode,episode)
    assert len(table)==48 and table.groupby("arm").size().eq(24).all()
    assert table.iloc[0].month=="2023-01" and table.iloc[-1].month=="2024-12"
    assert table.n.sum()==4 and table.known.sum()==2
    assert table.loc[table.n.eq(0),"mean_net_bp"].isna().all()
    assert table.loc[table.month.eq("2024-12"),"mean_net_bp"].isna().all()


def test_all251_effects_keep154_supported_and97_unknown_not_zero():
    times=pd.date_range("2024-01-01",periods=251,freq="h",tz="UTC")
    old=pd.DataFrame({"event_id":[f"c{i}" for i in range(251)],"mother_decision_time":times,"episode_net_return":-.002})
    new=old.copy();new["episode_net_return"]+=.0001
    matched=[]
    for table in (old,new):
        m=table.rename(columns={"episode_net_return":"event_net_return"}).copy()
        m["assigned_controls"]=[3]*154+[0]*97
        m["control_mean_return"]=[-.001]*154+[np.nan]*97
        m["excess"]=m.event_net_return-m.control_mean_return
        matched.append(m)
    frames,effects=r.paired_effects(old,new,*matched,old.assign(portfolio_selected=True),new.assign(portfolio_selected=True))
    assert effects["case_delta"]["total_pairs"]==251
    assert frames["case_delta"].difference.notna().sum()==251
    assert effects["excess_delta"]["unknown_pairs"]==97
    assert frames["excess_delta"].difference.notna().sum()==154
    assert np.isclose(frames["case_delta"].difference.sum(),251*.0001)


def mocked_run(monkeypatch,tmp_path,fail_at):
    """A temporary synthetic input boundary; never calls real Study/read_frame."""
    calls=[];experiment=tmp_path/"experiment";experiment.mkdir()
    config=r.frozen_config();(experiment/"config.json").write_text(json.dumps(config))
    base_path=tmp_path/r.BASE_CONFIG;base_path.parent.mkdir(parents=True)
    base_path.write_text(json.dumps(base()))
    monkeypatch.setattr(r,"ROOT",tmp_path);monkeypatch.setattr(r,"EXPERIMENT",experiment)
    contexts,assignments,raw,hourly=inputs()
    mothers={key:frame.copy() for key,frame in contexts.items()}
    if fail_at=="invalidma":contexts["control"]["ma"]=np.nan
    if fail_at=="copied_control_ma":
        contexts["control"]["ma"]=102.
        mothers["control"]["ma"]=102. # Old mother/context parity alone agrees.
    expected={base_path:r.BASE_SHA256}
    for folder,hashes in ((tmp_path/r.MOTHERS,r.MOTHER_INPUTS),(tmp_path/r.PARENT,r.INPUTS)):
        expected.update({folder/name:value for name,value in hashes.items()})
    def digest(path):
        calls.append("hash:"+path.name)
        if path.name=="entry_geometry.csv":
            assert path.exists();return "synthetic_geometry_hash"
        assert path in expected,"Unexpected evidence read"
        return "wrong" if fail_at=="hash:"+path.name else expected[path]
    def committed(paths):
        calls.append("sources")
        assert all(path.is_relative_to(tmp_path) for path in paths)
        if fail_at=="sources":raise RuntimeError("synthetic guard")
        return [{"path":"synthetic_builder","sha256":"synthetic"}]
    def read(path):
        calls.append("read:"+path.name)
        assert path.is_relative_to(tmp_path)
        if path.name=="original_mothers.csv.gz":return mothers["case"].copy()
        if path.name=="control_mothers.csv.gz":return mothers["control"].copy()
        if path.name=="assignments.csv":return assignments.copy()
        for label in ("case","control"):
            if path.name==f"direct_k1_stop_{label}_context.csv.gz":return contexts[label].copy()
        raise AssertionError("Unexpected outcome/price read")
    def population(*args):
        calls.append("population")
        if fail_at=="population":raise ValueError("synthetic population guard")
    class FakeStudy:
        def __init__(self,supplied,phase):
            calls.append("study")
            assert supplied==base() and phase=="development"
            if fail_at=="study":raise RuntimeError("synthetic source failure")
            self.raw=raw.copy()
            if fail_at=="entryopen":self.raw.loc[1,"open"]=0.
        def featured(self,minutes,kind,length):
            calls.append("featured:"+str(minutes));assert (kind,length)==("SMA",40)
            assert minutes in (5,60)
            return hourly.copy() if minutes==60 else object()
        def entries(self,spec):
            calls.append("entries");assert spec=={"synthetic":True}
            result=mothers["case"].copy()
            if fail_at=="entries":result.loc[0,"ma"]+=1
            return result
    def context(source,management,request):
        label="control" if request.event_id.iloc[0]=="r0" else "case"
        calls.append("context:"+label)
        result=contexts[label].copy()
        if fail_at==label+"_context":result.loc[0,"ma"]+=1
        return result
    def direct(frame):return frame.copy(),pd.DataFrame()
    stages=["case_trades","case_episodes","control_trades","control_episodes","matched","single_pending"]
    def replay(study,policy,mothers,contexts,folder,config,**kwargs):
        calls.append("arm:"+policy["id"])
        assert (experiment/"results/entry_geometry_frozen.json").exists()
        assert (experiment/"results/boundary_source_parity.json").exists()
        if policy==r.POLICIES[0]:
            assert kwargs["parent"]==tmp_path/r.PARENT
            for stage in stages:
                calls.append("baseline_parity:"+stage)
                if fail_at==stage:raise AssertionError("synthetic all-field parity failure")
            return ({"parity":{key:{"rows":1,"columns":9} for key in stages}},None,None,None,None)
        assert policy==r.POLICIES[1] and not kwargs
        assert (experiment/"results/anchor_parity.json").exists()
        raise RuntimeError("candidate sentinel: synthetic test ends before simulation")
    monkeypatch.setattr(r,"digest",digest);monkeypatch.setattr(r,"committed_sources",committed)
    monkeypatch.setattr(r,"read_frame",read);monkeypatch.setattr(r,"validate_population",population)
    monkeypatch.setattr(r,"Study",FakeStudy);monkeypatch.setattr(r,"attach_entry_colour_context",context)
    monkeypatch.setattr(r,"direct_requests",direct);monkeypatch.setattr(r,"replay_arm",replay)
    monkeypatch.setattr(r.subprocess,"check_output",lambda *args,**kwargs:"synthetic_commit")
    if fail_at=="existing":(experiment/"results").mkdir()
    with pytest.raises((ValueError,AssertionError,RuntimeError)):
        r.run()
    return calls,experiment


@pytest.mark.parametrize("stage",["sources","hash:summary.json","population","invalidma","existing"])
def test_preflight_failure_prevents_study(monkeypatch,tmp_path,stage):
    calls,experiment=mocked_run(monkeypatch,tmp_path,stage)
    assert "study" not in calls and not any(x.startswith("arm:") for x in calls)
    assert not (experiment/"results/entry_geometry.csv").exists()


@pytest.mark.parametrize("stage",["study","copied_control_ma","entryopen","entries","case_context","control_context"])
def test_own_source_geometry_and_context_failure_prevent_any_arm(monkeypatch,tmp_path,stage):
    calls,experiment=mocked_run(monkeypatch,tmp_path,stage)
    assert "study" in calls and not any(x.startswith("arm:") for x in calls)
    assert (experiment/"results/failure.json").exists()
    if stage=="copied_control_ma":
        failure=json.loads((experiment/"results/failure.json").read_text())
        assert "control.ma" in failure["message"]
        assert not (experiment/"results/entry_geometry.csv").exists()


@pytest.mark.parametrize("stage",["case_trades","case_episodes","control_trades","control_episodes","matched","single_pending"])
def test_any_old_allfield_parity_failure_prevents_candidate(monkeypatch,tmp_path,stage):
    calls,experiment=mocked_run(monkeypatch,tmp_path,stage)
    assert "arm:5m_native40" in calls and "arm:5m_native40_frozen_ma" not in calls
    assert (experiment/"results/entry_geometry.csv").exists()
    assert (experiment/"results/failure.json").exists()
    assert not (experiment/"results/anchor_parity.json").exists()


def test_geometry_and_all_six_old_tables_frozen_before_candidate(monkeypatch,tmp_path):
    calls,experiment=mocked_run(monkeypatch,tmp_path,"candidate")
    assert calls.index("sources")<calls.index("population")<calls.index("study")
    assert calls.index("study")<calls.index("featured:60")<calls.index("hash:entry_geometry.csv")
    assert calls.index("hash:entry_geometry.csv")<calls.index("arm:5m_native40")
    assert calls.index("baseline_parity:single_pending")<calls.index("arm:5m_native40_frozen_ma")
    receipt=json.loads((experiment/"results/entry_geometry_frozen.json").read_text())
    assert receipt["before_any_arm_outcomes"] is True and receipt["used_for_selection"] is False
    assert receipt["population"]["all_cases"]["n"]==5
    boundary=json.loads((experiment/"results/boundary_source_parity.json").read_text())
    assert boundary["populations"]["control"]["ma_matched"]==1
    assert not (experiment/"results/summary.json").exists()

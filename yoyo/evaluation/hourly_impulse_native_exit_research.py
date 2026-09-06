"""V15 native5m versus native15m true-colour exits on original251 intentions.

The management specification changes aggregation, SMA40 HL2 memory and clock;
this is NOT the isolated effect of waiting15 minutes on a5m series. Initial
states for all713 own requests and BOTH policies are frozen before outcome
files are hashed/read or replayed. Raw5m hard-stop checks, hourly direct entry,
K1 extreme stop,72h deadline and20bp cost remain unchanged. No entry selection.
Reuse2023--2024 only; neither independent validation nor a production candidate.

Inputs use completed native bars at entry and preceding39 contiguous native
HL2 observations. Paired inference uses calendar months, not random splits:
https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.choice.html
One-to-one ledger identity/parity follows pandas2.3 contracts:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
"""
from __future__ import annotations

from copy import deepcopy
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_colour_context import attach_entry_colour_context
from yoyo.data.hourly_impulse_native_exit_context import attach_native_exit_context, NATIVE_CONTEXT_COLUMNS
from yoyo.evaluation.hourly_impulse_context_research import committed_sources, development_gates, month_support
from yoyo.evaluation.hourly_impulse_diagnostics import diagnose_frame
from yoyo.evaluation.hourly_impulse_k2_research import describe, direct_requests, episode_ledger, matched_episodes, single_pending_ledger
from yoyo.evaluation.hourly_impulse_launch_research import (
    BASE_CONFIG, BASE_SHA256, MOTHERS, MOTHER_INPUTS, PARENT, INPUTS, FOLDS,
    SOURCES as BASE_SOURCES, frozen_config as base_config, validate_population,
)
from yoyo.evaluation.hourly_impulse_management_research import assert_saved_parity, paired_effects, positive_inference, simulate_native
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, digest, metrics, write_csv, write_json
from yoyo.evaluation.hourly_impulse_transition_research import read_frame

EXPERIMENT_ID = "exp-btcusdtp-1h-native15-exit-preholdout-20260906-v15"
EXPERIMENT = ROOT/"experiments/active"/EXPERIMENT_ID
POLICIES = [dict(id=f"{m}m_native40", management_minutes=m, ma_kind="SMA", ma_length=40,
                 exit_mode="transition_colour", confirmations=1) for m in (5,15)]
SOURCES = list(dict.fromkeys(BASE_SOURCES + [
    "yoyo/data/hourly_impulse_management_context.py", "yoyo/data/hourly_impulse_native_exit_context.py",
    "yoyo/evaluation/hourly_impulse_native_exit_research.py", "tests/test_hourly_impulse_native_exit_context.py",
    "tests/test_hourly_impulse_native_exit_research.py", "tests/test_hourly_impulse_transition_15m.py",
]))
CONTEXT_INPUTS = {k:v for k,v in INPUTS.items() if k.endswith("_context.csv.gz")}
OUTCOME_INPUTS = {k:v for k,v in INPUTS.items() if k not in CONTEXT_INPUTS}


def frozen_config():
    config=deepcopy(base_config())
    config.update(experiment_id=EXPERIMENT_ID, policies=deepcopy(POLICIES),
        native_contract={"context_freeze_before_outcomes":True,"raw_stop_minutes":5,
            "direct_wait_hours":0,"sma_memory_minutes":[200,600],"entry_gates":False,
            "v1_state15_semantic_diagnostic":True,"selection_uses_diagnostic":False})
    return config


def verify_config(config, base):
    if json.dumps(config,sort_keys=True) != json.dumps(frozen_config(),sort_keys=True):
        raise ValueError("Frozen V15 native management contract changed")
    if base["development_folds"] != FOLDS:
        raise ValueError("Only frozen2023--2024 development is permitted")
    e=base["execution"]
    if e["max_hours"] != 72 or e["cost_fraction"] != .002 or e["stop_first"] is not True:
        raise ValueError("Original stop-first/72h/20bp economics required")


def validate_direct_context(frame):
    """Native replay is safe here only for original direct hourly requests."""
    d=pd.to_datetime(frame.decision_time,utc=True,format="mixed")
    mother=pd.to_datetime(frame.mother_decision_time,utc=True,format="mixed")
    deadline=pd.to_datetime(frame.mother_deadline,utc=True,format="mixed")
    if not (frame.wait_hours.eq(0)&d.eq(mother)&d.eq(d.dt.floor("h"))&
            deadline.eq(d+pd.Timedelta(hours=72))).all():
        raise ValueError("Only original direct hourly entry and72h deadline permitted")


def assert_native_initial_state(trades):
    valid=trades.risk_pct.gt(0)&trades.risk_atr.gt(0)
    t=trades.loc[valid]
    if not t.mg_entry_known.eq(t.mg_entry_state.isin(["aligned","opposite"])).all():
        raise ValueError("Native known flag must agree with its explicit state")
    if not t.mg_entry_state.eq(t.transition_initial_state).all():
        raise ValueError("Independent native context and engine initial state disagree")
    if not t.mg_entry_reason.eq(t.transition_initial_reason).all():
        raise ValueError("Independent native context and engine initial reason disagree")
    known=t.mg_entry_known
    np.testing.assert_allclose(t.loc[known,"mg_entry_side"].astype(float),
        t.loc[known,"transition_initial_side"].astype(float),rtol=0,atol=0)
    a=pd.to_datetime(t.loc[known,"mg_entry_bar_open"],utc=True)
    b=pd.to_datetime(t.loc[known,"transition_initial_open_time"],utc=True)
    if not a.eq(b).all():
        raise ValueError("Independent native context and engine seed time disagree")


def replay_arm(study, policy, mothers, contexts, folder, config, *, parent=None):
    folder.mkdir()
    trades,episodes,parity={},{},{}
    folds=[f[0] for f in FOLDS]
    prefix="direct_k1_stop__transition_colour_"
    for label in ("case","control"):
        validate_direct_context(contexts[label])
        t=simulate_native(study,contexts[label],policy)
        assert_native_initial_state(t)
        e=episode_ledger(mothers[label],direct_requests(mothers[label])[1],t)
        if parent is not None:
            for suffix,table in (("trades",t),("episodes",e)):
                saved=read_frame(parent/(prefix+label+"_"+suffix+".csv.gz"))
                assert_saved_parity(saved,table)
                parity[label+"_"+suffix]={"rows":len(saved),"columns":len(saved.columns)}
        trades[label],episodes[label]=t,e
        write_csv(folder/f"{label}_trades.csv.gz",t)
        write_csv(folder/f"{label}_episodes.csv.gz",e)
    pairs,matching=matched_episodes(episodes["case"],episodes["control"])
    serial=single_pending_ledger(episodes["case"])
    if parent is not None:
        for name,table,suffix in (("matched",pairs,".csv"),("single_pending",serial,".csv.gz")):
            saved=read_frame(parent/(prefix+name+suffix))
            assert_saved_parity(saved,table)
            parity[name]={"rows":len(saved),"columns":len(saved.columns)}
    write_csv(folder/"matched.csv",pairs)
    write_csv(folder/"single_pending.csv.gz",serial)
    chosen=set(serial.loc[serial.portfolio_selected,"event_id"])
    selected=trades["case"].loc[trades["case"].event_id.isin(chosen)]
    info,controls,single=(metrics(x,folds) for x in (trades["case"],trades["control"],selected))
    months=month_support(trades["case"],folds)
    gates=development_gates(info,matching,single,months,config)
    gates["complete_evidence"]=bool(episodes["case"].observed.all() and episodes["control"].observed.all())
    net=describe(episodes["case"].episode_net_return,episodes["case"].mother_decision_time)
    gates.update(net_inference=positive_inference(net),excess_inference=positive_inference(matching["effect"]))
    classified,diagnosis,tables=diagnose_frame(trades["case"])
    write_csv(folder/"classified_case_trades.csv.gz",classified)
    for name,frame in tables.items():
        write_csv(folder/("diagnosis_"+name+".csv"),frame)
    summary={"policy":policy,"metrics":info,"control_metrics":controls,"matching":matching,
        "single_position":single,"serial_selected_mothers":len(chosen),"original_mothers":len(serial),
        "months":months,"net_effect":net,"diagnosis":diagnosis,"gates":gates,"parity":parity}
    write_json(folder/"summary.json",summary)
    return summary,trades,episodes,pairs,serial


def paired_mechanics(before, after):
    """All intentions and unknowns retained; outcomes describe, never filter."""
    fixed=["event_id","entry_time","entry_price","direction","initial_stop","signal_atr","risk_pct","risk_atr"]
    assert_saved_parity(before[fixed],after[fixed])
    b=before.set_index("event_id"); a=after.set_index("event_id").loc[b.index]
    known=b.closed&a.closed&np.isfinite(b.net_return)&np.isfinite(a.net_return)
    out=pd.DataFrame({"event_id":b.index,"mother_decision_time":b.mother_decision_time.to_numpy()})
    for arm,t in (("baseline",b),("candidate",a)):
        out[arm+"_net_bp"]=(t.net_return.where(known)*1e4).to_numpy()
        out[arm+"_exit_time"]=t.exit_time.to_numpy()
        out[arm+"_exit_reason"]=t.outcome.to_numpy()
        out[arm+"_mfe_r"]=t.max_favourable_r.to_numpy()
        out[arm+"_hold_minutes"]=t.hold_minutes.to_numpy()
    out["delta_net_bp"]=out.candidate_net_bp-out.baseline_net_bp
    out["exit_delay_minutes"]=(pd.to_datetime(out.candidate_exit_time,utc=True)-pd.to_datetime(out.baseline_exit_time,utc=True)).dt.total_seconds()/60
    out["outcome_transition"]="flat_or_unknown"
    for old,new,name in ((False,False,"loss_to_loss"),(False,True,"loss_to_win"),
                          (True,False,"win_to_loss"),(True,True,"win_to_win")):
        mask=known & b.net_return.ne(0) & a.net_return.ne(0) & b.net_return.gt(0).eq(old) & a.net_return.gt(0).eq(new)
        out.loc[mask.to_numpy(),"outcome_transition"]=name
    rows=[]
    for name,p in out.groupby("outcome_transition",sort=True):
        rows.append({"group":name,"n":len(p),"known":int(p.delta_net_bp.notna().sum()),
            "old_mean_net_bp":p.baseline_net_bp.mean(),"new_mean_net_bp":p.candidate_net_bp.mean(),
            "mean_delta_bp":p.delta_net_bp.mean(),"sum_delta_event_bp":p.delta_net_bp.sum(min_count=1)})
    return out,pd.DataFrame(rows),{"total":len(out),"known":int(known.sum()),
        "transitions":out.outcome_transition.value_counts().to_dict(),"groups":rows,
        "later_exits":int(out.exit_delay_minutes.gt(0).sum()),"earlier_exits":int(out.exit_delay_minutes.lt(0).sum()),
        "same_exit_time":int(out.exit_delay_minutes.eq(0).sum()),
        "interpretation":"Retrospective paired native management specification; no live causal or future entry-selection claim."}


def pin(directory, hashes):
    for name,expected in hashes.items():
        if digest(directory/name) != expected:
            raise ValueError("Pinned prior evidence changed: "+name)


def run():
    config_path=EXPERIMENT/"config.json"
    config=json.loads(config_path.read_text())
    base_path=ROOT/config["base_config"]
    if digest(base_path)!=BASE_SHA256: raise ValueError("Frozen base config hash changed")
    base=json.loads(base_path.read_text()); verify_config(config,base)
    sources=committed_sources([ROOT/p for p in SOURCES]+[config_path,base_path,EXPERIMENT/"PROJECT_PLAN.md"])
    pin(ROOT/MOTHERS,MOTHER_INPUTS); pin(ROOT/PARENT,CONTEXT_INPUTS)
    mothers={"case":read_frame(ROOT/MOTHERS/"original_mothers.csv.gz"),"control":read_frame(ROOT/MOTHERS/"control_mothers.csv.gz")}
    contexts={k:read_frame(ROOT/PARENT/f"direct_k1_stop_{k}_context.csv.gz") for k in mothers}
    assignments=pd.read_csv(ROOT/MOTHERS/"assignments.csv")
    validate_population(mothers,contexts,assignments)
    for frame in contexts.values(): validate_direct_context(frame)
    results=EXPERIMENT/"results"
    if results.exists(): raise ValueError("Preserve prior attempts; no overwrite")
    results.mkdir()
    write_json(results/"started.json",{"at":pd.Timestamp.now(tz="UTC"),"sources":sources,
        "builder_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()})
    try:
        study=Study(base,"development")
        assert_saved_parity(mothers["case"],study.entries(base["baseline"]))
        native={}; rows=[]
        for label in mothers:
            regenerated=attach_entry_colour_context(study.raw,study.featured(5,"SMA",40),direct_requests(mothers[label])[0])
            assert_saved_parity(contexts[label],regenerated)
            write_csv(results/f"{label}_context.csv.gz",contexts[label])
        for arm,policy in zip(("baseline","candidate"),POLICIES):
            native[arm]={}
            for label in mothers:
                frame=attach_native_exit_context(study.raw,study.featured(policy["management_minutes"],"SMA",40),contexts[label],policy["management_minutes"])
                native[arm][label]=frame
                rows.append(frame[["event_id","decision_time","direction"]+NATIVE_CONTEXT_COLUMNS].assign(arm=arm,population=label))
        observation=pd.concat(rows,ignore_index=True)
        write_csv(results/"native_entry_context.csv.gz",observation)
        write_csv(results/"assignments.csv",assignments)
        state_counts=observation.groupby(["arm","population","mg_entry_state"]).size().rename("n").reset_index()
        write_csv(results/"native_initial_state_counts.csv",state_counts)
        write_json(results/"context_frozen.json",{"at":pd.Timestamp.now(tz="UTC"),"before_outcome_reads":True,
            "rows":len(observation),"counts":state_counts.to_dict("records"),"context_sha256":digest(results/"native_entry_context.csv.gz"),
            "entry_gates":False,"outcomes_hashed_or_read":False})
        pin(ROOT/PARENT,OUTCOME_INPUTS)
        old=replay_arm(study,POLICIES[0],mothers,native["baseline"],results/"baseline",config,parent=ROOT/PARENT)
        write_json(results/"anchor_parity.json",old[0]["parity"])
        new=replay_arm(study,POLICIES[1],mothers,native["candidate"],results/"candidate",config)
        for label in mothers:
            fixed=list(contexts[label].columns)+["entry_time","entry_price","risk_pct","risk_atr"]
            assert_saved_parity(old[1][label][fixed],new[1][label][fixed])
        frames,effects=paired_effects(old[2]["case"],new[2]["case"],old[3],new[3],old[4],new[4])
        for name,frame in frames.items(): write_csv(results/(name+".csv"),frame)
        mechanics,groups,diagnosis=paired_mechanics(old[1]["case"],new[1]["case"])
        write_csv(results/"native_exit_mechanics.csv",mechanics); write_csv(results/"mechanism_groups.csv",groups)
        # Predeclared semantic diagnostic: native15 old opposite-state versus
        # true transition on this SAME population, never a third selected arm.
        semantics={}
        for label in mothers:
            state=simulate_native(study,native["candidate"][label],{**POLICIES[1],"exit_mode":"colour"})
            write_csv(results/f"semantic_state15_{label}_trades.csv.gz",state)
            joined,_,info=paired_mechanics(state,new[1][label])
            write_csv(results/f"semantic_state15_{label}_delta.csv",joined)
            semantics[label]={**info,"same_net":int(joined.delta_net_bp.abs().le(1e-8).sum()),
                "purpose":"V1-state semantic replication diagnostic only; not fresh evidence or parameter selection"}
        monthly=[]
        for name,episode in (("baseline",old[2]["case"]),("candidate",new[2]["case"])):
            part=episode.assign(month=episode.mother_decision_time.dt.strftime("%Y-%m"))
            for (fold,month),subset in part.groupby(["fold","month"]):
                monthly.append({"arm":name,"fold":fold,"month":month,"n":len(subset),"known":int(subset.observed.sum()),"mean_net_bp":subset.episode_net_return.mean()*1e4})
        write_csv(results/"monthly_case_net.csv",pd.DataFrame(monthly))
        gates={**new[0]["gates"],**{key:positive_inference(effects[key]) for key in ("case_delta","excess_delta")}}
        summary={"experiment_id":EXPERIMENT_ID,"status":"diagnostic_only_no_candidate_acceptance",
            "arms":{"baseline":old[0],"candidate":new[0]},"effects":effects,"mechanics":diagnosis,"semantics":semantics,
            "gates":gates,"all_financial_gates_pass":all(gates.values()),"known_coverage_ceiling":154/251,"coverage_required":.9,
            "native_context":state_counts.to_dict("records"),"source":study.source_receipt,"sources":sources,"config_sha256":digest(config_path),
            "audit_prices_loaded":False,"holdout_consumed":False,"production_eligible":False,"training_eligible":False,
            "inputs":INPUTS,"mother_inputs":MOTHER_INPUTS,
            "output_hashes":{str(p.relative_to(results)):digest(p) for p in sorted(results.rglob("*")) if p.is_file()}}
        write_json(results/"summary.json",summary)
        print(json.dumps({"status":summary["status"],"candidate_net_bp":new[0]["metrics"]["mean_net_bp"],
            "case_delta_bp":effects["case_delta"]["mean_bp"],"semantics":semantics}),flush=True)
    except Exception as error:
        write_json(results/"failure.json",{"at":pd.Timestamp.now(tz="UTC"),"type":type(error).__name__,"message":str(error)})
        raise


if __name__ == "__main__":
    run()

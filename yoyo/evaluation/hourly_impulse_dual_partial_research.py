"""V16: one qualified fast partial realization, unchanged native15 final exit.

Inputs: original hourly K1 intentions; completed native5/15 SMA40 HL2 bars
and their preceding39 contiguous bars. The fast edge can use the next5m OPEN,
never its later HLC. Initial contexts freeze before outcome-file access/replay.
No entry gate, stop movement, future-MFE selection, rematching or grid search.
All2023--2024 cases/own controls remain paired; development reuse is explicit.
Pandas one-to-one joins and UTC floor semantics are grounded in2.3.3 docs:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.merge.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Timestamp.floor.html
"""
from __future__ import annotations

from copy import deepcopy
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_colour_context import attach_entry_colour_context
from yoyo.data.hourly_impulse_native_exit_context import attach_native_exit_context, NATIVE_CONTEXT_COLUMNS
from yoyo.evaluation.hourly_impulse_context_research import committed_sources
from yoyo.evaluation.hourly_impulse_k2_research import direct_requests
from yoyo.evaluation.hourly_impulse_launch_research import (
    BASE_CONFIG, BASE_SHA256, MOTHERS, MOTHER_INPUTS, PARENT as ENTRY_CONTEXT_RESULTS,
    FOLDS, frozen_config as base_config, validate_population,
)
from yoyo.evaluation.hourly_impulse_management_research import assert_saved_parity, paired_effects, positive_inference
from yoyo.evaluation.hourly_impulse_native_exit_research import (
    CONTEXT_INPUTS, SOURCES as NATIVE_SOURCES, replay_arm, validate_direct_context,
    paired_mechanics, pin,
)
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, digest, utc, write_csv, write_json
from yoyo.evaluation.hourly_impulse_transition_research import read_frame
from yoyo.layers.l3_backtest.hourly_impulse import simulate_events

EXPERIMENT_ID = "exp-btcusdtp-1h-dual-partial-preholdout-20260906-v16"
EXPERIMENT = ROOT/"experiments/active"/EXPERIMENT_ID
PARENT = "experiments/active/exp-btcusdtp-1h-native15-exit-preholdout-20260906-v15/results/candidate"
INPUTS = {
    "case_episodes.csv.gz": "d904981d9db66662fabfcb101a0b782b56121169cddd406d4c4a29b18db48439",
    "case_trades.csv.gz": "772b2dc9333ee5f7e63ce92e7b3b2927b105ad4a0ba2f832093f09ad91df3ff0",
    "control_episodes.csv.gz": "5b41a046d32b06d0fdfc4843aef04a5eea12b97a7226f13850f88be6190c2468",
    "control_trades.csv.gz": "9512425350d47e8fa38ead12bf295be5eec8ed5f55ed124a34218b6b51cb9e97",
    "matched.csv": "20ed18c0eb4d48cfe21590a68689461c12c2a08e4b06adba3389a880f9d99122",
    "single_pending.csv.gz": "d817e8a5ccb7bf4aa95aa9e176bff26770ce9b5bd98ec2f63c44f4118a19dd37",
    "summary.json": "bc7813969875fca5b5205feeee2be97cba2837ce5e003dc610af84606a438f8e",
}
POLICIES = [
    {"id":"15m_native40", "management_minutes":15, "ma_kind":"SMA", "ma_length":40,
     "exit_mode":"transition_colour", "confirmations":1},
    {"id":"15m_native40_dual_partial", "management_minutes":15, "ma_kind":"SMA", "ma_length":40,
     "exit_mode":"transition_colour", "confirmations":1, "fast_partial_fraction":0.5},
]
SOURCES = list(dict.fromkeys(NATIVE_SOURCES+[
    "yoyo/evaluation/hourly_impulse_dual_partial_research.py",
    "tests/test_hourly_impulse_dual_partial.py", "tests/test_hourly_impulse_dual_partial_research.py",
]))


def frozen_config():
    config=deepcopy(base_config())
    config.update(experiment_id=EXPERIMENT_ID, parent_results=PARENT, inputs=INPUTS,
        entry_context_results=ENTRY_CONTEXT_RESULTS, entry_context_inputs=CONTEXT_INPUTS,
        policies=deepcopy(POLICIES), dual_partial_contract={
            "context_freeze_before_outcomes":True, "fast_minutes":5, "slow_minutes":15,
            "raw_stop_minutes":5, "fast_ma_kind":"SMA", "fast_ma_length":40,
            "slow_must_be_latest_completed_aligned":True, "true_fast_edge_only":True,
            "original_notional_fraction":0.5, "maximum_executions":1,
            "strict_open_gross_above":0.002, "cost_stress_changes_trigger":False,
            "entry_gates":False, "final_path_unchanged":True,
            "priority":["gap_invalid_open", "gap_stop", "slow_full_exit", "deadline",
                        "fast_partial_at_open", "current_intrabar_stop"],
            "future_mfe_never_a_fill":True, "incomplete_trade_remains_unknown":True})
    return config


def verify_config(config, base):
    if json.dumps(config,sort_keys=True)!=json.dumps(frozen_config(),sort_keys=True):
        raise ValueError("Frozen V16 qualified partial contract changed")
    if base["development_folds"]!=FOLDS:
        raise ValueError("Only frozen2023--2024 development is permitted")
    e=base["execution"]
    if e["max_hours"]!=72 or e["cost_fraction"]!=.002 or e["stop_first"] is not True:
        raise ValueError("Original stop-first/72h/20bp economics required")


def simulate_dual(study, entries, policy):
    """Fold-bounded native15 management plus opt-in native5 partial event stream."""
    management=study.featured(15,"SMA",40)
    kwargs={}
    if "fast_partial_fraction" in policy:
        kwargs["fast_management_featured"]=study.featured(5,"SMA",40)
    pieces=[]
    for fold,_,end in study.folds:
        part=entries.loc[entries.fold.eq(fold)]
        if len(part):
            pieces.append(simulate_events(study.raw,management,part,
                {**study.config["execution"],**policy},end_exclusive=utc(end),**kwargs))
    return pd.concat(pieces,ignore_index=True)


def assert_final_path(before, after):
    """Only weighted payoff may change; partial never changes final occupancy."""
    fixed=["event_id","entry_time","entry_price","direction","initial_stop","signal_atr",
           "risk_pct","risk_atr","exit_time","exit_price","outcome","hold_minutes","closed",
           "max_favourable_r","max_adverse_r"]
    fixed+= [c for c in before if c.startswith("transition_")]
    assert_saved_parity(before[fixed],after[fixed])
    return {"rows":len(before),"columns":len(fixed),"unchanged":True}


def partial_mechanics(before, after):
    """Post-outcome attribution on all intentions, not a deployable selection."""
    assert_final_path(before,after)
    out,groups,info=paired_mechanics(before,after)
    a=after.set_index("event_id").loc[out.event_id]
    for c in ("partial_fraction","partial_exit_time","realised_partial_gross_return","exit_remaining_fraction"):
        out[c]=a[c].to_numpy()
    out["partial_net_contribution_bp"]=(a.realised_partial_gross_return-.002*a.partial_fraction).to_numpy()*1e4
    out["partial_executed"]=out.partial_fraction.eq(.5)
    known=out.delta_net_bp.notna()
    if (known&out.baseline_net_bp.gt(0)&out.candidate_net_bp.le(0)).any():
        raise ValueError("Positive half realization cannot turn a20bp winner into a loss")
    if (known&out.partial_executed&out.baseline_net_bp.lt(0)&out.delta_net_bp.le(0)).any():
        raise ValueError("Positive half realization must improve a20bp losing path")
    info.update(partial_count=int(out.partial_executed.sum()),
        unmodified_count=int((~out.partial_executed).sum()),
        partial_improved=int((out.partial_executed&out.delta_net_bp.gt(1e-8)).sum()),
        partial_hurt=int((out.partial_executed&out.delta_net_bp.lt(-1e-8)).sum()),
        interpretation="Retrospective qualified fast partial realization; unchanged slow final path; no independent or live profitability claim.")
    return out,groups,info


def assert_fast_initial(context, trades):
    """Independent completed-native5 context agrees with the engine seed."""
    c=context.set_index("event_id"); t=trades.set_index("event_id").loc[c.index]
    valid=t.risk_pct.gt(0)&t.risk_atr.gt(0)
    c=c.loc[valid]; t=t.loc[valid]
    for left,right in (("mg_entry_state","partial_fast_initial_state"),
                       ("mg_entry_reason","partial_fast_initial_reason")):
        if not c[left].eq(t[right]).all():
            raise ValueError("Fast initial context/engine mismatch: "+left)
    known=c.mg_entry_known
    for left,right in (("mg_entry_side","partial_fast_initial_side"),
                       ("mg_entry_ma","partial_fast_initial_ma"),
                       ("mg_entry_hl2","partial_fast_initial_hl2"),
                       ("mg_entry_management_segment_id","partial_fast_initial_management_segment_id"),
                       ("mg_entry_raw_segment_id","partial_fast_initial_raw_segment_id")):
        np.testing.assert_allclose(c.loc[known,left].astype(float),t.loc[known,right].astype(float),rtol=1e-12,atol=1e-12)
    for left,right in (("mg_entry_bar_open","partial_fast_initial_open_time"),
                       ("mg_entry_available_at","partial_fast_initial_available_at")):
        a=pd.to_datetime(c.loc[known,left],utc=True)
        b=pd.to_datetime(t.loc[known,right],utc=True)
        if not a.eq(b).all(): raise ValueError("Fast initial bar clock mismatch")


def run():
    config_path=EXPERIMENT/"config.json"
    config=json.loads(config_path.read_text()); base_path=ROOT/BASE_CONFIG
    if digest(base_path)!=BASE_SHA256: raise ValueError("Frozen base config changed")
    base=json.loads(base_path.read_text()); verify_config(config,base)
    sources=committed_sources([ROOT/p for p in SOURCES]+[config_path,base_path,EXPERIMENT/"PROJECT_PLAN.md"])
    pin(ROOT/MOTHERS,MOTHER_INPUTS); pin(ROOT/ENTRY_CONTEXT_RESULTS,CONTEXT_INPUTS)
    mothers={k:read_frame(ROOT/MOTHERS/name) for k,name in
             (("case","original_mothers.csv.gz"),("control","control_mothers.csv.gz"))}
    contexts={k:read_frame(ROOT/ENTRY_CONTEXT_RESULTS/f"direct_k1_stop_{k}_context.csv.gz") for k in mothers}
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
        native={}; rows=[]; fast_rows=[]
        for label in mothers:
            fast=attach_entry_colour_context(study.raw,study.featured(5,"SMA",40),direct_requests(mothers[label])[0])
            assert_saved_parity(contexts[label],fast)
            write_csv(results/f"{label}_context.csv.gz",contexts[label])
            native[label]=attach_native_exit_context(study.raw,study.featured(15,"SMA",40),contexts[label],15)
            fast=attach_native_exit_context(study.raw,study.featured(5,"SMA",40),contexts[label],5)
            fast_rows.append(fast[["event_id","decision_time","direction"]+NATIVE_CONTEXT_COLUMNS].assign(population=label))
            for arm in ("baseline","candidate"):
                rows.append(native[label][["event_id","decision_time","direction"]+NATIVE_CONTEXT_COLUMNS].assign(arm=arm,population=label))
        observation=pd.concat(rows,ignore_index=True)
        write_csv(results/"native_entry_context.csv.gz",observation)
        write_csv(results/"fast_entry_context.csv.gz",pd.concat(fast_rows,ignore_index=True))
        write_csv(results/"assignments.csv",assignments)
        counts=observation.groupby(["arm","population","mg_entry_state"]).size().rename("n").reset_index()
        write_csv(results/"native_initial_state_counts.csv",counts)
        write_json(results/"context_frozen.json",{"at":pd.Timestamp.now(tz="UTC"),"before_outcome_reads":True,
            "rows":len(observation),"fast_rows":sum(map(len,fast_rows)),"counts":counts.to_dict("records"),
            "context_sha256":digest(results/"native_entry_context.csv.gz"),
            "fast_context_sha256":digest(results/"fast_entry_context.csv.gz"),
            "entry_gates":False,"outcomes_hashed_or_read":False})
        pin(ROOT/PARENT,INPUTS)
        old=replay_arm(study,POLICIES[0],mothers,native,results/"baseline",config,
                       parent=ROOT/PARENT,parent_prefix="",simulator=simulate_dual)
        write_json(results/"anchor_parity.json",old[0]["parity"])
        new=replay_arm(study,POLICIES[1],mothers,native,results/"candidate",config,simulator=simulate_dual)
        for label,frame in zip(mothers,fast_rows): assert_fast_initial(frame,new[1][label])
        path_parity={k:assert_final_path(old[1][k],new[1][k]) for k in mothers}
        # Portfolio occupancy uses the same final exits; every source intention stays.
        serial_fixed=[c for c in old[4] if c not in ("portfolio_return","episode_net_return")]
        assert_saved_parity(old[4][serial_fixed],new[4][serial_fixed])
        frames,effects=paired_effects(old[2]["case"],new[2]["case"],old[3],new[3],old[4],new[4])
        for name,frame in frames.items(): write_csv(results/(name+".csv"),frame)
        info={}; edge_rows=[]
        for label in mothers:
            mechanics,groups,info[label]=partial_mechanics(old[1][label],new[1][label])
            write_csv(results/f"partial_{label}_mechanics.csv",mechanics)
            write_csv(results/f"partial_{label}_groups.csv",groups)
            for row in new[1][label].to_dict("records"):
                for edge in json.loads(row["partial_fast_events"]):
                    edge_rows.append({"event_id":row["event_id"],"population":label,**edge})
        edge_columns=["event_id","population","available_at","open_price","gross_return",
            "profit_threshold","profit_qualified","action","previous_fast","current_fast",
            "slow","slow_available_at","slow_state","slow_reason"]
        edges=pd.DataFrame(edge_rows,columns=edge_columns)
        for column in ("previous_fast","current_fast","slow"):
            edges[column]=edges[column].map(lambda x:json.dumps(x,sort_keys=True,allow_nan=False))
        write_csv(results/"partial_fast_edges.csv.gz",edges)
        monthly=[]
        for name,episode in (("baseline",old[2]["case"]),("candidate",new[2]["case"])):
            for (fold,month),part in episode.assign(month=episode.mother_decision_time.dt.strftime("%Y-%m")).groupby(["fold","month"]):
                monthly.append({"arm":name,"fold":fold,"month":month,"n":len(part),"known":int(part.observed.sum()),"mean_net_bp":part.episode_net_return.mean()*1e4})
        write_csv(results/"monthly_case_net.csv",pd.DataFrame(monthly))
        gates={**new[0]["gates"],**{k:positive_inference(effects[k]) for k in ("case_delta","excess_delta")}}
        summary={"experiment_id":EXPERIMENT_ID,"status":"diagnostic_only_no_candidate_acceptance",
            "arms":{"baseline":old[0],"candidate":new[0]},"effects":effects,"mechanics":info,
            "final_path_parity":path_parity,"gates":gates,"all_financial_gates_pass":all(gates.values()),
            "known_coverage_ceiling":154/251,"coverage_required":.9,"native_context":counts.to_dict("records"),
            "source":study.source_receipt,"sources":sources,"config_sha256":digest(config_path),
            "audit_prices_loaded":False,"holdout_consumed":False,"production_eligible":False,"training_eligible":False,
            "inputs":INPUTS,"mother_inputs":MOTHER_INPUTS,"entry_context_inputs":CONTEXT_INPUTS,
            "output_hashes":{str(p.relative_to(results)):digest(p) for p in sorted(results.rglob("*")) if p.is_file()}}
        write_json(results/"summary.json",summary)
        print(json.dumps({"status":summary["status"],"candidate_net_bp":new[0]["metrics"]["mean_net_bp"],
            "case_delta_bp":effects["case_delta"]["mean_bp"],"mechanics":info}),flush=True)
    except Exception as error:
        write_json(results/"failure.json",{"type":type(error).__name__,"message":str(error)})
        raise


if __name__=="__main__":
    run()

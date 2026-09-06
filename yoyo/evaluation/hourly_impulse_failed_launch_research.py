"""V17: complement V16's profitable half with an unprofitable fast full exit.

Inputs are the original hourly K1 intentions and completed native5/15 SMA40
HL2 observations (current completed bar and preceding39 contiguous bars).
Only the next raw5 OPEN may qualify a fill, never its later HLC or future MFE.
This is not a time-limited failed-launch detector: a late profit giveback may
also meet the same current-open economic condition. All original intentions
and own controls remain, with per-arm serial occupancy recomputed.

Pandas2.3 one-to-one identity joins and Decimal quote-price boundary semantics:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.merge.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.read_csv.html
https://docs.python.org/3.9/library/decimal.html#decimal.Decimal
Inference reuses the predeclared calendar-month resampling, not random splits:
https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.choice.html
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
from yoyo.evaluation.hourly_impulse_dual_partial_research import (
    BASE_CONFIG, BASE_SHA256, MOTHERS, MOTHER_INPUTS, ENTRY_CONTEXT_RESULTS,
    CONTEXT_INPUTS, FOLDS, SOURCES as PARTIAL_SOURCES, frozen_config as partial_config,
    simulate_dual, assert_fast_initial,
)
from yoyo.evaluation.hourly_impulse_k2_research import direct_requests
from yoyo.evaluation.hourly_impulse_launch_research import validate_population
from yoyo.evaluation.hourly_impulse_management_research import assert_saved_parity, paired_effects, positive_inference
from yoyo.evaluation.hourly_impulse_native_exit_research import replay_arm, validate_direct_context, paired_mechanics, pin
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, digest, write_csv, write_json
from yoyo.evaluation.hourly_impulse_transition_research import read_frame

EXPERIMENT_ID = "exp-btcusdtp-1h-failed-launch-preholdout-20260906-v17"
EXPERIMENT = ROOT/"experiments/active"/EXPERIMENT_ID
PARENT = "experiments/active/exp-btcusdtp-1h-dual-partial-preholdout-20260906-v16/results/candidate"
INPUTS = {
    "case_episodes.csv.gz": "0528402bb7dad51eb5a0ea8a196863e20f0fc70bc4a57c7adfe79fde1cfdfbe1",
    "case_trades.csv.gz": "9a43891b81bf50c281db3a26d1e53188f6b7876c746b75509ae78a570826bc47",
    "control_episodes.csv.gz": "8a995534d98058a2efa241e9ebbd8dbb5169204790bb727ff78765364ea62847",
    "control_trades.csv.gz": "6fc2975228852fe8afbbd62148cec64354f0cfd025c29d7cc49be0ef7e842c91",
    "matched.csv": "a0a29d07d4bec5aa75b40d976ad6bbdba2e6810f6cf4d4736896f228b98a54c6",
    "single_pending.csv.gz": "2cfe1214c565b07b38b4c302224d086bfdb5bb6a9706633f27d6d79d1bfb01b0",
    "summary.json": "1494ea21905bc84071892940d3de294444c79a5a18fc5f1a9e321375dd9028f0",
}
POLICIES = [
    {"id":"15m_native40_dual_partial", "management_minutes":15, "ma_kind":"SMA", "ma_length":40,
     "exit_mode":"transition_colour", "confirmations":1, "fast_partial_fraction":0.5},
    {"id":"15m_native40_failed_launch", "management_minutes":15, "ma_kind":"SMA", "ma_length":40,
     "exit_mode":"transition_colour", "confirmations":1, "fast_partial_fraction":0.5,
     "fast_failed_launch_exit":True},
]
SOURCES = list(dict.fromkeys(PARTIAL_SOURCES+[
    "yoyo/evaluation/hourly_impulse_failed_launch_research.py",
    "tests/test_hourly_impulse_failed_launch.py", "tests/test_hourly_impulse_failed_launch_research.py",
]))


def frozen_config():
    config=deepcopy(partial_config())
    config.pop("dual_partial_contract")
    config.update(experiment_id=EXPERIMENT_ID,parent_results=PARENT,inputs=INPUTS,
        policies=deepcopy(POLICIES),failed_launch_contract={
            "context_freeze_before_outcomes":True,"fast_minutes":5,"slow_minutes":15,
            "raw_stop_minutes":5,"ma_kind":"SMA","ma_length":40,
            "true_fast_edge_only":True,"slow_must_be_latest_completed_aligned":True,
            "before_partial_only":True,"full_exit_if_open_gross_not_above":0.002,
            "profitable_partial_fraction":0.5,"maximum_partial_executions":1,
            "cost_stress_changes_trigger":False,"entry_gates":False,
            "time_limited_launch_claim":False,"final_path_unchanged":False,
            "serial_recomputed_for_each_arm":True,
            "priority":["gap_invalid_open","gap_stop","slow_full_exit","deadline",
                        "fast_full_or_partial_at_open","current_intrabar_stop"],
            "future_mfe_never_a_fill":True,"incomplete_trade_remains_unknown":True})
    return config


def verify_config(config, base):
    if json.dumps(config,sort_keys=True)!=json.dumps(frozen_config(),sort_keys=True):
        raise ValueError("Frozen V17 fast full-exit switch changed")
    e=base["execution"]
    if base["development_folds"]!=FOLDS or e["max_hours"]!=72 or e["cost_fraction"]!=.002 or e["stop_first"] is not True:
        raise ValueError("Only original2023--2024,stop-first/72h/20bp permitted")


def read_parent_frame(path):
    """Preserve two opaque source-ID lexemes before CSV inference, not after.

    The V16 engine emits these source identities as strings. Numeric-looking
    IDs and literal 'nan' are identities, not numbers/missing data. All other
    fields retain the original read_frame contract; strict parity is unchanged.
    """
    names=("partial_fast_initial_management_segment_id", "partial_fast_initial_raw_segment_id")
    frame=pd.read_csv(path,converters={name:lambda value:value for name in names})
    for column in frame:
        if column.endswith(("_time","_deadline")):
            frame[column]=pd.to_datetime(frame[column],utc=True,format="mixed")
    return frame


def failed_launch_mechanics(before, after):
    """Retrospective all-intention attribution, including sacrificed recoveries.

    No-trigger paths must reproduce every old field. Triggered paths have no
    partial fill and may only close earlier; a finite fast full exit is allowed
    to precede an otherwise censored remainder without imputing the old return.
    Outcome-conditioned groups below never select original entries.
    """
    out,groups,info=paired_mechanics(before,after)
    b=before.set_index("event_id").loc[out.event_id]
    a=after.set_index("event_id").loc[out.event_id]
    failed=a.outcome.eq("fast_failed_launch")
    if not a.failed_launch_count.eq(failed.astype(int)).all():
        raise ValueError("Failed-launch count/outcome mismatch")
    columns=list(before.columns)
    assert_saved_parity(b.loc[~failed].reset_index()[columns],a.loc[~failed].reset_index()[columns])
    if failed.any():
        f=a.loc[failed]
        if not (f.closed & f.partial_fraction.eq(0) & f.exit_remaining_fraction.eq(1)
                & f.realised_partial_gross_return.eq(0) & f.partial_fast_fill_count.eq(0)).all():
            raise ValueError("Fast full exit cannot include a partial position")
        if not (f.net_return.le(1e-12)&f.net_return.notna()).all():
            raise ValueError("At20bp a failed economics full exit cannot have positive net")
        early=pd.to_datetime(f.exit_time,utc=True)
        old_end=pd.to_datetime(b.loc[failed,"exit_time"],utc=True)
        if early.isna().any() or old_end.isna().any() or early.gt(old_end).any():
            raise ValueError("Fast failed exit cannot occur after the unchanged slow path")
    out["failed_launch_executed"]=failed.to_numpy()
    for arm,t in (("baseline",b),("candidate",a)):
        out[arm+"_partial_executed"]=t.partial_fraction.eq(.5).to_numpy()
        out[arm+"_partial_exit_time"]=t.partial_exit_time.to_numpy()
    out["sacrificed_recovery"]=failed.to_numpy()&out.baseline_net_bp.gt(0)&out.candidate_net_bp.le(1e-8)
    out["prior_partial_path_cut"]=failed.to_numpy()&out.baseline_partial_executed
    known=out.delta_net_bp.notna()
    info.update(failed_launch_count=int(failed.sum()),unchanged_paths=int((~failed).sum()),
        failed_improved=int((failed.to_numpy()&out.delta_net_bp.gt(1e-8)).sum()),
        failed_hurt=int((failed.to_numpy()&out.delta_net_bp.lt(-1e-8)).sum()),
        failed_unknown_pairs=int((failed.to_numpy()&~known).sum()),
        sacrificed_recoveries=int(out.sacrificed_recovery.sum()),
        prior_partial_paths_cut=int(out.prior_partial_path_cut.sum()),
        baseline_partial_count=int(out.baseline_partial_executed.sum()),
        candidate_partial_count=int(out.candidate_partial_executed.sum()),
        interpretation="Retrospective before-partial fast economic exit; can occur late or after prior profit; not an entry filter or live efficacy claim.")
    return out,groups,info


def export_edges(arms):
    """Flatten both arms' evaluated edges; keep full source objects as JSON."""
    rows=[]
    for arm,trades in arms.items():
        for label,table in trades.items():
            for row in table.to_dict("records"):
                for edge in json.loads(row["partial_fast_events"]):
                    rows.append({"arm":arm,"population":label,"event_id":row["event_id"],**edge})
    columns=["arm","population","event_id","available_at","open_price","gross_return",
        "profit_threshold","profit_qualified","action","previous_fast","current_fast",
        "slow","slow_available_at","slow_state","slow_reason"]
    frame=pd.DataFrame(rows,columns=columns)
    for column in ("previous_fast","current_fast","slow"):
        frame[column]=frame[column].map(lambda x:json.dumps(x,sort_keys=True,allow_nan=False))
    return frame


def run():
    config_path=EXPERIMENT/"config.json";config=json.loads(config_path.read_text())
    base_path=ROOT/BASE_CONFIG
    if digest(base_path)!=BASE_SHA256: raise ValueError("Frozen base config changed")
    base=json.loads(base_path.read_text());verify_config(config,base)
    sources=committed_sources([ROOT/p for p in SOURCES]+[config_path,base_path,EXPERIMENT/"PROJECT_PLAN.md"])
    pin(ROOT/MOTHERS,MOTHER_INPUTS);pin(ROOT/ENTRY_CONTEXT_RESULTS,CONTEXT_INPUTS)
    mothers={k:read_frame(ROOT/MOTHERS/name) for k,name in
        (("case","original_mothers.csv.gz"),("control","control_mothers.csv.gz"))}
    contexts={k:read_frame(ROOT/ENTRY_CONTEXT_RESULTS/f"direct_k1_stop_{k}_context.csv.gz") for k in mothers}
    assignments=pd.read_csv(ROOT/MOTHERS/"assignments.csv")
    validate_population(mothers,contexts,assignments)
    for frame in contexts.values():validate_direct_context(frame)
    results=EXPERIMENT/"results"
    if results.exists():raise ValueError("Preserve prior attempts; no overwrite")
    results.mkdir()
    write_json(results/"started.json",{"at":pd.Timestamp.now(tz="UTC"),"sources":sources,
        "builder_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()})
    try:
        study=Study(base,"development")
        assert_saved_parity(mothers["case"],study.entries(base["baseline"]))
        native={};rows=[];fast_rows=[]
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
            parent=ROOT/PARENT,parent_prefix="",simulator=simulate_dual,saved_reader=read_parent_frame)
        write_json(results/"anchor_parity.json",old[0]["parity"])
        new=replay_arm(study,POLICIES[1],mothers,native,results/"candidate",config,simulator=simulate_dual)
        for arm in (old,new):
            for label,frame in zip(mothers,fast_rows):assert_fast_initial(frame,arm[1][label])
        info={}
        for label in mothers:
            mechanics,groups,info[label]=failed_launch_mechanics(old[1][label],new[1][label])
            write_csv(results/f"failed_launch_{label}_mechanics.csv",mechanics)
            write_csv(results/f"failed_launch_{label}_groups.csv",groups)
        write_csv(results/"fast_edges.csv.gz",export_edges({"baseline":old[1],"candidate":new[1]}))
        frames,effects=paired_effects(old[2]["case"],new[2]["case"],old[3],new[3],old[4],new[4])
        for name,frame in frames.items():write_csv(results/(name+".csv"),frame)
        monthly=[]
        for name,episode in (("baseline",old[2]["case"]),("candidate",new[2]["case"])):
            for (fold,month),part in episode.assign(month=episode.mother_decision_time.dt.strftime("%Y-%m")).groupby(["fold","month"]):
                monthly.append({"arm":name,"fold":fold,"month":month,"n":len(part),
                    "known":int(part.observed.sum()),"mean_net_bp":part.episode_net_return.mean()*1e4})
        write_csv(results/"monthly_case_net.csv",pd.DataFrame(monthly))
        gates={**new[0]["gates"],**{k:positive_inference(effects[k]) for k in ("case_delta","excess_delta")}}
        summary={"experiment_id":EXPERIMENT_ID,"status":"diagnostic_only_no_candidate_acceptance",
            "arms":{"baseline":old[0],"candidate":new[0]},"effects":effects,"mechanics":info,
            "gates":gates,"all_financial_gates_pass":all(gates.values()),
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


if __name__=="__main__":run()

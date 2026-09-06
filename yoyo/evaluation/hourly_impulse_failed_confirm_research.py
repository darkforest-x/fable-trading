"""V18: adjacent two-bar confirmation for the unprofitable fast full exit.

Same original intentions, completed native5/native15 SMA40(HL2) contexts and
20bp/72h/K1-stop contract as V17. Only a pending failed full action waits for
the immediately next completed native5 bar. Profitable real edges still take
50% immediately; cancellation never creates a synthetic partial or new edge.
Sources: pandas2.3.3 one-to-one joins and lexical CSV identity preservation:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_csv.html
https://docs.python.org/3.9/library/decimal.html#decimal.Decimal
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
    CONTEXT_INPUTS, FOLDS,
    simulate_dual, assert_fast_initial,
)
from yoyo.evaluation.hourly_impulse_k2_research import direct_requests
from yoyo.evaluation.hourly_impulse_launch_research import validate_population
from yoyo.evaluation.hourly_impulse_management_research import assert_saved_parity, paired_effects, positive_inference
from yoyo.evaluation.hourly_impulse_native_exit_research import replay_arm, validate_direct_context, paired_mechanics, pin
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, digest, write_csv, write_json
from yoyo.evaluation.hourly_impulse_transition_research import read_frame

EXPERIMENT_ID = "exp-btcusdtp-1h-failed-confirm-preholdout-20260906-v18"
EXPERIMENT = ROOT/"experiments/active"/EXPERIMENT_ID
PARENT = "experiments/active/exp-btcusdtp-1h-failed-launch-preholdout-20260906-v17/results/candidate"
INPUTS = {
    "case_episodes.csv.gz": "b323f68376b916c5dd24740218831557cc4e61dde01a677c37fa56986f76b348",
    "case_trades.csv.gz": "160e5c503fe67e98bb016ab83f94d376f78eb6c270f9ed85422de5000df92b51",
    "control_episodes.csv.gz": "801b34e51d1a197fdd6ff15f8c2605f0d06707475bfa70710dcddd9578333ace",
    "control_trades.csv.gz": "1aa4d13860ffd97ac240b0935e4196a9564153e2d4d382437e1b07c340f89e2b",
    "matched.csv": "e6225f2ab6d9c55e73e4ab77ba2580a0122b7bc4b285bf9187b397a92858a493",
    "single_pending.csv.gz": "b3f29cae7c5bf7d1bbd37c1a37adaf7f556fec56fb3f9f0fd2d2d7dd91a4dbb7",
    "summary.json": "bdd099aeadcb841223afb4eb999322198dc3f1deef69a6706180f6368dd7aa50"
}
POLICIES = [
    {"id":"15m_native40_failed_launch", "management_minutes":15, "ma_kind":"SMA", "ma_length":40,
     "exit_mode":"transition_colour", "confirmations":1, "fast_partial_fraction":0.5,
     "fast_failed_launch_exit":True},
    {"id":"15m_native40_failed_confirm2", "management_minutes":15, "ma_kind":"SMA", "ma_length":40,
     "exit_mode":"transition_colour", "confirmations":1, "fast_partial_fraction":0.5,
     "fast_failed_launch_exit":True, "fast_failed_launch_confirmations":2},
]
from yoyo.evaluation.hourly_impulse_failed_launch_research import (
    SOURCES as FAILED_SOURCES, frozen_config as failed_config, read_parent_frame,
    export_edges,
)

SOURCES = list(dict.fromkeys(FAILED_SOURCES+[
    "yoyo/evaluation/hourly_impulse_failed_confirm_research.py",
    "tests/test_hourly_impulse_failed_confirm.py", "tests/test_hourly_impulse_failed_confirm_research.py",
]))


def frozen_config():
    config=deepcopy(failed_config())
    config.pop("failed_launch_contract")
    config.update(experiment_id=EXPERIMENT_ID,parent_results=PARENT,inputs=INPUTS,
        policies=deepcopy(POLICIES),failed_confirm_contract={
            "context_freeze_before_outcomes":True,"fast_minutes":5,"slow_minutes":15,
            "raw_stop_minutes":5,"ma_kind":"SMA","ma_length":40,
            "first_true_fast_edge_only":True,"next_adjacent_confirmation_only":True,
            "confirmation_count":2,"same_valid_source_segments":True,
            "latest_completed_slow_rechecked_at_confirmation":True,
            "before_partial_only":True,"full_exit_if_open_gross_not_above":0.002,
            "profitable_partial_fraction":0.5,"profitable_real_edge_not_delayed":True,
            "cancelled_edge_consumed":True,"no_partial_on_confirmation_bar":True,
            "fresh_alignment_then_edge_required_to_rearm":True,
            "confirmation_is_not_a_flip":True,"first_trigger_fields_preserved":True,
            "cost_stress_changes_trigger":False,"entry_gates":False,
            "serial_recomputed_for_each_arm":True,
            "priority":["gap_invalid_open","gap_stop","slow_full_exit","deadline",
                        "pending_confirmation_or_fast_edge_at_open","current_intrabar_stop"],
            "future_mfe_never_a_fill":True,"incomplete_trade_remains_unknown":True,
            "price_prefix_before":"2025-01-01T00:00:00Z",
            "pre2023_feature_warmup_included":True})
    return config


def verify_config(config, base):
    if json.dumps(config,sort_keys=True)!=json.dumps(frozen_config(),sort_keys=True):
        raise ValueError("Frozen V18 adjacent fast-full confirmation changed")
    e=base["execution"]
    if base["development_folds"]!=FOLDS or e["max_hours"]!=72 or e["cost_fraction"]!=.002 or e["stop_first"] is not True:
        raise ValueError("Only original2023--2024,stop-first/72h/20bp permitted")


def confirmed_mechanics(before, after):
    """All-intention paired diagnosis; no future recovery selects an entry.

    The unchanged V17 path ends at the first pending creation when it had a
    fast full exit. V18 can delay, cancel, rearm, realize a later true partial,
    stop, or become unknown. Only old non-full paths retain all old columns.
    """
    out,groups,info=paired_mechanics(before,after)
    b=before.set_index("event_id").loc[out.event_id]
    a=after.set_index("event_id").loc[out.event_id]
    oldfull=b.outcome.eq("fast_failed_launch")
    newfull=a.outcome.eq("fast_failed_launch")
    columns=list(before.columns)
    assert_saved_parity(b.loc[~oldfull].reset_index()[columns],a.loc[~oldfull].reset_index()[columns])
    if (newfull & ~oldfull).any():
        raise ValueError("Confirmation full must belong to an old full path")
    if not a.failed_confirm_create_count.gt(0).eq(oldfull).all():
        raise ValueError("Pending population must equal the old full population")
    if not a.failed_confirm_confirm_count.eq(newfull.astype(int)).all():
        raise ValueError("Confirmation count/outcome mismatch")
    for event in b.index[oldfull]:
        events=json.loads(a.loc[event,"failed_confirm_events"])
        creates=[x for x in events if x["action"]=="created"]
        if not creates or pd.Timestamp(creates[0]["created_at"])!=pd.Timestamp(b.loc[event,"exit_time"]):
            raise ValueError("First pending must equal original full-exit clock")
    closed=oldfull & a.closed
    if (pd.to_datetime(a.loc[closed,"exit_time"],utc=True) <
        pd.to_datetime(b.loc[closed,"exit_time"],utc=True)+pd.Timedelta(minutes=5)).any():
        raise ValueError("Known changed exit must wait at least one full raw5 interval")
    if newfull.any():
        f=a.loc[newfull]
        if not (f.closed & f.partial_fraction.eq(0) & f.exit_remaining_fraction.eq(1)
                & f.realised_partial_gross_return.eq(0) & f.partial_fast_fill_count.eq(0)).all():
            raise ValueError("Confirmed full cannot include a partial position")
        if not (f.net_return.le(1e-12)&f.net_return.notna()).all():
            raise ValueError("Confirmed full cannot have positive20bp net")
    out["baseline_failed_full"]=oldfull.to_numpy()
    out["candidate_confirmed_full"]=newfull.to_numpy()
    out["candidate_partial_executed"]=a.partial_fraction.eq(.5).to_numpy()
    out["candidate_pending_created"]=a.failed_confirm_create_count.to_numpy()
    out["candidate_pending_cancelled"]=a.failed_confirm_cancel_count.to_numpy()
    out["candidate_pending_terminated"]=a.failed_confirm_priority_termination_count.to_numpy()
    out["recovered_winner"]=oldfull.to_numpy()&out.candidate_net_bp.gt(0)
    out["newly_unknown"]=b.closed.to_numpy()&~a.closed.to_numpy()
    known=out.delta_net_bp.notna()
    info.update(baseline_failed_full_count=int(oldfull.sum()),candidate_confirmed_full_count=int(newfull.sum()),
        unchanged_paths=int((~oldfull).sum()),pending_events=int(a.failed_confirm_create_count.sum()),
        cancelled_pending_events=int(a.failed_confirm_cancel_count.sum()),
        priority_terminated_pending_events=int(a.failed_confirm_priority_termination_count.sum()),
        changed_improved=int((oldfull.to_numpy()&out.delta_net_bp.gt(1e-8)).sum()),
        changed_hurt=int((oldfull.to_numpy()&out.delta_net_bp.lt(-1e-8)).sum()),
        changed_unknown_pairs=int((oldfull.to_numpy()&~known).sum()),
        recovered_winners=int(out.recovered_winner.sum()),newly_unknown=int(out.newly_unknown.sum()),
        restored_partial_paths=int((oldfull.to_numpy()&out.candidate_partial_executed).sum()),
        baseline_partial_count=int(b.partial_fraction.eq(.5).sum()),
        candidate_partial_count=int(a.partial_fraction.eq(.5).sum()),
        interpretation="Retrospective all-intention confirmation effects, not a future entry filter or independently validated edge.")
    return out,groups,info


def export_confirmations(arms):
    """Lossless separate pending log; opposite-to-opposite is never a flip."""
    rows=[]
    for arm,trades in arms.items():
        for label,table in trades.items():
            for row in table.to_dict("records"):
                for event in json.loads(row.get("failed_confirm_events","[]")):
                    rows.append({"arm":arm,"population":label,"event_id":row["event_id"],
                        "action":event["action"],"evidence_json":json.dumps(event,sort_keys=True,allow_nan=False)})
    return pd.DataFrame(rows,columns=["arm","population","event_id","action","evidence_json"])


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
            mechanics,groups,info[label]=confirmed_mechanics(old[1][label],new[1][label])
            write_csv(results/f"confirmed_{label}_mechanics.csv",mechanics)
            write_csv(results/f"confirmed_{label}_groups.csv",groups)
        write_csv(results/"fast_edges.csv.gz",export_edges({"baseline":old[1],"candidate":new[1]}))
        write_csv(results/"confirmation_events.csv.gz",export_confirmations({"candidate":new[1]}))
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

"""V19: confirmed risk reduction instead of unprofitable full liquidation.

Same original intentions, completed native5/native15 SMA40(HL2) contexts and
20bp/72h/K1-stop contract as V18. Only the confirmed execution fraction changes
from original-notional100% to50%; losing reduction is not TP. Profitable real
edges stay unchanged. A censored remainder keeps whole-trade PnL unknown.
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

EXPERIMENT_ID = "exp-btcusdtp-1h-failed-reduce-preholdout-20260906-v19"
EXPERIMENT = ROOT/"experiments/active"/EXPERIMENT_ID
PARENT = "experiments/active/exp-btcusdtp-1h-failed-confirm-preholdout-20260906-v18/results/candidate"
INPUTS = {
    "case_episodes.csv.gz": "f1d6d8c29af2c78f4fe0a3c79560b1ac9e21062202d3ac1b462b640463ad8e02",
    "case_trades.csv.gz": "f8b5009e58b5f098004c7ea5c3e8a65cfe5be135459f3f0b55f3fae83f903b9e",
    "control_episodes.csv.gz": "cdc677b08fab6185d2be363e871fe2f7cce0f5d72cdec52196e7c61ff52282e0",
    "control_trades.csv.gz": "8d279aa8ffb21a3de01f090845fc9624732fb8a2158f69cb65237178e3f46af5",
    "matched.csv": "3604757c56daee054c3caed6fe9dbf28018c72a73c0faf151de3c97c76b60a8b",
    "single_pending.csv.gz": "c72d429fbd2193fa107d4335be835c37909596aedbc0184c50acababe23cd1ab",
    "summary.json": "a1506379446a1f8bfbb3ed4017f2ad5d85a55100b6a1f67647546cb9e1b15226"
}
POLICIES = [
    {"id":"15m_native40_failed_confirm2", "management_minutes":15, "ma_kind":"SMA", "ma_length":40,
     "exit_mode":"transition_colour", "confirmations":1, "fast_partial_fraction":0.5,
     "fast_failed_launch_exit":True, "fast_failed_launch_confirmations":2},
    {"id":"15m_native40_failed_reduce_half", "management_minutes":15, "ma_kind":"SMA", "ma_length":40,
     "exit_mode":"transition_colour", "confirmations":1, "fast_partial_fraction":0.5,
     "fast_failed_launch_exit":True, "fast_failed_launch_confirmations":2, "fast_failed_launch_fraction":0.5},
]
from yoyo.evaluation.hourly_impulse_failed_launch_research import (
    read_parent_frame, export_edges,
)
from yoyo.evaluation.hourly_impulse_failed_confirm_research import (
    SOURCES as CONFIRM_SOURCES, frozen_config as confirm_config, export_confirmations,
)
V16_STRUCTURE = "experiments/active/exp-btcusdtp-1h-dual-partial-preholdout-20260906-v16/results/candidate"
STRUCTURE_INPUTS = {
    "case_trades.csv.gz":"9a43891b81bf50c281db3a26d1e53188f6b7876c746b75509ae78a570826bc47",
    "control_trades.csv.gz":"6fc2975228852fe8afbbd62148cec64354f0cfd025c29d7cc49be0ef7e842c91",
}
STRUCTURE_COLUMNS = ["event_id","exit_time","exit_price","outcome","closed","hold_minutes",
                     "max_favourable_r","max_adverse_r"]

SOURCES = list(dict.fromkeys(CONFIRM_SOURCES+[
    "yoyo/evaluation/hourly_impulse_failed_reduce_research.py",
    "tests/test_hourly_impulse_failed_reduce.py", "tests/test_hourly_impulse_failed_reduce_research.py",
]))


def frozen_config():
    config=deepcopy(confirm_config())
    contract=config.pop("failed_confirm_contract")
    contract.pop("no_partial_on_confirmation_bar")
    contract.pop("full_exit_if_open_gross_not_above")
    contract.update(confirmed_action_open_gross_not_above=0.002,
        baseline_confirmed_fraction=1.0,candidate_confirmed_fraction=0.5,
        original_notional_fraction=True,maximum_combined_fast_fills=1,
        profitable_true_edge_unchanged=True,recovered_confirm_profit_cancels_only=True,
        reduction_is_not_tp=True,remainder_uses_original_slow_stop_deadline=True,
        confirmed_fill_equals_baseline_full_clock=True,
        realised_partial_does_not_uncensor_remainder=True,
        decimal_quote_accounting_only_on_new_reduced_paths=True)
    config.update(experiment_id=EXPERIMENT_ID,parent_results=PARENT,inputs=INPUTS,
        policies=deepcopy(POLICIES),failed_reduce_contract=contract,
        structure_reference=V16_STRUCTURE,structure_inputs=STRUCTURE_INPUTS,
        structure_columns=STRUCTURE_COLUMNS,
        scope_stop_rule="If net remains nonpositive or joint D/I evidence fails, end pure exit microtuning on this cohort; audit entry edge before another such variant.")
    return config


def verify_config(config, base):
    if json.dumps(config,sort_keys=True)!=json.dumps(frozen_config(),sort_keys=True):
        raise ValueError("Frozen V19 confirmed risk-reduction fraction changed")
    e=base["execution"]
    if base["development_folds"]!=FOLDS or e["max_hours"]!=72 or e["cost_fraction"]!=.002 or e["stop_first"] is not True:
        raise ValueError("Only original2023--2024,stop-first/72h/20bp permitted")


def reduced_mechanics(before, after):
    """Audit the changed fill, not a new confirmation clock or entry filter.

    All old non-full paths retain every old column. The old confirmed full
    execution becomes one risk half at the SAME open; subsequent slow exits
    remain independently simulated. Leg sums explicitly distinguish all known
    realised risk halves from the subset with a known closed remainder. A
    missing remainder is never zero (pandas sum uses min_count=1 below):
    https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.sum.html
    """
    out,groups,info=paired_mechanics(before,after)
    b=before.set_index("event_id").loc[out.event_id]
    a=after.set_index("event_id").loc[out.event_id]
    oldfull=b.outcome.eq("fast_failed_launch")
    columns=list(before.columns)
    assert_saved_parity(b.loc[~oldfull].reset_index()[columns],a.loc[~oldfull].reset_index()[columns])
    if a.outcome.eq("fast_failed_launch").any() or not a.failed_launch_count.eq(0).all():
        raise ValueError("Risk-reduction candidate cannot execute a fast full exit")
    for name in ("failed_reduce_fill_count","partial_fast_fill_count"):
        if not a[name].isin([0,1]).all():
            raise ValueError("Fast fill counts must be zero or one")
    reduced=a.failed_reduce_fill_count.eq(1)
    profitable=a.partial_fast_fill_count.eq(1)
    if not reduced.eq(oldfull).all():
        raise ValueError("V18 confirmed full population must equal V19 risk-fill population")
    if (a.failed_reduce_fill_count+a.partial_fast_fill_count).gt(1).any():
        raise ValueError("Only one combined fast partial is allowed")
    if not a.failed_confirm_confirm_count.eq(reduced.astype(int)).all():
        raise ValueError("Confirmed observation must execute exactly one risk half")
    stable=["event_id"]+[name for name in columns if
        (name.startswith("failed_confirm_") and name not in
         {"failed_confirm_status","failed_confirm_events","failed_confirm_last_reason"})
        or (name.startswith("failed_launch_") and name not in
            {"failed_launch_count","failed_launch_status"})]
    assert_saved_parity(before[stable],after[stable])
    if not a.partial_fraction.eq(.5*(reduced|profitable).astype(int)).all():
        raise ValueError("Partial fraction must describe the single original-notional half")
    if not a.failed_reduce_fraction.eq(.5*reduced.astype(int)).all():
        raise ValueError("Risk-reduction fraction/count mismatch")
    for table in (b,a):
        known_closed=table.closed.eq(True)
        if not np.isfinite(table.loc[known_closed,["net_return","gross_return"]]).all().all():
            raise ValueError("Closed trade economics must be finite")
        np.testing.assert_allclose(table.loc[known_closed,"gross_return"]-table.loc[known_closed,"net_return"],
                                   .002,rtol=0,atol=1e-12,err_msg="Original roundtrip cost changed")
        if table.loc[~known_closed,["net_return","gross_return","net_r"]].notna().any().any():
            raise ValueError("Unknown remainder cannot become known whole-trade economics")
    f=a.loc[reduced]
    old=b.loc[reduced]
    if not (f.partial_fraction.eq(.5)&f.exit_remaining_fraction.eq(.5)&f.partial_fast_fill_count.eq(0)).all():
        raise ValueError("Risk reduction must leave exactly half, without a profitable fill")
    for new_name,old_name in (("failed_reduce_fill_time","exit_time"),("partial_exit_time","exit_time"),
                              ("failed_reduce_fill_price","exit_price"),("partial_exit_price","exit_price"),
                              ("failed_reduce_full_notional_gross_return","gross_return")):
        left=old[[old_name]].rename(columns={old_name:new_name}).reset_index()
        right=f[[new_name]].reset_index()
        assert_saved_parity(left,right)
    if (pd.to_datetime(f.exit_time,utc=True)<pd.to_datetime(f.failed_reduce_fill_time,utc=True)).any():
        raise ValueError("Remainder cannot terminate before its actual reduction")
    realised=f.failed_reduce_realised_net_return
    if not (np.isfinite(realised)&realised.le(1e-12)).all():
        raise ValueError("Risk half must realise finite nonpositive net, not profitable TP")
    np.testing.assert_allclose(f.failed_reduce_realised_gross_return,.5*old.gross_return,rtol=0,atol=1e-12)
    np.testing.assert_allclose(realised,.5*old.gross_return-.001,rtol=0,atol=1e-12)
    np.testing.assert_allclose(f.realised_partial_gross_return,f.failed_reduce_realised_gross_return,rtol=0,atol=1e-12)
    for event in old.index:
        prior=json.loads(old.loc[event,"failed_confirm_events"])
        current=json.loads(f.loc[event,"failed_confirm_events"])
        confirmations=[x for x in current if x["action"]=="confirmed"]
        if len(confirmations)!=1:
            raise ValueError("One existing confirmation must record the risk fill")
        obs=confirmations[0]["observation"]
        expected={"fill_action":"risk_reduce","fill_fraction":.5,
                  "fill_price":float(f.loc[event,"failed_reduce_fill_price"]),
                  "fill_available_at":pd.Timestamp(f.loc[event,"failed_reduce_fill_time"]).isoformat()}
        if any(obs.get(key)!=value for key,value in expected.items()):
            raise ValueError("Confirmation fill evidence changed")
        for key in expected:obs.pop(key)
        if current!=prior:
            raise ValueError("Risk reduction must not invent a new flip or pending lifecycle")
    reduced_known=reduced&a.closed.eq(True)
    remainder=(a.net_return-a.failed_reduce_realised_net_return).where(reduced_known)*1e4
    # Independently check the remaining quote, not just net minus first leg.
    quote_gross=a.direction*(a.exit_price/a.entry_price-1)
    np.testing.assert_allclose(remainder.loc[reduced_known],
        (.5*quote_gross.loc[reduced_known]-.001)*1e4,rtol=0,atol=1e-8)
    out["baseline_confirmed_full"]=oldfull.to_numpy()
    out["candidate_risk_reduced"]=reduced.to_numpy()
    out["candidate_profitable_partial"]=profitable.to_numpy()
    out["candidate_risk_realised_net_bp"]=(a.failed_reduce_realised_net_return.where(reduced,0)*1e4).to_numpy()
    out["candidate_remainder_net_bp"]=remainder.to_numpy()
    out["recovered_winner"]=oldfull.to_numpy()&out.candidate_net_bp.gt(0)
    out["newly_unknown"]=b.closed.eq(True).to_numpy()&~a.closed.eq(True).to_numpy()
    known=out.delta_net_bp.notna()
    risk_mask=reduced.to_numpy();closed_mask=reduced_known.to_numpy()
    def leg_sum(column,mask):
        # No risk fills means a true empty sum. Existing but all-unknown legs
        # remain NaN rather than silently reporting zero realised total.
        return float(out.loc[mask,column].sum(min_count=1)) if np.any(mask) else 0.0
    info.update(baseline_confirmed_full_count=int(oldfull.sum()),candidate_risk_reduced_count=int(reduced.sum()),
        candidate_profitable_partial_count=int(profitable.sum()),baseline_profitable_partial_count=int(b.partial_fast_fill_count.sum()),
        unchanged_paths=int((~oldfull).sum()),pending_events=int(a.failed_confirm_create_count.sum()),
        cancelled_pending_events=int(a.failed_confirm_cancel_count.sum()),
        priority_terminated_pending_events=int(a.failed_confirm_priority_termination_count.sum()),
        changed_improved=int((oldfull.to_numpy()&out.delta_net_bp.gt(1e-8)).sum()),
        changed_hurt=int((oldfull.to_numpy()&out.delta_net_bp.lt(-1e-8)).sum()),
        changed_unknown_pairs=int((oldfull.to_numpy()&~known).sum()),
        recovered_winners=int(out.recovered_winner.sum()),newly_unknown=int(out.newly_unknown.sum()),
        baseline_partial_count=int(b.partial_fraction.eq(.5).sum()),
        candidate_partial_count=int(a.partial_fraction.eq(.5).sum()),
        improved=int(out.delta_net_bp.gt(1e-8).sum()),hurt=int(out.delta_net_bp.lt(-1e-8).sum()),
        unchanged=int((known&out.delta_net_bp.abs().le(1e-8)).sum()),unknown_pairs=int((~known).sum()),
        remainder_known_count=int(reduced_known.sum()),remainder_unknown_count=int((reduced&~a.closed.eq(True)).sum()),
        risk_realised_net_event_bp=leg_sum("candidate_risk_realised_net_bp",risk_mask),
        risk_realised_net_known_pairs_event_bp=leg_sum("candidate_risk_realised_net_bp",closed_mask),
        risk_realised_net_unknown_remainder_event_bp=leg_sum("candidate_risk_realised_net_bp",risk_mask&~closed_mask),
        remainder_net_event_bp=leg_sum("candidate_remainder_net_bp",risk_mask),
        reduced_total_net_event_bp=leg_sum("candidate_net_bp",risk_mask),
        reduced_delta_event_bp=leg_sum("delta_net_bp",risk_mask),
        leg_totals_scope="Risk realised: all risk fills; remainder/total/delta: known reduced pairs only, unknowns excluded and counted; empty risk population is zero.",
        interpretation="Retrospective all-intention risk-reduction effects, not a future entry filter or independently validated edge.")
    return out,groups,info


def remainder_structure_parity(reference, candidate):
    """Compare only final paths; V16 PnL is neither read by this helper nor reused."""
    if set(reference)!={"case","control"} or set(candidate)!=set(reference):
        raise ValueError("Both fixed populations are required for structural parity")
    checks={}
    for label in ("case","control"):
        assert_saved_parity(reference[label][STRUCTURE_COLUMNS],candidate[label][STRUCTURE_COLUMNS])
        checks[label]={"rows":len(reference[label]),"columns":len(STRUCTURE_COLUMNS)}
    return {"reference":V16_STRUCTURE,"inputs":STRUCTURE_INPUTS,"columns":STRUCTURE_COLUMNS,
            "checks":checks,"pnl_borrowed":False}


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
        # V16 is a final-path assertion only, after the candidate is simulated.
        # Do not load its PnL or use a saved exit to fill candidate economics.
        pin(ROOT/V16_STRUCTURE,STRUCTURE_INPUTS)
        structure={label:pd.read_csv(ROOT/V16_STRUCTURE/f"{label}_trades.csv.gz",usecols=STRUCTURE_COLUMNS)
                   for label in mothers}
        write_json(results/"remainder_structure_parity.json",remainder_structure_parity(structure,new[1]))
        info={}
        for label in mothers:
            mechanics,groups,info[label]=reduced_mechanics(old[1][label],new[1][label])
            write_csv(results/f"reduced_{label}_mechanics.csv",mechanics)
            write_csv(results/f"reduced_{label}_groups.csv",groups)
        write_csv(results/"fast_edges.csv.gz",export_edges({"baseline":old[1],"candidate":new[1]}))
        write_csv(results/"confirmation_events.csv.gz",export_confirmations({"baseline":old[1],"candidate":new[1]}))
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
            "structure_inputs":STRUCTURE_INPUTS,
            "output_hashes":{str(p.relative_to(results)):digest(p) for p in sorted(results.rglob("*")) if p.is_file()}}
        write_json(results/"summary.json",summary)
        print(json.dumps({"status":summary["status"],"candidate_net_bp":new[0]["metrics"]["mean_net_bp"],
            "case_delta_bp":effects["case_delta"]["mean_bp"],"mechanics":info}),flush=True)
    except Exception as error:
        write_json(results/"failure.json",{"type":type(error).__name__,"message":str(error)})
        raise


if __name__=="__main__":run()

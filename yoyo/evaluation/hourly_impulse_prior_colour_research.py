"""V13: prior completed4h colour entry gate, unchanged V5 true-flip execution.

Features use own signal_time=K1 OPEN and40 contiguous completed4h HL2 bars.
All713 contexts are persisted before arm outcomes. Unknown context is NaN,
known opposite abstention is zero with no fee; no outcome selects a request.
Baseline and accepted candidate requests are actually replayed on raw5m.
Paired opportunity D and fixed-triplet I include known non-trading zeros.
Reused2023--2024 only; no audit, live or parameter-search entrypoint.

Sources: pandas2.3.3 backward asof permits only right key<=left key:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge_asof.html
SMA minimum observations are explicit, not optional ATR/slope warmup:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Series.rolling.html
"""
from __future__ import annotations

from copy import deepcopy
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_colour_context import attach_entry_colour_context
from yoyo.data.hourly_impulse_prior_colour import add_prior_colour_context
from yoyo.evaluation.hourly_impulse_context_research import committed_sources, development_gates, month_support
from yoyo.evaluation.hourly_impulse_diagnostics import diagnose_frame
from yoyo.evaluation.hourly_impulse_k2_research import (
    describe, direct_requests, simulate_requests, matched_episodes, single_pending_ledger,
)
from yoyo.evaluation.hourly_impulse_launch_research import (
    BASE_CONFIG, BASE_SHA256, MOTHERS, MOTHER_INPUTS, PARENT, INPUTS, FOLDS,
    POLICIES as OLD_POLICIES, SOURCES as OLD_SOURCES,
    frozen_config as old_config, validate_population, replay_arm,
)
from yoyo.evaluation.hourly_impulse_management_research import assert_saved_parity, paired_effects, positive_inference
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, digest, metrics, write_csv, write_json
from yoyo.evaluation.hourly_impulse_transition_research import read_frame

EXPERIMENT_ID = "exp-btcusdtp-1h-prior4h-colour-preholdout-20260906-v13"
EXPERIMENT = ROOT/"experiments/active"/EXPERIMENT_ID
POLICIES = [deepcopy(OLD_POLICIES[0]), {**OLD_POLICIES[0],
    "id":"5m_native40_prior4h_colour", "entry_gate":"prior4h_colour_at_k1_open"}]
GATE_CONTRACT = {
    "time":"signal_time_K1_open", "minutes":240, "ma_kind":"SMA", "ma_length":40,
    "ma_source":"HL2", "side":"1_if_hl2_greater_equal_ma_else_minus1",
    "maximum_age_hours_exclusive":4, "minimum_contiguous_complete_bars":40,
    "require_atr":False, "require_slope":False, "control_gate":"own_context_no_transfer",
    "known_opposite":"zero_no_entry_no_fee", "unknown":"NaN_not_abstention",
    "serial_unknown":"conservative_full72h_reservation_not_actual_position",
    "population":"all251_cases462_controls154_fixed_triples97_unmatched",
}
SOURCES = list(dict.fromkeys(OLD_SOURCES + [
    "yoyo/data/hourly_impulse_context.py",
    "yoyo/data/hourly_impulse_prior_colour.py", "tests/test_hourly_impulse_prior_colour.py",
    "yoyo/evaluation/hourly_impulse_prior_colour_research.py", "tests/test_hourly_impulse_prior_colour_research.py",
]))
STATES = ("accepted","abstain","unknown")


def frozen_config():
    config=deepcopy(old_config())
    config.update(experiment_id=EXPERIMENT_ID,policies=deepcopy(POLICIES),gate_contract=deepcopy(GATE_CONTRACT))
    return config


def verify_config(config, base):
    if json.dumps(config,sort_keys=True)!=json.dumps(frozen_config(),sort_keys=True):
        raise ValueError("Frozen V13 single prior-colour contract changed")
    if base["development_folds"]!=FOLDS:
        raise ValueError("Only frozen2023--2024 development is permitted")
    e=base["execution"]
    if e["max_hours"]!=72 or e["cost_fraction"]!=.002 or e["stop_first"] is not True:
        raise ValueError("Original stop-first/72h/20bp must remain")


def counts(frame):
    values=frame.prior_colour_gate_state
    if values.isna().any() or not values.isin(STATES).all():
        raise ValueError("Every request requires an explicit tri-state gate")
    return {"total":len(frame),**values.value_counts().reindex(STATES,fill_value=0).to_dict()}


def gated_episodes(baseline, context):
    """Full request ledger; only known-opposite is an observed non-trading zero.

    Uses entry-known prior_colour_* columns and old execution outcomes solely
    for accounting. Accepted rows retain all original episode fields. Unknown
    reserves the inherited deadline for conservative serial diagnostics; it is
    not claimed to be a real position. No baseline outcome decides the gate.
    """
    if baseline.event_id.isna().any() or not baseline.event_id.is_unique or not context.event_id.is_unique:
        raise ValueError("Unique finite request identities required")
    if set(baseline.event_id)!=set(context.event_id):
        raise ValueError("Full original population must remain")
    counts(context)
    cols=[c for c in context if c.startswith("prior_colour_")]
    if any(c in baseline for c in cols):
        raise ValueError("Baseline must not already contain candidate gate")
    result=baseline.merge(context[["event_id"]+cols],on="event_id",how="left",validate="one_to_one")
    result["policy_fee_fraction"]=np.where(result.completed_trade,.002,np.nan)
    for state in ("abstain","unknown"):
        mask=result.prior_colour_gate_state.eq(state)
        for col in ("status","episode_status"):
            result.loc[mask,col]="prior_colour_"+state
        result.loc[mask,["executed","completed_trade"]]=False
        result.loc[mask,"observed"]=state=="abstain"
        result.loc[mask,"episode_net_return"]=0. if state=="abstain" else np.nan
        result.loc[mask,"policy_fee_fraction"]=0. if state=="abstain" else np.nan
        result.loc[mask,["entry_time","exit_time"]]=pd.NaT
        result.loc[mask,"terminal_time"]=result.loc[mask,"mother_decision_time"]
        result.loc[mask,"occupied_until"]=result.loc[mask,"mother_decision_time" if state=="abstain" else "mother_deadline"]
    accepted=result.prior_colour_gate_state.eq("accepted")
    assert_saved_parity(baseline.loc[baseline.event_id.isin(result.loc[accepted,"event_id"])],result.loc[accepted])
    if not result.observed.eq(np.isfinite(result.episode_net_return)).all():
        raise ValueError("Known policy return must match observed flag")
    return result


def mechanism_table(before, after):
    """Retrospective avoidance/opportunity-cost audit, not entry covariates."""
    columns=["event_id","episode_net_return"]
    result=after.merge(before[columns].rename(columns={"episode_net_return":"baseline_net_return"}),
        on="event_id",validate="one_to_one")
    result["difference"]=result.episode_net_return-result.baseline_net_return
    result["avoided_net_loser"]=result.prior_colour_gate_state.eq("abstain") & result.baseline_net_return.lt(0)
    result["missed_net_winner"]=result.prior_colour_gate_state.eq("abstain") & result.baseline_net_return.gt(0)
    groups=[]
    for state,part in result.groupby("prior_colour_gate_state",sort=True):
        known=part.difference.notna()
        groups.append({"gate_state":state,"n":len(part),"known_pairs":int(known.sum()),
            "old_mean_net_bp":part.loc[known,"baseline_net_return"].mean()*1e4,
            "new_mean_net_bp":part.loc[known,"episode_net_return"].mean()*1e4,
            "mean_delta_bp":part.difference.mean()*1e4,
            "sum_delta_event_bp":part.difference.sum(min_count=1)*1e4,
            "avoided_net_losers":int(part.avoided_net_loser.sum()),"missed_net_winners":int(part.missed_net_winner.sum())})
    info={**counts(result),"known_pairs":int(result.difference.notna().sum()),
        "avoided_net_losers":int(result.avoided_net_loser.sum()),"missed_net_winners":int(result.missed_net_winner.sum()),
        "avoided_loss_total_bp":-result.loc[result.avoided_net_loser,"baseline_net_return"].sum()*1e4,
        "missed_winner_total_bp":result.loc[result.missed_net_winner,"baseline_net_return"].sum()*1e4,
        "groups":groups}
    return result,pd.DataFrame(groups),info


def replay_gated(study,mothers,contexts,gated,old,folder,config):
    folder.mkdir()
    trades,episodes={},{}
    folds=[f[0] for f in FOLDS]
    for label in ("case","control"):
        selected=set(gated[label].loc[gated[label].prior_colour_gate_state.eq("accepted"),"event_id"])
        requests=contexts[label].loc[contexts[label].event_id.isin(selected)]
        # Actual candidate replay, independent of saved outcome masking.
        t=simulate_requests(study,requests,POLICIES[0]) if len(requests) else old[1][label].iloc[:0].copy()
        assert_saved_parity(old[1][label].loc[old[1][label].event_id.isin(selected)],t)
        e=gated_episodes(old[2][label],gated[label])
        # The accepted-policy accounting must reconcile actual replay, not just a cached selection.
        actual=t.set_index("event_id").net_return
        accepted=e.loc[e.prior_colour_gate_state.eq("accepted")]
        np.testing.assert_allclose(accepted.episode_net_return,accepted.event_id.map(actual),rtol=1e-12,atol=1e-12,equal_nan=True)
        trades[label],episodes[label]=t,e
        write_csv(folder/f"{label}_trades.csv.gz",t)
        write_csv(folder/f"{label}_episodes.csv.gz",e)
    pairs,matching=matched_episodes(episodes["case"],episodes["control"])
    serial=single_pending_ledger(episodes["case"])
    if episodes["case"].observed.all() and old[4].portfolio_selected.all() and not serial.portfolio_selected.all():
        raise ValueError("Known gate with unchanged exits cannot add baseline occupancy")
    write_csv(folder/"matched.csv",pairs)
    write_csv(folder/"single_pending.csv.gz",serial)
    chosen=set(serial.loc[serial.portfolio_selected,"event_id"])
    info,controls,single=(metrics(x,folds) for x in (trades["case"],trades["control"],trades["case"].loc[trades["case"].event_id.isin(chosen)]))
    months=month_support(trades["case"],folds)
    # Zero selected trades are a rejected experiment, not a metrics-schema crash.
    gate_metrics={"profit_factor":np.nan,**info}
    gates=development_gates(gate_metrics,matching,single,months,config)
    gates["complete_evidence"]=bool(episodes["case"].observed.all() and episodes["control"].observed.all())
    net=describe(episodes["case"].episode_net_return,episodes["case"].mother_decision_time)
    net.update(total_opportunities=len(episodes["case"]),unknown_opportunities=int((~episodes["case"].observed).sum()))
    gates.update(net_inference=positive_inference(net),excess_inference=positive_inference(matching["effect"]))
    diagnosis={}
    if len(trades["case"]):
        classified,diagnosis,tables=diagnose_frame(trades["case"])
        write_csv(folder/"classified_case_trades.csv.gz",classified)
        for name,frame in tables.items():write_csv(folder/("diagnosis_"+name+".csv"),frame)
    summary={"policy":POLICIES[1],"metrics":info,"control_metrics":controls,"matching":matching,
        "single_position":single,"serial_selected_mothers":len(chosen),"original_mothers":len(serial),
        "months":months,"net_effect":net,"diagnosis":diagnosis,"gates":gates,
        "gate_counts":{label:counts(gated[label]) for label in gated},
        "parity":{"accepted_trade_fields_unchanged":{label:{"rows":len(trades[label]),"columns":len(trades[label].columns)} for label in trades}},
        "accounting":"metrics=selected_actual_trades;net_effect=all_known_opportunities_including_abstention0"}
    write_json(folder/"summary.json",summary)
    return summary,trades,episodes,pairs,serial


def run():
    config_path=EXPERIMENT/"config.json"
    config=json.loads(config_path.read_text());base_path=ROOT/config["base_config"]
    if digest(base_path)!=BASE_SHA256:raise ValueError("Frozen base hash changed")
    base=json.loads(base_path.read_text());verify_config(config,base)
    sources=committed_sources([ROOT/p for p in SOURCES]+[config_path,base_path,EXPERIMENT/"PROJECT_PLAN.md"])
    for directory,hashes in ((ROOT/MOTHERS,MOTHER_INPUTS),(ROOT/PARENT,INPUTS)):
        for name,expected in hashes.items():
            if digest(directory/name)!=expected:raise ValueError("Pinned prior evidence changed: "+name)
    mothers={"case":read_frame(ROOT/MOTHERS/"original_mothers.csv.gz"),"control":read_frame(ROOT/MOTHERS/"control_mothers.csv.gz")}
    contexts={label:read_frame(ROOT/PARENT/f"direct_k1_stop_{label}_context.csv.gz") for label in mothers}
    assignments=read_frame(ROOT/MOTHERS/"assignments.csv")
    validate_population(mothers,contexts,assignments)
    results=EXPERIMENT/"results"
    if results.exists():raise ValueError("Preserve prior attempts; no overwrite")
    results.mkdir()
    write_json(results/"started.json",{"at":pd.Timestamp.now(tz="UTC"),"sources":sources,"inputs":INPUTS,"mother_inputs":MOTHER_INPUTS,
        "builder_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()})
    try:
        study=Study(base,"development")
        gated={label:add_prior_colour_context(contexts[label],study.raw) for label in contexts}
        all_context=pd.concat([frame.assign(population=label) for label,frame in gated.items()],ignore_index=True)
        write_csv(results/"context_gates.csv",all_context)
        context_receipt={"at":pd.Timestamp.now(tz="UTC"),"sha256":digest(results/"context_gates.csv"),
            "before_any_arm_outcomes":True,"populations":{label:counts(frame) for label,frame in gated.items()}}
        write_json(results/"context_gates_frozen.json",context_receipt)
        assert_saved_parity(mothers["case"],study.entries(base["baseline"]))
        for label in mothers:
            regenerated=attach_entry_colour_context(study.raw,study.featured(5,"SMA",40),direct_requests(mothers[label])[0])
            assert_saved_parity(contexts[label],regenerated)
            write_csv(results/f"{label}_context.csv.gz",contexts[label])
        write_csv(results/"assignments.csv",assignments)
        old=replay_arm(study,POLICIES[0],mothers,contexts,results/"baseline",config,parent=ROOT/PARENT)
        write_json(results/"anchor_parity.json",old[0]["parity"])
        new=replay_gated(study,mothers,contexts,gated,old,results/"candidate",config)
        frames,effects=paired_effects(old[2]["case"],new[2]["case"],old[3],new[3],old[4],new[4])
        for name,frame in frames.items():write_csv(results/(name+".csv"),frame)
        mechanics={}
        for label in mothers:
            ledger,groups,mechanics[label]=mechanism_table(old[2][label],new[2][label])
            write_csv(results/f"paired_{label}_mechanics.csv.gz",ledger)
            write_csv(results/f"{label}_mechanism_groups.csv",groups)
        monthly=[]
        for arm,episodes in (("baseline",old[2]["case"]),("candidate",new[2]["case"])):
            for (fold,month),part in episodes.assign(month=episodes.mother_decision_time.dt.strftime("%Y-%m")).groupby(["fold","month"]):
                monthly.append({"arm":arm,"fold":fold,"month":month,"n":len(part),"known":int(part.observed.sum()),
                    "executed":int(part.executed.sum()),"mean_net_bp":part.episode_net_return.mean()*1e4})
        write_csv(results/"monthly_case_net.csv",pd.DataFrame(monthly))
        gates={**new[0]["gates"],**{key:positive_inference(effects[key]) for key in ("case_delta","excess_delta")}}
        summary={"experiment_id":EXPERIMENT_ID,"status":"diagnostic_only_no_candidate_acceptance",
            "arms":{"baseline":old[0],"candidate":new[0]},"effects":effects,"mechanics":mechanics["case"],"control_mechanics":mechanics["control"],
            "gate_contract":GATE_CONTRACT,"context_receipt":context_receipt,"gates":gates,"all_financial_gates_pass":all(gates.values()),
            "known_coverage_ceiling":154/251,"coverage_required":.9,"source":study.source_receipt,"sources":sources,
            "config_sha256":digest(config_path),"audit_prices_loaded":False,"holdout_consumed":False,
            "production_eligible":False,"training_eligible":False,"inputs":INPUTS,"mother_inputs":MOTHER_INPUTS,
            "output_hashes":{str(p.relative_to(results)):digest(p) for p in sorted(results.rglob("*")) if p.is_file()}}
        write_json(results/"summary.json",summary)
        print(json.dumps({"status":summary["status"],"selected_trades":new[0]["metrics"]["events"],
            "selected_mean_net_bp":new[0]["metrics"]["mean_net_bp"],"opportunity_mean_net_bp":new[0]["net_effect"]["mean_bp"],
            "case_delta_bp":effects["case_delta"]["mean_bp"]}),flush=True)
    except Exception as error:
        write_json(results/"failure.json",{"at":pd.Timestamp.now(tz="UTC"),"type":type(error).__name__,"message":str(error)})
        raise


if __name__=="__main__":
    run()

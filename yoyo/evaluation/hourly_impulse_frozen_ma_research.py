"""V12: fixed signal-hour MA boundary, original V5 requests and true-flip exit.

Only a post-entry exit is added. The boundary is each request's own completed
signal-hour SMA40(HL2), not a dynamic MA or the matched case's MA. All original
251/462 requests and154 triples remain; entry geometry is measured before any
arm outcomes and never filters the population. Reused2023--2024 only.

Geometry reads raw open_time/open/segment_id at entry and its preceding5m
timestamp, and saved event signal_time/decision_time/direction/signal_close/
ma/signal_atr/initial_stop. No raw high/low/close or subsequent price is read by
that helper. After-entry trigger/MAE/MFE/returns are outcomes, never covariates.
Sources: pandas2.3 one-to-one merge and exact timestamp parity contracts:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.testing.assert_frame_equal.html
Signed geometry follows https://numpy.org/doc/2.0/reference/generated/numpy.sign.html
"""
from __future__ import annotations

from copy import deepcopy
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_colour_context import attach_entry_colour_context
from yoyo.evaluation.hourly_impulse_context_research import committed_sources
from yoyo.evaluation.hourly_impulse_k2_research import direct_requests
from yoyo.evaluation.hourly_impulse_launch_research import (
    BASE_CONFIG, BASE_SHA256, MOTHERS, MOTHER_INPUTS, PARENT, INPUTS, FOLDS, SELECTION,
    POLICIES as V11_POLICIES, SOURCES as V11_SOURCES, frozen_config as v11_config,
    validate_population, replay_arm,
)
from yoyo.evaluation.hourly_impulse_management_research import assert_saved_parity, paired_effects, positive_inference
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, digest, write_csv, write_json
from yoyo.evaluation.hourly_impulse_transition_research import read_frame

EXPERIMENT_ID = "exp-btcusdtp-1h-frozen-ma-exit-preholdout-20260906-v12"
EXPERIMENT = ROOT/"experiments/active"/EXPERIMENT_ID
POLICIES = [deepcopy(V11_POLICIES[0]), {**V11_POLICIES[0],"id":"5m_native40_frozen_ma","frozen_ma_exit":True}]
BOUNDARY_CONTRACT = {
    "field":"ma", "source":"own_completed_signal_hour", "price":"completed_held_raw5_close",
    "comparison":"strict_opposite_state", "equal_is_exit":False, "entry_state_gate":False,
    "control_boundary":"own_signal_hour_ma_no_transfer",
    "priority":"source_gap_stop_colour_deadline_before_frozen_ma", "launch_deadline":False,
    "geometry_bins":"negative_zero_inside_equal_stop_beyond_stop",
}
SOURCES = list(dict.fromkeys(V11_SOURCES + [
    "yoyo/evaluation/hourly_impulse_frozen_ma_research.py",
    "tests/test_hourly_impulse_frozen_ma_research.py",
    "tests/test_hourly_impulse_frozen_ma_exit.py",
]))
GEOMETRY_BINS = ("negative","zero","inside","equal_stop","beyond_stop")


def frozen_config():
    """Fresh full contract; V11 economics/support unchanged, no launch option."""
    config = deepcopy(v11_config())
    config.update(experiment_id=EXPERIMENT_ID,policies=deepcopy(POLICIES),boundary_contract=deepcopy(BOUNDARY_CONTRACT))
    return config


def verify_config(config, base):
    if json.dumps(config,sort_keys=True) != json.dumps(frozen_config(),sort_keys=True):
        raise ValueError("Frozen V12 single boundary contract changed")
    if base["development_folds"] != FOLDS:
        raise ValueError("Only frozen2023--2024 development is permitted")
    e = base["execution"]
    if e["max_hours"] != 72 or e["cost_fraction"] != .002 or e["stop_first"] is not True:
        raise ValueError("Original stop-first/72h/20bp economics must remain unchanged")


def _times(values):
    stamps=[]
    for value in values:
        if isinstance(value,(int,float,np.number,bool)):
            raise ValueError("Explicit timezone-aware timestamps required; no unit guessing")
        stamp=pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError("Finite timezone-aware timestamp required")
        stamps.append(stamp.tz_convert("UTC"))
    return pd.Series(stamps,index=values.index,dtype="datetime64[ns, UTC]")


def validate_boundary_inputs(contexts):
    """Before Study: own complete signal hour and finite positive MA/ATR only.

    No entry-state/MA-distance gate; all signed geometric configurations are
    retained. Extra outcome-looking columns are neither selected nor consulted.
    """
    for label, frame in contexts.items():
        required={"event_id","signal_time","decision_time","direction","ma","signal_close","signal_atr","initial_stop","fold"}
        if label not in ("case","control") or not required.issubset(frame):
            raise ValueError("Incomplete own-boundary context")
        if frame.columns.duplicated().any() or frame.event_id.isna().any() or not frame.event_id.is_unique:
            raise ValueError("Unique finite boundary identities/columns required")
        signal,decision=(_times(frame[c]) for c in ("signal_time","decision_time"))
        if not (signal.eq(signal.dt.floor("h")) & decision.eq(decision.dt.floor("h")) &
                (signal+pd.Timedelta(hours=1)).eq(decision)).all():
            raise ValueError("Boundary signal hour must be complete exactly at direct entry")
        for column in ("ma","signal_close","signal_atr","initial_stop"):
            values=pd.to_numeric(frame[column],errors="coerce")
            if frame[column].map(lambda x:isinstance(x,(bool,np.bool_))).any() or not (np.isfinite(values)&values.gt(0)).all():
                raise ValueError("Positive finite own boundary inputs required: "+column)
        if not frame.direction.isin([-1,1]).all() or frame.direction.map(lambda x:isinstance(x,(bool,np.bool_))).any():
            raise ValueError("Real direction must be +1/-1")


def validate_preflight(mothers, contexts, assignments):
    validate_population(mothers,contexts,assignments)
    validate_boundary_inputs(contexts)


def validate_boundary_source(hourly, contexts):
    """Verify each own boundary against causally rebuilt hourly SMA40(HL2).

    The caller supplies Study.featured(60, 'SMA', 40): each complete hour's
    open_time/ma/close uses that hour and its preceding39 contiguous HL2 bars.
    Match the exact saved signal_time, available signal_time+1h at decision;
    inspect no later hourly values and never replace or filter saved events.
    Case/control checks are independent: copying an incorrect control MA into
    its context cannot satisfy this provenance check. CSV tolerance is1e-12.
    """
    validate_boundary_inputs(contexts)
    if not {"open_time","ma","close"}.issubset(hourly) or hourly.columns.duplicated().any():
        raise ValueError("Rebuilt complete hourly source schema required")
    source=hourly[["open_time","ma","close"]].copy()
    source["open_time"]=_times(source.open_time)
    if not source.open_time.is_unique or not source.open_time.eq(source.open_time.dt.floor("h")).all():
        raise ValueError("Rebuilt signal-hour source must have unique exact hourly timestamps")
    source=source.set_index("open_time")
    populations={}
    for label,frame in contexts.items():
        times=_times(frame.signal_time)
        if not times.isin(source.index).all():
            raise ValueError("Missing exact own completed signal hour: "+label)
        selected=source.loc[times]
        counts={"n":len(frame)}
        for saved,column in (("ma","ma"),("signal_close","close")):
            values=pd.to_numeric(selected[column],errors="coerce").to_numpy(dtype=float)
            expected=pd.to_numeric(frame[saved],errors="coerce").to_numpy(dtype=float)
            if not (np.isfinite(values)&(values>0)).all() or not np.isclose(values,expected,rtol=1e-12,atol=1e-12).all():
                raise ValueError("Own completed signal-hour source mismatch: "+label+"."+saved)
            counts[saved+"_matched"]=len(frame)
            counts[saved+"_max_abs_error"]=float(np.abs(values-expected).max()) if len(frame) else 0.
        populations[label]=counts
    return {"feature_spec":{"minutes":60,"ma_kind":"SMA","ma_length":40,"ma_source":"HL2"},
        "join":"exact_own_signal_time","available_at":"signal_time+1h == decision_time",
        "relative_tolerance":1e-12,"absolute_tolerance":1e-12,
        "before_any_arm_outcomes":True,"saved_values_changed":False,"populations":populations}


def build_entry_geometry(raw, contexts, assignments):
    """Standalone causal table, all populations/order retained and no mutation.

    Uses only raw open_time/open/segment_id at exact entry and the immediately
    preceding5m timestamp/segment. Current/future high/low/close/volume are never
    selected. Saved signal_close is the previous completed hourly close. The
    whole finite raw timestamp prefix through the latest request is validated;
    subsequent rows cannot affect the returned values. No imputation/fallback.
    """
    validate_boundary_inputs(contexts)
    if set(contexts) != {"case","control"}:
        raise ValueError("Both full populations required")
    if assignments.event_id.isna().any() or not assignments.event_id.is_unique:
        raise ValueError("Unique original assignment identities required")
    case_ids=set(contexts["case"].event_id)
    if set(assignments.event_id) != case_ids:
        raise ValueError("Assignments must retain every original case")
    matched=set(assignments.loc[assignments.match_status.eq("matched"),"event_id"])
    if "parent_event_id" not in contexts["control"] or not set(contexts["control"].parent_event_id).issubset(matched):
        raise ValueError("Controls must retain their already assigned case")
    required={"open_time","open","segment_id"}
    if not required.issubset(raw) or raw.columns.duplicated().any():
        raise ValueError("Exact raw entry open/time/segment required")
    times=_times(raw.open_time)
    decisions=pd.concat([_times(f.decision_time) for f in contexts.values()],ignore_index=True)
    if decisions.empty:
        raise ValueError("Original geometry cannot be empty")
    prefix=raw.loc[times.le(decisions.max()),["open_time","open","segment_id"]].copy()
    prefix["open_time"]=times.loc[prefix.index]
    if prefix.open_time.duplicated().any() or not prefix.open_time.is_monotonic_increasing:
        raise ValueError("Entry prefix times must be unique and ordered")
    if not prefix.open_time.eq(prefix.open_time.dt.floor("5min")).all():
        raise ValueError("Raw entry clock is not5m aligned")
    prefix=prefix.set_index("open_time")
    rows=[]
    for label,frame in contexts.items():
        for event in frame.to_dict("records"):
            decision=pd.Timestamp(event["decision_time"]).tz_convert("UTC")
            prior=decision-pd.Timedelta(minutes=5)
            if decision not in prefix.index or prior not in prefix.index:
                raise ValueError("Missing exact entry/preceding source timestamp")
            current,previous=prefix.loc[decision],prefix.loc[prior]
            if isinstance(current["open"],(bool,np.bool_)):
                raise ValueError("A boolean is not an entry price")
            entry=float(current["open"])
            segments=pd.to_numeric(pd.Series([current.segment_id,previous.segment_id]),errors="coerce")
            if not np.isfinite(entry) or entry<=0 or not np.isfinite(segments).all() or segments.iloc[0]!=segments.iloc[1]:
                raise ValueError("Invalid entry open or source segment discontinuity")
            direction,ma,atr,close,stop=(float(event[c]) for c in ("direction","ma","signal_atr","signal_close","initial_stop"))
            risk=direction*(entry-stop)
            if not np.isfinite(risk) or risk<=0:
                raise ValueError("Pinned valid request acquired invalid initial risk")
            entry_distance=direction*(entry-ma)
            close_distance=direction*(close-ma)
            g=entry_distance/risk
            if not np.isfinite([entry_distance/atr,close_distance/atr,g]).all():
                raise ValueError("Nonfinite entry geometry")
            category="negative" if g<0 else "zero" if g==0 else "inside" if g<1 else "equal_stop" if g==1 else "beyond_stop"
            parent=event.get("parent_event_id")
            rows.append({"population":label,"event_id":event["event_id"],"parent_event_id":parent,
                "matched_case":event["event_id"] in matched if label=="case" else parent in matched,
                "fold":event["fold"],"signal_time":event["signal_time"],"decision_time":decision,
                "direction":direction,"ma":ma,"signal_close":close,"signal_atr":atr,"initial_stop":stop,
                "entry_open":entry,"raw_entry_segment_id":current.segment_id,
                "entry_distance_atr":entry_distance/atr,"entry_side":int(np.sign(entry_distance)),
                "previous_hour_close_distance_atr":close_distance/atr,"previous_hour_close_side":int(np.sign(close_distance)),
                "initial_R":risk,"entry_distance_r":g,"geometry_bin":category})
    return pd.DataFrame(rows)


def geometry_summary(frame):
    """Four pre-outcome populations; repeated controls are not extra cases."""
    case=frame.loc[frame.population.eq("case")]
    groups={"all_cases":case,"matched_cases":case.loc[case.matched_case],
            "unmatched_cases":case.loc[~case.matched_case],"controls":frame.loc[frame.population.eq("control")]}
    return {name:{"n":len(part),"geometry_bins":part.geometry_bin.value_counts().reindex(GEOMETRY_BINS,fill_value=0).to_dict()}
            for name,part in groups.items()}


def paired_mechanics(before, after):
    """All paired outcomes; frozen boundary trigger must beat the original exit.

    Every non-frozen known path retains ALL old fields, including MAE/MFE and
    native transition diagnostics. Unknown rows remain present and unclassified.
    Controls retain their own ma; matching does not equalize MA-boundary distance.
    """
    fixed=["event_id","entry_time","entry_price","direction","initial_stop","signal_atr","risk_pct","risk_atr","ma","signal_time","decision_time"]
    assert_saved_parity(before[fixed],after[fixed])
    if before.columns.duplicated().any() or after.columns.duplicated().any():
        raise ValueError("Duplicate paired columns")
    joined=before.merge(after,on="event_id",suffixes=("_before","_after"),validate="one_to_one")
    known=joined.closed_before & joined.closed_after & np.isfinite(joined.net_return_before) & np.isfinite(joined.net_return_after)
    joined["difference"]=(joined.net_return_after-joined.net_return_before).where(known)
    joined["frozen_exit"]=joined.outcome_after.eq("frozen_ma_exit")
    if (joined.frozen_exit & ~known).any():
        raise ValueError("Frozen exit requires complete paired outcome")
    retained_ids=set(joined.loc[known & ~joined.frozen_exit,"event_id"])
    assert_saved_parity(before.loc[before.event_id.isin(retained_ids)],after.loc[after.event_id.isin(retained_ids)])
    selected=after.loc[after.event_id.isin(set(joined.loc[joined.frozen_exit,"event_id"]))].set_index("event_id")
    original=before.set_index("event_id")
    for event_id,row in selected.iterrows():
        entry=pd.Timestamp(row.entry_time);exit_=pd.Timestamp(row.exit_time)
        trigger=pd.Timestamp(row.frozen_ma_trigger_open_time);available=pd.Timestamp(row.frozen_ma_trigger_available_at)
        if not (pd.notna(trigger) and pd.notna(available) and trigger>=entry and
                trigger==trigger.floor("5min") and available==trigger+pd.Timedelta(minutes=5) and
                available==exit_ and exit_<pd.Timestamp(original.loc[event_id,"exit_time"])):
            raise ValueError("Frozen trigger must be a held completed5m close and strictly earlier exit")
        hold=(exit_-entry).total_seconds()/60
        if hold<5 or hold%5 or row.hold_minutes!=hold:
            raise ValueError("Frozen hold clock must be exact5m and at least5min")
        if not isinstance(row.frozen_ma_enabled,(bool,np.bool_)) or not row.frozen_ma_enabled or row.frozen_ma_status!="structure_exit":
            raise ValueError("Frozen exit missing enabled structure state")
        if not np.isfinite([row.frozen_ma_boundary,row.frozen_ma_trigger_close]).all() or row.frozen_ma_boundary!=row.ma:
            raise ValueError("Frozen boundary must be the request's own unchanged MA")
        if row.direction*(row.frozen_ma_trigger_close-row.frozen_ma_boundary)>=0:
            raise ValueError("Frozen exit needs strict opposite completed CLOSE; equality is not exit")
        if pd.Timestamp(row.frozen_ma_available_at)!=pd.Timestamp(row.signal_time)+pd.Timedelta(hours=1) or pd.Timestamp(row.frozen_ma_available_at)!=entry:
            raise ValueError("Frozen signal boundary unavailable at entry")
    joined["win_loss_transition"]="unknown"
    for old,new,name in ((False,False,"loss_to_loss"),(False,True,"loss_to_win"),(True,False,"win_to_loss"),(True,True,"win_to_win")):
        joined.loc[known & joined.net_return_before.gt(0).eq(old) & joined.net_return_after.gt(0).eq(new),"win_loss_transition"]=name
    joined.loc[known & (joined.net_return_before.eq(0)|joined.net_return_after.eq(0)),"win_loss_transition"]="includes_flat"
    joined["mechanism_group"]=np.where(~known,"unknown",np.where(joined.frozen_exit,"frozen_ma_exit","original_exit_retained"))
    rows=[]
    for name,part in joined.groupby("mechanism_group",sort=True):
        rows.append({"group":name,"n":len(part),"known":int(part.difference.notna().sum()),
            "old_mean_net_bp":part.net_return_before.mean()*1e4,"new_mean_net_bp":part.net_return_after.mean()*1e4,
            "mean_delta_bp":part.difference.mean()*1e4,"sum_delta_event_bp":part.difference.sum(min_count=1)*1e4,
            "wins_before":int(part.net_return_before.gt(0).sum()),"wins_after":int(part.net_return_after.gt(0).sum())})
    distributions={}
    for column in ("net_return_before","net_return_after","difference"):
        values=joined[column].where(known).dropna()*1e4
        distributions[column]={"n":len(values),"unknown":len(joined)-len(values),"outliers_removed":0,
            "quantiles_bp":{str(k):v for k,v in values.quantile([0,.05,.25,.5,.75,.95,1]).items()},"sd_bp":values.std(ddof=1)}
    return joined,pd.DataFrame(rows),{"total":len(joined),"known":int(known.sum()),"frozen_ma_exits":int(joined.frozen_exit.sum()),
        "transitions":joined.win_loss_transition.value_counts().to_dict(),"distributions":distributions,"groups":rows,
        "interpretation":"Retrospective fixed-policy differences; I is neither pure K1-shape edge nor distance-matched boundary effect."}


def monthly_case_table(old, new):
    rows=[]
    for arm,episode in (("baseline",old),("candidate",new)):
        part=episode.assign(month=pd.to_datetime(episode.mother_decision_time,utc=True).dt.strftime("%Y-%m"))
        for fold,start,end in FOLDS:
            for stamp in pd.date_range(start,end,freq="MS",inclusive="left"):
                month=stamp.strftime("%Y-%m");subset=part.loc[part.fold.eq(fold)&part.month.eq(month)]
                rows.append({"arm":arm,"fold":fold,"month":month,"n":len(subset),
                    "known":int(subset.observed.sum()),"mean_net_bp":subset.episode_net_return.mean()*1e4})
    return pd.DataFrame(rows)


def run():
    config_path=EXPERIMENT/"config.json"
    config=json.loads(config_path.read_text())
    base_path=ROOT/config["base_config"]
    if digest(base_path)!=BASE_SHA256:
        raise ValueError("Frozen base config hash changed")
    base=json.loads(base_path.read_text());verify_config(config,base)
    sources=committed_sources([ROOT/p for p in SOURCES]+[config_path,base_path,EXPERIMENT/"PROJECT_PLAN.md"])
    for directory,hashes in ((ROOT/MOTHERS,MOTHER_INPUTS),(ROOT/PARENT,INPUTS)):
        for name,expected in hashes.items():
            if digest(directory/name)!=expected:
                raise ValueError("Pinned prior evidence changed: "+name)
    mothers={"case":read_frame(ROOT/MOTHERS/"original_mothers.csv.gz"),"control":read_frame(ROOT/MOTHERS/"control_mothers.csv.gz")}
    contexts={label:read_frame(ROOT/PARENT/f"direct_k1_stop_{label}_context.csv.gz") for label in mothers}
    assignments=read_frame(ROOT/MOTHERS/"assignments.csv")
    validate_preflight(mothers,contexts,assignments)
    results=EXPERIMENT/"results"
    if results.exists():
        raise ValueError("Preserve prior outcome attempts; no overwrite")
    results.mkdir()
    write_json(results/"started.json",{"at":pd.Timestamp.now(tz="UTC"),"sources":sources,"inputs":INPUTS,"mother_inputs":MOTHER_INPUTS,
        "builder_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()})
    try:
        study=Study(base,"development")
        boundary_source=validate_boundary_source(study.featured(60,"SMA",40),contexts)
        write_json(results/"boundary_source_parity.json",{"at":pd.Timestamp.now(tz="UTC"),**boundary_source})
        geometry=build_entry_geometry(study.raw,contexts,assignments)
        write_csv(results/"entry_geometry.csv",geometry)
        geometry_info=geometry_summary(geometry)
        write_json(results/"entry_geometry_frozen.json",{"at":pd.Timestamp.now(tz="UTC"),"sha256":digest(results/"entry_geometry.csv"),
            "before_any_arm_outcomes":True,"population":geometry_info,"used_for_selection":False})
        assert_saved_parity(mothers["case"],study.entries(base["baseline"]))
        for label in mothers:
            regenerated=attach_entry_colour_context(study.raw,study.featured(5,"SMA",40),direct_requests(mothers[label])[0])
            assert_saved_parity(contexts[label],regenerated)
            write_csv(results/f"{label}_context.csv.gz",contexts[label])
        write_csv(results/"assignments.csv",assignments)
        old=replay_arm(study,POLICIES[0],mothers,contexts,results/"baseline",config,parent=ROOT/PARENT)
        write_json(results/"anchor_parity.json",old[0]["parity"])
        new=replay_arm(study,POLICIES[1],mothers,contexts,results/"candidate",config)
        for label in mothers:
            fixed=list(contexts[label].columns)+["entry_time","entry_price","risk_pct","risk_atr"]
            assert_saved_parity(old[1][label][fixed],new[1][label][fixed])
        frames,effects=paired_effects(old[2]["case"],new[2]["case"],old[3],new[3],old[4],new[4])
        for name,frame in frames.items():write_csv(results/(name+".csv"),frame)
        diagnostics={}
        for label in mothers:
            mechanics,groups,diagnosis=paired_mechanics(old[1][label],new[1][label])
            write_csv(results/f"paired_{label}_mechanics.csv.gz",mechanics)
            write_csv(results/("mechanism_groups.csv" if label=="case" else "control_mechanism_groups.csv"),groups)
            diagnostics[label]=diagnosis
        write_csv(results/"monthly_case_net.csv",monthly_case_table(old[2]["case"],new[2]["case"]))
        gates={**new[0]["gates"],**{key:positive_inference(effects[key]) for key in ("case_delta","excess_delta")}}
        summary={"experiment_id":EXPERIMENT_ID,"status":"diagnostic_only_no_candidate_acceptance",
            "arms":{"baseline":old[0],"candidate":new[0]},"effects":effects,"mechanics":diagnostics["case"],
            "control_mechanics":diagnostics["control"],"entry_geometry":geometry_info,"boundary_contract":BOUNDARY_CONTRACT,
            "boundary_source_parity":boundary_source,
            "gates":gates,"all_financial_gates_pass":all(gates.values()),"known_coverage_ceiling":154/251,"coverage_required":.9,
            "source":study.source_receipt,"sources":sources,"config_sha256":digest(config_path),
            "audit_prices_loaded":False,"holdout_consumed":False,"production_eligible":False,"training_eligible":False,
            "inputs":INPUTS,"mother_inputs":MOTHER_INPUTS,
            "output_hashes":{str(p.relative_to(results)):digest(p) for p in sorted(results.rglob("*")) if p.is_file()}}
        write_json(results/"summary.json",summary)
        print(json.dumps({"status":summary["status"],"candidate_net_bp":new[0]["metrics"]["mean_net_bp"],
            "frozen_ma_exits":diagnostics["case"]["frozen_ma_exits"],"case_delta_bp":effects["case_delta"]["mean_bp"]}),flush=True)
    except Exception as error:
        write_json(results/"failure.json",{"at":pd.Timestamp.now(tz="UTC"),"type":type(error).__name__,"message":str(error)})
        raise


if __name__=="__main__":
    run()

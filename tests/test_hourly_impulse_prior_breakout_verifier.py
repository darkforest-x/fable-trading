"""Pure artificial support windows, no original prices/outcomes or runner imports."""
from collections import Counter,defaultdict
from copy import deepcopy
import csv
from datetime import datetime,timedelta,timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("v14_support_verifier",ROOT/"scripts/verify_hourly_impulse_prior_breakout_v14.py")
v=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(v)


def fixture(*,full=False,case_states=None,control_states=None):
    n,m,k=(251,462,154) if full else (8,6,2)
    case_states=case_states or ["accepted"]*n
    control_states=control_states or ["accepted"]*m
    assert len(case_states)==n and len(control_states)==m
    #Disjoint21-hour source windows avoid contradictory duplicate physical
    #source observations while spreading admitted cases over several months.
    mothers=[dict(event_id="case"+str(i),population="case",fold=list(v.FOLDS)[i%4],direction=1 if i%2==0 else -1) for i in range(n)]
    controls=[dict(event_id="case{}::control{}".format(i,j),parent_event_id="case"+str(i),population="control",fold=mothers[i]["fold"],direction=mothers[i]["direction"]) for i in range(k) for j in range(3)]
    all_rows=mothers+controls;state_by_id={r["event_id"]:s for r,s in zip(all_rows,case_states+control_states)}
    for fold,(start,end) in v.FOLDS.items():
        c=[r for r in mothers if r["fold"]==fold];r=[r for r in controls if r["fold"]==fold];total=len(c)+len(r)
        positions={round(i*(total-1)/(len(c)-1)) if len(c)>1 else 0 for i in range(len(c))}
        ci=ri=0
        for slot in range(total):
            if slot in positions:row=c[ci];ci+=1
            else:row=r[ri];ri+=1
            #Stop well before the fixed72h fold embargo, including full fixtures.
            row["decision_time"]=(datetime.fromisoformat(start).replace(tzinfo=timezone.utc)+timedelta(days=1,hours=slot*21)).isoformat()
            row["signal_time"]=(datetime.fromisoformat(row["decision_time"])-timedelta(hours=1)).isoformat()
    source=[];context=[]
    for original in all_rows:
        state=state_by_id[original["event_id"]];direction=original["direction"]
        signal=datetime.fromisoformat(original["signal_time"])
        close=(121. if direction==1 else 79.) if state!="abstain" else (120. if direction==1 else 80.)
        row=dict(original,signal_close=close,breakout20=state!="abstain",prior_breakout_window_start=(signal-timedelta(hours=20)).isoformat(),
            prior_breakout_window_end=(signal-timedelta(hours=1)).isoformat(),prior_breakout_available_at=signal.isoformat(),
            prior_breakout_signal_available_at=original["decision_time"],prior_breakout_count=9 if state=="unknown" else 20,
            prior_breakout_high=None if state=="unknown" else 120.,prior_breakout_low=None if state=="unknown" else 80.,
            prior_breakout_signal_close=close,prior_breakout_raw_segment_id=12345,prior_breakout_known=state!="unknown",
            prior_breakout_reason="source_gap" if state=="unknown" else "known",prior_breakout_gate_state=state)
        context.append(row)
        for offset in range(-20,1):
            if state=="unknown" and offset==-10:continue
            source.append(dict(population=row["population"],event_id=row["event_id"],role="k1" if offset==0 else "prior",
                open_time=(signal+timedelta(hours=offset)).isoformat(),open=100.,high=max(100.,close)+10 if offset==0 else 120.,
                low=min(100.,close)-10 if offset==0 else 80.,close=close if offset==0 else 100.,segment_id=list(v.FOLDS).index(row["fold"])))
    counts=[]
    for population in ("case","control"):
        part=[r for r in context if r["population"]==population]
        dimensions={"all":["all"],"fold":list(v.FOLDS),"direction":["1","-1"],"month":["{:04d}-{:02d}".format(y,m) for y in (2023,2024) for m in range(1,13)]}
        for dimension,keys in dimensions.items():
            for key in keys:
                subset=part if dimension=="all" else [r for r in part if (r["decision_time"][:7] if dimension=="month" else str(r[dimension]))==key]
                counter=Counter(r["prior_breakout_gate_state"] for r in subset)
                counts.append(dict(population=population,dimension=dimension,key=key,total=len(subset),**{s:counter[s] for s in v.STATES},
                    accepted_rate=counter["accepted"]/len(subset) if subset else None))
    matched=[];lookup={r["event_id"]:r for r in context}
    for mother in mothers[:k]:
        ids=sorted(r["event_id"] for r in controls if r["parent_event_id"]==mother["event_id"])
        states=Counter(lookup[key]["prior_breakout_gate_state"] for key in ids)
        state=lookup[mother["event_id"]]["prior_breakout_gate_state"]
        matched.append(dict(event_id=mother["event_id"],fold=mother["fold"],case_state=state,control_ids="|".join(ids),
            control_total=3,**{"control_"+s:states[s] for s in v.STATES},all_known=state!="unknown" and not states["unknown"]))
    populations={}
    for population in ("case","control"):
        states=Counter(r["prior_breakout_gate_state"] for r in context if r["population"]==population)
        populations[population]=dict(total=sum(states.values()),**{s:states[s] for s in v.STATES})
    accepted=[r for r in context if r["population"]=="case" and r["prior_breakout_gate_state"]=="accepted"]
    values=dict(events=len(accepted),minimum_fold_events=min(sum(r["fold"]==fold for r in accepted) for fold in v.FOLDS),
        active_months=len({r["decision_time"][:7] for r in accepted}),minimum_fold_months=min(len({r["decision_time"][:7] for r in accepted if r["fold"]==fold}) for fold in v.FOLDS))
    gates={"minimum_events":values["events"]>=80,"minimum_per_fold":values["minimum_fold_events"]>=12,
        "minimum_active_months":values["active_months"]>=12,"minimum_months_per_fold":values["minimum_fold_months"]>=3}
    summary=dict(experiment_id=v.EXPERIMENT_ID,status="support_pass_requires_separate_replay" if all(gates.values()) else "insufficient_support_no_outcomes",
        population=populations,support_values=values,support_gates=gates,support_pass=all(gates.values()),
        matching=dict(matched=k,unmatched=n-k,all_known=sum(r["all_known"] for r in matched),coverage=k/n,required_coverage=.9,coverage_pass=False),
        gate_hours=20,outcomes_read_or_computed=False,outcome_replays=0,profitability_test=False,holdout_consumed=False,training_eligible=False,production_eligible=False)
    return context,source,counts,matched,summary


def run(data,full=False):return v.verify_tables(*data,expected_counts=(251,462,154) if full else (8,6,2))


def test_small_support_stop_and_full_support_pass_without_outcomes():
    assert run(fixture())["support_status"]=="insufficient_support_no_outcomes"
    output=run(fixture(full=True),True)
    assert output["support_status"]=="support_pass_requires_separate_replay"
    assert output["saved_source_rows"]==14973 and output["count_rows"]==62
    assert output["matched_groups"]==154 and output["unmatched"]==97
    assert output["outcomes_read_or_computed"] is output["raw_price_replay"] is False


@pytest.mark.parametrize("direction",[-1,1])
def test_equality_is_abstention_and_k1_wick_does_not_raise_prior_extreme(direction):
    data=fixture(case_states=["abstain"]*8)
    row=next(r for r in data[0] if r["direction"]==direction and r["population"]=="case")
    fact=v.analyze_windows(data[0],data[1])[("case",row["event_id"])]
    assert fact["prior_high"]==120 and fact["prior_low"]==80 and fact["gate_state"]=="abstain"
    own=next(r for r in data[1] if r["event_id"]==row["event_id"] and r["role"]=="k1")
    assert own["high"]>120 if direction==1 else own["low"]<80
    row["prior_breakout_gate_state"]="accepted"
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("suffix,value",[("window_start","2023-01-01T04:00:00Z"),("window_end","2023-01-01T23:00:00Z"),
    ("available_at","2023-01-02T00:00:00Z"),("signal_available_at","2023-01-02T00:00:00.000000001Z"),
    ("high",131),("low",79),("count",19),("count",20.5),("known",False),("gate_state","unknown"),
    ("signal_close",122),("raw_segment_id",float("inf")),("reason","warmup")])
def test_context_boundary_or_current_bar_contamination(suffix,value):
    data=fixture();data[0][0]["prior_breakout_"+suffix]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("mutation",["wrong_role","future","duplicate","outside","invalid_ohlc","bad_segment","drop_k1","drop_prior"])
def test_saved_source_window_corruption(mutation):
    data=fixture();rows=data[1];own=next(r for r in rows if r["event_id"]=="case0" and r["role"]=="k1")
    if mutation=="wrong_role":own["role"]="prior"
    elif mutation=="future":own["open_time"]=(datetime.fromisoformat(own["open_time"])+timedelta(hours=1)).isoformat()
    elif mutation=="duplicate":rows.append(deepcopy(rows[0]))
    elif mutation=="outside":rows[0]["open_time"]=(datetime.fromisoformat(rows[0]["open_time"])-timedelta(hours=1)).isoformat()
    elif mutation=="invalid_ohlc":rows[0]["high"]=79
    elif mutation=="bad_segment":rows[0]["segment_id"]=float("nan")
    elif mutation=="drop_k1":rows.remove(own)
    else:rows.remove(rows[0])
    with pytest.raises(v.VerificationError):run(data)


def test_unknown_tail_count_not_total_available_rows():
    data=fixture(case_states=["unknown"]+["accepted"]*7)
    output=run(data)
    assert output["population"]["case"]["unknown"]==1
    fact=v.analyze_windows(data[0],data[1])[("case","case0")]
    assert fact["saved_prior_rows"]==19 and fact["prior_count"]==9
    data[0][0]["prior_breakout_count"]=19
    with pytest.raises(v.VerificationError):run(data)


def test_missing_k1_can_preserve_valid_prior20_extrema_but_is_unknown():
    data=fixture();context,source=data[:2]
    source[:]=[r for r in source if not (r["event_id"]=="case0" and r["role"]=="k1")]
    row=context[0];row.update(prior_breakout_known=False,prior_breakout_gate_state="unknown",prior_breakout_reason="missing_signal_hour",
        prior_breakout_raw_segment_id=None,prior_breakout_signal_close=None)
    fact=v.analyze_windows(context,source)[("case","case0")]
    assert fact["prior_count"]==20 and fact["prior_high"]==120 and fact["gate_state"]=="unknown"
    v.verify_context(context,v.analyze_windows(context,source))


def test_empty_source_never_known_and_raw_hourly_segment_domains_not_compared():
    data=fixture()
    assert all(r["prior_breakout_raw_segment_id"]==12345 for r in data[0])
    assert run(data)["status"]=="passed"  #hourly source segment values0..3 differ intentionally.
    for row in data[0]:row.update(prior_breakout_count=0,prior_breakout_high=None,prior_breakout_low=None,prior_breakout_signal_close=None,
        prior_breakout_raw_segment_id=None,prior_breakout_known=False,prior_breakout_reason="no_source",prior_breakout_gate_state="unknown")
    v.verify_context(data[0],v.analyze_windows(data[0],[]))


def test_known_case_old_breakout_feature_parity():
    data=fixture();data[0][0]["breakout20"]=False
    with pytest.raises(v.VerificationError):run(data)


def test_shared_hour_must_have_identical_ohlc_not_request_specific_prices():
    data=fixture();case=data[0][0]
    alias=dict(case,event_id="alias",population="control",parent_event_id="case0")
    source=[dict(r,event_id="alias",population="control") for r in data[1] if r["event_id"]=="case0"]
    v.analyze_windows([case,alias],[r for r in data[1] if r["event_id"]=="case0"]+source)
    source[0]["high"]+=1
    with pytest.raises(v.VerificationError,match="contradictory"):v.analyze_windows([case,alias],[r for r in data[1] if r["event_id"]=="case0"]+source)


@pytest.mark.parametrize("states",[["abstain"]*6,["unknown"]*6,["accepted","abstain","unknown"]*2])
def test_controls_gate_independently_and_fixed_triplets_never_shrink(states):
    data=fixture(control_states=states);output=run(data)
    assert output["matched_groups"]==2 and output["population"]["control"]["accepted"]==states.count("accepted")
    assert all(row["control_total"]==3 for row in data[3])


@pytest.mark.parametrize("field,value",[("control_total",2),("control_accepted",2),("all_known",False),("case_state","abstain"),
    ("control_ids","case0::control0|case0::control1|foreign"),("fold","2024H1")])
def test_matched_support_corruption(field,value):
    data=fixture();data[3][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("table",range(4))
def test_dropped_or_duplicate_identity_or_dimension(table):
    for duplicate in (False,True):
        data=fixture();rows=data[table];rows.append(deepcopy(rows[0])) if duplicate else rows.pop()
        with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field,value",[("total",999),("accepted",999),("accepted_rate",0),("unknown",1)])
def test_count_denominator_corruption(field,value):
    data=fixture();data[2][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


def test_missing_halfyear_is_zero_not_dropped_from_minimum():
    data=fixture(full=True,case_states=["abstain" if i%4==2 else "accepted" for i in range(251)])
    output=run(data,True)
    assert output["support_values"]["minimum_fold_events"]==0
    assert output["support_gates"]["minimum_events"] is True and output["support_gates"]["minimum_per_fold"] is False


def test_control_pool_cannot_rescue_case_event_gate():
    data=fixture(full=True,case_states=["accepted" if i<60 else "abstain" for i in range(251)])
    output=run(data,True)
    assert output["support_values"]["events"]==60 and output["population"]["control"]["accepted"]==462
    assert output["support_status"]=="insufficient_support_no_outcomes"
    data[4]["support_values"]["events"]+=462
    with pytest.raises(v.VerificationError):run(data,True)


@pytest.mark.parametrize("field",["outcomes_read_or_computed","profitability_test","production_eligible","training_eligible","holdout_consumed"])
def test_support_never_claims_economics_or_production(field):
    data=fixture();data[4][field]=True
    with pytest.raises(v.VerificationError):run(data)


def test_support_is_invariant_to_input_order():
    data=fixture();expected=run(data)
    for rows in data[:4]:rows.reverse()
    assert run(data)==expected


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,sort_keys=True,allow_nan=False))


def write_csv(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True);opener=gzip.open if path.suffix==".gz" else open
    columns=list(dict.fromkeys(k for row in rows for k in row))
    with opener(path,"wt",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=columns);writer.writeheader();writer.writerows(rows)


@pytest.mark.parametrize("column",["net_return","gross_return","closed","outcome","max_favourable_r","exit_time","net_r"])
def test_outcome_columns_rejected_before_row_values(tmp_path,column):
    path=tmp_path/"support.csv";path.write_text("event_id,"+column+"\na,not_read\n")
    with pytest.raises(v.VerificationError,match="Outcome"):v.read_csv(path)


def disk_fixture(tmp_path,monkeypatch):
    data=fixture(full=True,case_states=["accepted" if i<60 else "abstain" for i in range(251)])
    context,source,counts,matched,summary=data
    results=tmp_path/v.EXPERIMENT_PATH/"results";parent=tmp_path/v.PARENT_PATH
    original={population:[{k:value for k,value in row.items() if not k.startswith("prior_breakout_") and k!="population"} for row in context if row["population"]==population] for population in ("case","control")}
    for population,name in (("case","original_mothers.csv.gz"),("control","control_mothers.csv.gz")):write_csv(parent/name,original[population])
    assignments=[dict(event_id=row["event_id"],fold=row["fold"],match_status="matched" if i<154 else "insufficient_exact_controls" if i<248 else "missing_causal_matching_support") for i,row in enumerate(original["case"])]
    write_csv(parent/"assignments.csv",assignments);write_json(parent/"assignment_receipt.json",dict(matched=154))
    for name,rows in (("entry_context",context),("prior_hourly_rows",source),("counts",counts),("matched_support",matched)):write_csv(results/(name+".csv"),rows)
    base=dict(execution=dict(cost_fraction=.002,max_hours=72,stop_first=True),development_folds=[[fold,a,c] for fold,(a,c) in v.FOLDS.items()],source=dict(sha256="synthetic-no-raw"))
    write_json(tmp_path/v.BASE_PATH,base)
    config=dict(experiment_id=v.EXPERIMENT_ID,base_config=v.BASE_PATH,base_config_sha256=v.sha(tmp_path/v.BASE_PATH),parent_results=v.PARENT_PATH,
        inputs={path.name:v.sha(path) for path in parent.iterdir()},development_folds=base["development_folds"],support=v.SUPPORT,gate=v.GATE,
        expected=dict(mothers=251,controls=462,matched=154,status_counts=dict(matched=154,insufficient_exact_controls=94,missing_causal_matching_support=3)),
        inherited_execution_not_run=dict(cost_fraction=.002,max_hours=72,stop="K1_extreme",exit="5m_native40_true_aligned_to_opposite"),
        matching_coverage=dict(actual=154/251,required=.9,pass_=False),no_outcome_entry_point=True,holdout_consumed=False,training_eligible=False,production_eligible=False)
    config["matching_coverage"]["pass"]=config["matching_coverage"].pop("pass_")
    write_json(tmp_path/v.EXPERIMENT_PATH/"config.json",config)
    paths=v.SOURCE_FILES | {v.EXPERIMENT_PATH+"/config.json",v.EXPERIMENT_PATH+"/PROJECT_PLAN.md",v.BASE_PATH}
    committed={path:(tmp_path/path).read_bytes() if (tmp_path/path).exists() else ("synthetic committed source:"+path).encode() for path in paths}
    sources=[dict(path=path,sha256=hashlib.sha256(content).hexdigest()) for path,content in sorted(committed.items())]
    started=dict(at="2026-09-06T01:00:00Z",sources=sources,inputs=config["inputs"],builder_commit="c"*40)
    write_json(results/"started.json",started)
    checkpoint=dict(at="2026-09-06T01:00:01Z",output_hashes={name:v.sha(results/name) for name in v.CSV_NAMES},outcome_replays=0)
    write_json(results/"support_frozen.json",checkpoint)
    summary.update(config_sha256=v.sha(tmp_path/v.EXPERIMENT_PATH/"config.json"),source_receipt=dict(sha256=base["source"]["sha256"],holdout_price_rows=0,
        phase_price_last_open="2024-12-31T23:55:00Z"),source_receipts=sources,input_hashes=config["inputs"],output_hashes=checkpoint["output_hashes"],generated_at="2026-09-06T01:00:02Z")
    write_json(results/"summary.json",summary)
    def fake_git(command,**kwargs):
        assert command[:2]==["git","show"]
        if "--format=%ct" in command:return SimpleNamespace(stdout="1788652800")
        commit,path=command[2].split(":",1);assert commit=="c"*40 and not path.startswith("data/")
        return SimpleNamespace(stdout=committed[path])
    monkeypatch.setattr(v.h.subprocess,"run",fake_git)
    def refresh():
        checkpoint["output_hashes"]={name:v.sha(results/name) for name in v.CSV_NAMES}
        summary["output_hashes"]=checkpoint["output_hashes"]
        write_json(results/"support_frozen.json",checkpoint);write_json(results/"summary.json",summary)
    return results,summary,config,committed,checkpoint,refresh


def test_whole_support_directory_no_outcome_paths_read_and_no_writes(tmp_path,monkeypatch):
    results,summary,config,committed,checkpoint,refresh=disk_fixture(tmp_path,monkeypatch)
    before={str(path.relative_to(tmp_path)):path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    output=v.verify(tmp_path)
    assert output["support_values"]["events"]==60 and output["saved_source_rows"]==14973
    assert output["output_hashes_verified"]==4 and output["committed_sources_verified"]==11
    assert {str(path.relative_to(tmp_path)):path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}==before


@pytest.mark.parametrize("mutation",["extra_outcome_file","failure","source_hash","input_hash","config","csv_hash","receipt_time","unknown_coercion",
    "matched_ids","gate_override","current_bar_role","source_commit","missing_source","csv_economics","coverage_rescue"])
def test_support_directory_corruption(tmp_path,monkeypatch,mutation):
    results,summary,config,committed,checkpoint,refresh=disk_fixture(tmp_path,monkeypatch)
    if mutation=="extra_outcome_file":write_json(results/"baseline"/"summary.json",dict(economics="forbidden"))
    elif mutation=="failure":write_json(results/"failure.json",dict(status="failed"))
    elif mutation=="source_hash":committed["yoyo/data/hourly_impulse_prior_breakout.py"]+=b"corrupted"
    elif mutation=="input_hash":(tmp_path/v.PARENT_PATH/"assignment_receipt.json").write_text("changed")
    elif mutation=="config":config["gate"]=dict(config["gate"],prior_hours=19);write_json(tmp_path/v.EXPERIMENT_PATH/"config.json",config)
    elif mutation=="csv_hash":(results/"counts.csv").write_text("changed")
    elif mutation=="receipt_time":checkpoint["at"]="2026-09-06T00:59:59.999999999Z";refresh()
    elif mutation=="unknown_coercion":
        rows=v.read_csv(results/"entry_context.csv");rows[0]["prior_breakout_known"]="False";write_csv(results/"entry_context.csv",rows);refresh()
    elif mutation=="matched_ids":
        rows=v.read_csv(results/"matched_support.csv");rows[0]["control_ids"]="replacement";write_csv(results/"matched_support.csv",rows);refresh()
    elif mutation=="gate_override":summary["support_gates"]["minimum_events"]=True;summary["support_pass"]=True;refresh()
    elif mutation=="current_bar_role":
        rows=v.read_csv(results/"prior_hourly_rows.csv");next(r for r in rows if r["role"]=="k1")["role"]="prior";write_csv(results/"prior_hourly_rows.csv",rows);refresh()
    elif mutation=="source_commit":
        path=v.EXPERIMENT_PATH+"/config.json";committed[path]+=b"\n";digest=hashlib.sha256(committed[path]).hexdigest()
        for row in summary["source_receipts"]:
            if row["path"]==path:row["sha256"]=digest
        start=v.read_json(results/"started.json");start["sources"]=summary["source_receipts"];write_json(results/"started.json",start);refresh()
    elif mutation=="missing_source":
        summary["source_receipts"]=summary["source_receipts"][1:];start=v.read_json(results/"started.json");start["sources"]=summary["source_receipts"]
        write_json(results/"started.json",start);refresh()
    elif mutation=="csv_economics":
        rows=v.read_csv(results/"entry_context.csv");rows[0]["net_return"]=.1;write_csv(results/"entry_context.csv",rows);refresh()
    else:summary["matching"]["coverage_pass"]=True;refresh()
    with pytest.raises(v.VerificationError):v.verify(tmp_path)


def test_missing_results_fail_closed(tmp_path):
    with pytest.raises(v.VerificationError):v.verify(tmp_path)


def test_no_strategy_or_third_party_imports():
    import ast
    tree=ast.parse(Path(SPEC.origin).read_text())
    names=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]+[a.name for node in ast.walk(tree) if isinstance(node,ast.Import) for a in node.names]
    assert not any(name and name.split(".")[0] in {"yoyo","pandas","numpy","scipy"} for name in names)

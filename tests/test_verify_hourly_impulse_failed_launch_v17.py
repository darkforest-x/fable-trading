"""V17 synthetic saved evidence only; fixtures never import a strategy."""
from copy import deepcopy
from datetime import datetime,timedelta
from decimal import Decimal,localcontext
import importlib.util
import json
from collections import Counter,defaultdict
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("verify_failed_v17",ROOT/"scripts/verify_hourly_impulse_failed_launch_v17.py")
v=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(v)
FSPEC=importlib.util.spec_from_file_location("dual_synthetic_fixtures",ROOT/"tests/test_verify_hourly_impulse_dual_partial_v16.py")
f=importlib.util.module_from_spec(FSPEC);FSPEC.loader.exec_module(f)


def baseline(row,eligible=True,price=None):
    """Old V16 may reject an unprofitable edge then take a later profitable half."""
    old=f.candidate(row,partial=not eligible)
    if eligible:
        rejected=f.edge(old,5,price=100.+row["direction"]*.1 if price is None else price,action="insufficient_profit")
        chosen=f.edge(old,15)
        old["partial_fast_events"]=json.dumps([rejected,chosen]);f.fill(old,chosen)
    return old


def opt_in(old):
    row=deepcopy(old)
    row.update(failed_launch_enabled=True,failed_launch_count=0,failed_launch_profit_threshold=.002,
        failed_launch_status="prior_exit" if old["closed"] else "unknown_source",failed_launch_trigger_previous_open_time=None,
        failed_launch_trigger_previous_available_at=None,failed_launch_trigger_open_time=None,failed_launch_trigger_available_at=None,
        failed_launch_trigger_previous_side=None,failed_launch_trigger_side=None,failed_launch_trigger_open_price=None,
        failed_launch_trigger_gross_return=None,failed_launch_slow_open_time=None,failed_launch_slow_available_at=None,
        failed_launch_slow_side=None,failed_launch_slow_state="unknown")
    return row


def failed(old):
    row=opt_in(old);log=json.loads(old["partial_fast_events"])
    ix=next(i for i,e in enumerate(log) if e["action"]=="insufficient_profit")
    log=deepcopy(log[:ix+1]);last=log[-1]
    last["action"]="failed_launch_exit"
    with localcontext() as context:
        context.prec=40
        entry=Decimal(str(old["entry_price"]))
        gross=float(Decimal(str(old["direction"]))*(Decimal(str(last["open_price"]))-entry)/entry)
    last["gross_return"]=gross
    row.update(exit_time=last["available_at"],exit_price=last["open_price"],outcome="fast_failed_launch",closed=True,
        gross_return=gross,net_return=gross-.002,net_r=(gross-.002)/old["risk_pct"],
        marked_gross_return=gross,marked_net_return=gross-.002,
        hold_minutes=(datetime.fromisoformat(last["available_at"])-datetime.fromisoformat(row["entry_time"])).total_seconds()/60,
        partial_fraction=0.,exit_remaining_fraction=1.,partial_exit_time=None,partial_exit_price=None,realised_partial_gross_return=0.,
        partial_fast_fill_count=0,partial_fast_realised_net_return=0.,partial_fast_flip_count=len(log),
        partial_fast_status="failed_launch_closed",partial_fast_events=json.dumps(log),
        max_favourable_r=.2,max_adverse_r=-.1,
        transition_trigger_previous_open_time=None,transition_trigger_open_time=None,transition_trigger_available_at=None,
        failed_launch_count=1,failed_launch_status="failed_launch_closed",
        failed_launch_trigger_previous_open_time=last["previous_fast"]["open_time"],
        failed_launch_trigger_previous_available_at=last["current_fast"]["open_time"],
        failed_launch_trigger_open_time=last["current_fast"]["open_time"],failed_launch_trigger_available_at=last["available_at"],
        failed_launch_trigger_previous_side=last["previous_fast"]["side"],failed_launch_trigger_side=last["current_fast"]["side"],
        failed_launch_trigger_open_price=last["open_price"],failed_launch_trigger_gross_return=gross,
        failed_launch_slow_open_time=last["slow"]["open_time"],failed_launch_slow_available_at=last["slow_available_at"],
        failed_launch_slow_side=last["slow"]["side"],failed_launch_slow_state="aligned")
    for key in list(row):
        if key.startswith(("partial_fast_trigger_","partial_fast_slow_")):row[key]="unknown" if key.endswith("_state") else None
    return row


def assemble(cases,controls,new_cases,new_controls):
    tables,summary=f.assemble(cases,controls,new_cases,new_controls)
    summary["experiment_id"]=v.EXPERIMENT_ID
    summary["arms"]["baseline"]["policy"]=deepcopy(v.BASE_POLICY)
    summary["arms"]["candidate"]["policy"]=deepcopy(v.CANDIDATE_POLICY)
    return tables,summary


def fixture(full=False):
    n,m=(251,154) if full else (4,2)
    cases=[baseline(f.trade("case"+str(i),i*4,direction=1 if i%2==0 else -1,gross=.01 if i%2==0 else -.02),eligible=i%3!=2) for i in range(n)]
    controls=[baseline(f.trade("control{}-{}".format(i,j),4*n+4*(i*3+j),parent="case"+str(i),direction=cases[i]["direction"],gross=.01 if j else -.02),
        eligible=(i*3+j)%2==0) for i in range(m) for j in range(3)]
    new_cases=[failed(r) if i%3!=2 else opt_in(r) for i,r in enumerate(cases)]
    new_controls=[failed(r) if i%2==0 else opt_in(r) for i,r in enumerate(controls)]
    return assemble(cases,controls,new_cases,new_controls)


def run(data,full=False):
    return v.verify_tables(*data,expected_counts=(251,462,154) if full else (4,6,2))


@pytest.mark.parametrize("full",[False,True])
def test_complete_original_population_and_negative_failed_full_cost(full):
    result=run(fixture(full),full)
    assert result["counts"]==dict(cases=251 if full else 4,controls=462 if full else 6,matched=154 if full else 2,unmatched=97 if full else 2)
    assert result["effects"]["excess_delta"]["unknown_pairs"]==(97 if full else 2)
    assert result["accounting"]["serial_recomputed"] is True
    assert result["groups"]["case"]["missed_baseline_winners"]>0
    assert result["raw_replay"] is result["inferential_p_recomputed"] is False


@pytest.mark.parametrize("direction",[-1,1])
def test_exact_fee_equality_is_full_zero_not_half_or_tiny_positive(direction):
    old=baseline(f.trade("x",0,direction),price=100.+direction*.2)
    new=failed(old)
    assert new["gross_return"]==.002 and new["net_return"]==0
    edge=v.check_candidate_events(new);v.check_failed_trade(old,new,edge)
    new["net_return"]=1.7e-18
    with pytest.raises(v.VerificationError):v.check_failed_trade(old,new,edge)


@pytest.mark.parametrize("field,value",[("entry_price",101),("initial_stop",89),("signal_atr",4),("risk_pct",.2),("direction",-1),
    ("ma",101),("signal_close",101),("gross_return",.1),("net_return",.1),("net_r",2),("exit_price",100.5),
    ("hold_minutes",10),("closed",False),("partial_fraction",.5),("exit_remaining_fraction",.5),
    ("realised_partial_gross_return",.1),("partial_fast_fill_count",1),("partial_fast_realised_net_return",.1),
    ("failed_launch_count",0),("failed_launch_profit_threshold",.003),("failed_launch_enabled",False),
    ("failed_launch_trigger_side",1),("failed_launch_slow_side",-1),("failed_launch_slow_state","opposite"),
    ("max_favourable_r",9),("max_adverse_r",-9)])
def test_failed_accounting_original_entry_path_and_scalar_mutations(field,value):
    data=fixture();data[0]["candidate"]["case_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field,value",[("ma",99),("net_return",.1),("max_favourable_r",1.9),
    ("partial_fast_reset_count",3),("transition_initial_reason","fake"),("exit_time","2023-01-02T10:15:00Z")])
def test_nonfailed_preserves_every_old_column(field,value):
    data=fixture();data[0]["candidate"]["case_trades"][2][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("mutation",["not_true_edge","segment","future_slow","stale_slow","unknown","wrong_action","first_skipped","later_than_old","equal_known_exit","after_entry_zero","after_partial","duplicate_edge"])
def test_true_edge_first_logged_eligibility_and_priority(mutation):
    data=fixture();old=data[0]["baseline"]["case_trades"][0];row=data[0]["candidate"]["case_trades"][0]
    log=json.loads(row["partial_fast_events"]);edge=log[-1]
    if mutation=="not_true_edge":edge["current_fast"]["side"]=1
    elif mutation=="segment":edge["current_fast"]["management_segment_id"]="other"
    elif mutation=="future_slow":edge["slow_available_at"]=edge["available_at"]
    elif mutation=="stale_slow":edge["slow_available_at"]=edge["slow"]["open_time"]
    elif mutation=="unknown":edge["slow_state"]="unknown"
    elif mutation=="wrong_action":edge["action"]="insufficient_profit"
    elif mutation=="first_skipped":
        earlier=deepcopy(edge);earlier["action"]="slow_not_aligned";log.insert(0,earlier)
    elif mutation=="later_than_old":row["exit_time"]="2023-01-02T03:00:00Z"
    elif mutation=="equal_known_exit":old["exit_time"]=row["exit_time"]
    elif mutation=="after_entry_zero":row["exit_time"]=row["entry_time"]
    elif mutation=="after_partial":row["partial_fraction"]=.5;row["partial_fast_fill_count"]=1
    elif mutation=="duplicate_edge":log.append(deepcopy(edge))
    row["partial_fast_events"]=json.dumps(log)
    with pytest.raises(v.VerificationError):run(data)


def test_partial_first_disables_failed_full_and_remainder_unchanged():
    old=baseline(f.trade("x",0),eligible=False)
    log=json.loads(old["partial_fast_events"])
    log.append(f.edge(old,15,price=100.1,action="already_partial"))
    old.update(partial_fast_events=json.dumps(log),partial_fast_flip_count=2)
    row=opt_in(old)
    assert v.check_candidate_events(row) is None
    v.h.check_trade(row,True)
    row["failed_launch_count"]=1
    with pytest.raises(v.VerificationError):v.check_candidate_events(row)


def test_unknown_slow_may_skip_but_first_subsequent_eligible_cannot():
    old=baseline(f.trade("x",0))
    logs=json.loads(old["partial_fast_events"])
    unknown=f.edge(old,5,slow="unknown",action="slow_unknown")
    unprofitable=f.edge(old,15,price=100.1,action="insufficient_profit")
    later=f.edge(old,25)
    old["partial_fast_events"]=json.dumps([unknown,unprofitable,later]);f.fill(old,later)
    new=failed(old)
    assert new["hold_minutes"]==15
    edge=v.check_candidate_events(new);v.check_failed_trade(old,new,edge)


def test_unknown_old_counterfactual_remains_unknown_even_if_new_full_known():
    data=fixture();a=data[0]["baseline"];z=data[0]["candidate"]
    old=a["case_trades"][0];new=z["case_trades"][0]
    # The old strategy sees invalid current HLC after the same known open at
    # which the new policy already exited. New outcome can be known; D cannot.
    old.update(exit_time=new["exit_time"],exit_price=new["exit_price"],hold_minutes=5,closed=False,outcome="data_gap_censored",
        gross_return=None,net_return=None,net_r=None,partial_fraction=0.,exit_remaining_fraction=1.,
        partial_exit_time=None,partial_exit_price=None,realised_partial_gross_return=0.,
        partial_fast_fill_count=0,partial_fast_realised_net_return=0.,partial_fast_flip_count=1,partial_fast_status="unknown_source",
        marked_gross_return=.001,marked_net_return=-.001,transition_trigger_previous_open_time=None,transition_trigger_open_time=None,transition_trigger_available_at=None)
    log=json.loads(old["partial_fast_events"])[:1];old["partial_fast_events"]=json.dumps(log)
    for key in old:
        if key.startswith(("partial_fast_trigger_","partial_fast_slow_")):old[key]="unknown" if key.endswith("_state") else None
    data=assemble(a["case_trades"],a["control_trades"],z["case_trades"],z["control_trades"])
    result=run(data)
    assert result["effects"]["case_delta"]["unknown_pairs"]==1
    assert result["effects"]["serial_delta"]["unknown_pairs"]==1
    data[0]["case_delta"][0]["before"]=0
    with pytest.raises(v.VerificationError):run(data)


def test_shorter_exit_recomputes_serial_acceptance_and_all_intentions():
    data=fixture();a=data[0]["baseline"];z=data[0]["candidate"]
    a["case_trades"][1]=baseline(f.trade("case1",1,direction=-1,gross=-.02))
    z["case_trades"][1]=failed(a["case_trades"][1])
    # Controls still use the frozen direction/parent but unrelated own times.
    data=assemble(a["case_trades"],a["control_trades"],z["case_trades"],z["control_trades"])
    run(data)
    assert data[0]["baseline"]["single_pending"][1]["portfolio_selected"] is False
    assert data[0]["candidate"]["single_pending"][1]["portfolio_selected"] is True
    assert data[0]["serial_delta"][1]["before"]==0
    data[0]["candidate"]["single_pending"][1]["portfolio_selected"]=False
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("mutation",["drop_case","drop_control","control_parent","unmatched_zero","paired_omit","effect_n","serial_omit","baseline_field"])
def test_whole_denominators_frozen_triples_and_disabled_schema(mutation):
    data=fixture();t,s=data
    if mutation=="drop_case":t["candidate"]["case_trades"].pop()
    elif mutation=="drop_control":t["candidate"]["control_trades"].pop()
    elif mutation=="control_parent":t["candidate"]["control_trades"][0]["parent_event_id"]="case3"
    elif mutation=="unmatched_zero":t["candidate"]["matched"][-1]["excess"]=0
    elif mutation=="paired_omit":t["excess_delta"].pop()
    elif mutation=="effect_n":s["effects"]["case_delta"]["n"]=3
    elif mutation=="serial_omit":t["candidate"]["single_pending"].pop()
    else:t["baseline"]["case_trades"][0]["failed_launch_enabled"]=False
    with pytest.raises(v.VerificationError):run(data)


def test_exact_dependency_closure_is_two_stdlib_verifiers():
    assert Path(v.h.__file__).name=="verify_hourly_impulse_dual_partial_v16.py"
    text=(ROOT/"scripts/verify_hourly_impulse_failed_launch_v17.py").read_text()
    assert "import yoyo" not in text and "from yoyo" not in text and "simulate_events(" not in text


def test_two_file_verifier_bundle_imports_from_a_new_directory(tmp_path):
    for filename in ("verify_hourly_impulse_failed_launch_v17.py","verify_hourly_impulse_dual_partial_v16.py"):
        (tmp_path/filename).write_bytes((ROOT/"scripts"/filename).read_bytes())
    spec=importlib.util.spec_from_file_location("portable_v17",tmp_path/"verify_hourly_impulse_failed_launch_v17.py")
    portable=importlib.util.module_from_spec(spec);spec.loader.exec_module(portable)
    result=portable.verify_tables(*fixture(),expected_counts=(4,6,2))
    assert result["counts"]["unmatched"]==2 and Path(portable.h.__file__).parent==tmp_path


@pytest.mark.parametrize("field,value",[("partial_fast_enabled",False),("partial_fast_fraction",.25),("partial_fast_profit_threshold",.003),
    ("partial_fast_reset_count",-1),("partial_fast_first_armed_at","2023-01-02T00:05:00Z")])
def test_failed_prefix_preserves_partial_policy_and_causal_arming(field,value):
    data=fixture();data[0]["candidate"]["case_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


def source_fixture(tmp_path,monkeypatch):
    monkeypatch.setattr(f.v,"EXPERIMENT_ID",v.EXPERIMENT_ID)
    results,summary,contents,started=f.source_fixture(tmp_path,monkeypatch)
    config=json.loads((results.parent/"config.json").read_text())
    config.update(policies=[v.BASE_POLICY,v.CANDIDATE_POLICY],parent_results=v.PARENT)
    target=tmp_path/v.PARENT/"saved.json";target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes((tmp_path/"experiments/active/old/results/saved.json").read_bytes())
    (results.parent/"config.json").write_text(json.dumps(config))
    config_id=str((results.parent/"config.json").relative_to(tmp_path))
    contents[config_id]=(results.parent/"config.json").read_bytes()
    contents["yoyo/evaluation/hourly_impulse_failed_launch_research.py"]=contents.pop("yoyo/evaluation/hourly_impulse_dual_partial_research.py")
    path=tmp_path/"yoyo/evaluation/hourly_impulse_failed_launch_research.py";path.write_bytes(contents[str(path.relative_to(tmp_path))])
    sources=[dict(path=name,sha256=v.hashlib.sha256(content).hexdigest()) for name,content in contents.items()]
    started["sources"]=sources;summary["sources"]=sources
    summary["config_sha256"]=v.sha(results.parent/"config.json")
    (results/"started.json").write_text(json.dumps(started))
    summary["output_hashes"]["started.json"]=v.sha(results/"started.json")
    (results/"summary.json").write_text(json.dumps(summary))
    return results,summary,contents,started


def test_sources_no_hardcoded_count_and_auditor_not_pretend_builder(tmp_path,monkeypatch):
    results,summary,_,_=source_fixture(tmp_path,monkeypatch)
    answer=v.verify_sources(tmp_path,results,summary)
    assert answer["committed_sources_verified"]==5 and answer["output_hashes_verified"]==1
    assert not any("verify_hourly" in row["path"] for row in answer["source_pins"])


@pytest.mark.parametrize("mutation",["source_hash","parent_hash","config","extra_output","source_missing","bad_output_hash"])
def test_source_hash_lineage_failures_never_silently_skipped(tmp_path,monkeypatch,mutation):
    results,summary,contents,started=source_fixture(tmp_path,monkeypatch)
    if mutation=="source_hash":contents["yoyo/layers/l3_backtest/hourly_impulse.py"]=b"changed"
    elif mutation=="parent_hash":(tmp_path/v.PARENT/"saved.json").write_text("changed")
    elif mutation=="config":(results.parent/"config.json").write_text("{}")
    elif mutation=="extra_output":(results/"extra.json").write_text("{}")
    elif mutation=="bad_output_hash":summary["output_hashes"]["started.json"]="0"*64
    else:
        summary["sources"].pop(0);started["sources"]=summary["sources"]
        (results/"started.json").write_text(json.dumps(started));summary["output_hashes"]["started.json"]=v.sha(results/"started.json")
    with pytest.raises((v.VerificationError,KeyError)):v.verify_sources(tmp_path,results,summary)


def export_fixture(tmp_path):
    # Reuse only temporary saved-file fixture plumbing, not old strategy rules.
    results,_,_,_,write_csv=f.lineage_fixture(tmp_path)
    new_parent=tmp_path/"experiments/active"/v.EXPERIMENT_ID
    results.parent.rename(new_parent);results=new_parent/"results"
    tables,summary=fixture();native,fast=f.context_rows(tables)
    config=json.loads((results.parent/"config.json").read_text());config["parent_results"]=v.PARENT
    (results.parent/"config.json").write_text(json.dumps(config))
    for arm in v.ARMS:
        for table,file in v.TABLE_FILES.items():
            write_csv(results/arm/file,tables[arm][table])
            if arm=="baseline":write_csv(tmp_path/v.PARENT/file,tables[arm][table])
    for name in v.DELTAS:write_csv(results/(name+".csv"),tables[name])
    anchor={key:dict(rows=len(rows),columns=len(rows[0])) for key,rows in tables["baseline"].items()}
    (results/"anchor_parity.json").write_text(json.dumps(anchor))
    write_csv(results/"native_entry_context.csv.gz",native);write_csv(results/"fast_entry_context.csv.gz",fast)
    frozen=json.loads((results/"context_frozen.json").read_text())
    frozen.update(context_sha256=v.sha(results/"native_entry_context.csv.gz"),fast_context_sha256=v.sha(results/"fast_entry_context.csv.gz"))
    (results/"context_frozen.json").write_text(json.dumps(frozen));summary["native_context"]=frozen["counts"]
    flat=[]
    for arm in v.ARMS:
        for population in ("case","control"):
            for row in tables[arm][population+"_trades"]:
                for edge in json.loads(row["partial_fast_events"]):
                    edge=deepcopy(edge)
                    for field in ("previous_fast","current_fast","slow"):edge[field]=json.dumps(edge[field])
                    flat.append(dict(arm=arm,population=population,event_id=row["event_id"],**edge))
    write_csv(results/"fast_edges.csv.gz",flat)
    summary["mechanics"]={}
    for population in ("case","control"):
        rows=[]
        for a,z in zip(tables["baseline"][population+"_trades"],tables["candidate"][population+"_trades"]):
            b,n=a["net_return"],z["net_return"];d=n-b if b is not None and n is not None else None
            if d is None:b,n=None,None
            transition="flat_or_unknown" if b is None or n is None or b==0 or n==0 else ("win" if b>0 else "loss")+"_to_"+("win" if n>0 else "loss")
            row=dict(event_id=a["event_id"],mother_decision_time=a["mother_decision_time"],baseline_net_bp=b*1e4 if b is not None else None,
                candidate_net_bp=n*1e4 if n is not None else None,delta_net_bp=d*1e4 if d is not None else None,
                exit_delay_minutes=z["hold_minutes"]-a["hold_minutes"],outcome_transition=transition,
                failed_launch_executed=z["outcome"]=="fast_failed_launch",sacrificed_recovery=z["outcome"]=="fast_failed_launch" and b is not None and b>0 and n<=1e-12,
                prior_partial_path_cut=z["outcome"]=="fast_failed_launch" and a["partial_fraction"]==.5)
            for arm,t in (("baseline",a),("candidate",z)):
                row.update({arm+"_exit_time":t["exit_time"],arm+"_exit_reason":t["outcome"],arm+"_mfe_r":t["max_favourable_r"],
                    arm+"_hold_minutes":t["hold_minutes"],arm+"_partial_executed":t["partial_fraction"]==.5,arm+"_partial_exit_time":t["partial_exit_time"]})
            rows.append(row)
        categories=defaultdict(list)
        for row in rows:categories[row["outcome_transition"]].append(row)
        groups=[]
        for label,part in categories.items():
            ds=[r["delta_net_bp"] for r in part if r["delta_net_bp"] is not None]
            groups.append(dict(group=label,n=len(part),known=len(ds),old_mean_net_bp=sum(r["baseline_net_bp"] for r in part)/len(part),
                new_mean_net_bp=sum(r["candidate_net_bp"] for r in part)/len(part),mean_delta_bp=sum(ds)/len(ds),sum_delta_event_bp=sum(ds)))
        failed_rows=[r for r in rows if r["failed_launch_executed"]]
        summary["mechanics"][population]=dict(total=len(rows),known=len(rows),transitions=dict(Counter(r["outcome_transition"] for r in rows)),groups=groups,
            failed_launch_count=len(failed_rows),unchanged_paths=len(rows)-len(failed_rows),failed_improved=sum(r["delta_net_bp"]>1e-8 for r in failed_rows),
            failed_hurt=sum(r["delta_net_bp"]< -1e-8 for r in failed_rows),failed_unknown_pairs=0,sacrificed_recoveries=sum(r["sacrificed_recovery"] for r in rows),
            prior_partial_paths_cut=sum(r["prior_partial_path_cut"] for r in rows),baseline_partial_count=sum(r["baseline_partial_executed"] for r in rows),
            candidate_partial_count=sum(r["candidate_partial_executed"] for r in rows),later_exits=0,
            earlier_exits=sum(r["exit_delay_minutes"]<0 for r in rows),same_exit_time=sum(r["exit_delay_minutes"]==0 for r in rows))
        write_csv(results/("failed_launch_"+population+"_mechanics.csv"),rows)
        write_csv(results/("failed_launch_"+population+"_groups.csv"),groups)
    monthly=[]
    for arm in v.ARMS:
        values=[r["net_return"] for r in tables[arm]["case_trades"]]
        monthly.append(dict(arm=arm,fold="2023H1",month="2023-01",n=len(values),known=len(values),mean_net_bp=sum(values)/len(values)*1e4))
    write_csv(results/"monthly_case_net.csv",monthly)
    return results,v.load_tables(results),summary,write_csv


def test_saved_lineage_mechanics_and_monthly_exports(tmp_path):
    results,tables,summary,_=export_fixture(tmp_path)
    run((tables,summary))
    lineage=v.verify_lineage(tmp_path,results,tables,summary)
    assert lineage["anchor_tables"]==6 and lineage["native_context_rows"]==20 and lineage["fast_context_rows"]==10
    assert v.verify_mechanics_exports(results,tables,summary)==dict(case_mechanics_rows=4,control_mechanics_rows=6,monthly_rows=2)


@pytest.mark.parametrize("mutation",["missed_winners","group_mean","monthly_n","monthly_mean","partial_cut","edge_drop","edge_arm","freeze_sha","anchor_column"])
def test_saved_mechanics_lineage_corruptions(tmp_path,mutation):
    results,tables,summary,write_csv=export_fixture(tmp_path)
    if mutation=="missed_winners":summary["mechanics"]["case"]["sacrificed_recoveries"]+=1
    elif mutation=="group_mean":summary["mechanics"]["case"]["groups"][0]["mean_delta_bp"]+=1
    elif mutation.startswith("monthly"):
        rows=v.read_csv(results/"monthly_case_net.csv");rows[0]["n" if mutation=="monthly_n" else "mean_net_bp"]="999";write_csv(results/"monthly_case_net.csv",rows)
    elif mutation=="partial_cut":
        rows=v.read_csv(results/"failed_launch_case_mechanics.csv");rows[0]["prior_partial_path_cut"]="False";write_csv(results/"failed_launch_case_mechanics.csv",rows)
    elif mutation.startswith("edge"):
        rows=v.read_csv(results/"fast_edges.csv.gz")
        if mutation=="edge_drop":rows.pop()
        else:rows[0]["arm"]="other"
        write_csv(results/"fast_edges.csv.gz",rows)
    elif mutation=="freeze_sha":
        frozen=json.loads((results/"context_frozen.json").read_text());frozen["fast_context_sha256"]="0"*64;(results/"context_frozen.json").write_text(json.dumps(frozen))
    else:del tables["baseline"]["case_trades"][0]["max_adverse_r"]
    with pytest.raises((v.VerificationError,KeyError)):
        run((tables,summary));v.verify_lineage(tmp_path,results,tables,summary);v.verify_mechanics_exports(results,tables,summary)

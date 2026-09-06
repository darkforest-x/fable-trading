"""V15 synthetic saved-row counterexamples; no market or historical reads."""
from copy import deepcopy
from datetime import datetime,timedelta
from datetime import timezone
from collections import Counter,defaultdict
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_native_v15", ROOT/"scripts/verify_hourly_impulse_native_exit_v15.py")
v = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(v)
FSPEC = importlib.util.spec_from_file_location("synthetic_frozen_fixtures", ROOT/"tests/test_verify_hourly_impulse_frozen_ma_v12.py")
f = importlib.util.module_from_spec(FSPEC); FSPEC.loader.exec_module(f)


def native(row, minutes=15, *, hold=None, gross=None, state="aligned"):
    row = deepcopy(row)
    start = datetime.fromisoformat(row["entry_time"])
    hold = row["hold_minutes"] if hold is None else hold
    end = start+timedelta(minutes=hold)
    gross = row["gross_return"] if gross is None else gross
    row.update(hold_minutes=hold,exit_time=end.isoformat(),exit_price=row["entry_price"]*(1+row["direction"]*gross),
        gross_return=gross,net_return=gross-.002,net_r=(gross-.002)/row["risk_pct"],
        transition_initial_state=state,transition_initial_side=row["direction"] if state=="aligned" else -row["direction"] if state=="opposite" else None,
        transition_initial_reason="valid" if state!="unknown" else "missing_management",
        transition_initial_open_time=(start-timedelta(minutes=minutes)).isoformat() if state!="unknown" else "",
        transition_trigger_previous_open_time=(end-timedelta(minutes=2*minutes)).isoformat(),
        transition_trigger_open_time=(end-timedelta(minutes=minutes)).isoformat(),transition_trigger_available_at=end.isoformat(),
        max_favourable_r=max(1.,hold/60))
    return row


def fixture(*, full=False):
    n,m = (251,154) if full else (3,2)
    cases = [native(f.trade("case"+str(i),4*i,direction=1 if i%2==0 else -1,gross=.003 if i%2==0 else -.004),5) for i in range(n)]
    controls = [native(f.trade("case{}::control{}".format(i,j),4*n+4*(i*3+j),parent="case"+str(i),
        direction=cases[i]["direction"],gross=.003 if j else -.002),5) for i in range(m) for j in range(3)]
    return f.assemble(cases,controls,[native(row,hold=120 if i%2==0 else 60,gross=.005 if i%2==0 else -.003) for i,row in enumerate(cases)],
        [native(row) for row in controls])


def run(data, *, full=False):
    return v.verify_tables(*data,expected_counts=(251,462,154) if full else (3,6,2))


def contexts(data):
    tables = data[0]
    originals = {label:[{field:row[field] for field in ("event_id","decision_time","direction")} for row in tables["baseline"][label+"_trades"]]
        for label in ("case","control")}
    rows = []
    for arm,minutes in zip(v.ARMS,(5,15)):
        for label in originals:
            for trade in tables[arm][label+"_trades"]:
                side=trade["transition_initial_side"]
                state=trade["transition_initial_state"]
                start=datetime.fromisoformat(trade["entry_time"])
                rows.append(dict(event_id=trade["event_id"],arm=arm,population=label,decision_time=trade["decision_time"],direction=trade["direction"],
                    mg_entry_side=side,mg_entry_aligned=state=="aligned" if state!="unknown" else None,
                    mg_entry_state=state,mg_entry_bar_open=(start-timedelta(minutes=minutes)).isoformat(),mg_entry_available_at=start.isoformat(),
                    mg_entry_reason=trade["transition_initial_reason"],mg_entry_known=state!="unknown",mg_entry_ma=100.,
                    mg_entry_hl2=101. if side==1 else 99.,mg_entry_management_segment_id="native-7",mg_entry_raw_segment_id="source-12",
                    mg_entry_native_minutes=minutes))
                trade.update({field:rows[-1][field] for field in v.MG_FIELDS})
    return rows, originals


def mechanics(data):
    rows=[]
    for a,z in zip(data[0]["baseline"]["case_trades"],data[0]["candidate"]["case_trades"]):
        before,after=a["net_return"],z["net_return"]
        transition="flat_or_unknown" if before is None or after is None or before==0 or after==0 else \
            ("win" if before>0 else "loss")+"_to_"+("win" if after>0 else "loss")
        rows.append(dict(event_id=a["event_id"],mother_decision_time=a["mother_decision_time"],baseline_net_bp=before*1e4,
            candidate_net_bp=after*1e4,delta_net_bp=(after-before)*1e4,baseline_exit_time=a["exit_time"],candidate_exit_time=z["exit_time"],
            exit_delay_minutes=z["hold_minutes"]-a["hold_minutes"],baseline_exit_reason=a["outcome"],candidate_exit_reason=z["outcome"],
            outcome_transition=transition,baseline_mfe_r=a["max_favourable_r"],candidate_mfe_r=z["max_favourable_r"],
            baseline_hold_minutes=a["hold_minutes"],candidate_hold_minutes=z["hold_minutes"]))
    return rows


@pytest.mark.parametrize("full",[False,True])
def test_fixed_complete_opportunities_native_context_and_clocks(full):
    data=fixture(full=full)
    result=run(data,full=full)
    assert result["effects"]["excess_delta"]["unknown_pairs"]==(97 if full else 1)
    assert result["raw_replay"] is result["inferential_p_recomputed"] is False
    rows,originals=contexts(data)
    counts=v.verify_native_context(rows,originals,data[0])
    assert counts["candidate/case"]["n"]==(251 if full else 3)
    assert v.verify_mechanics(mechanics(data),data[0])["later"]==(126 if full else 2)


@pytest.mark.parametrize("field,value",[("entry_price",101),("initial_stop",89),("signal_atr",5),("risk_pct",.2),
    ("risk_atr",3),("direction",-1),("net_return",.1),("gross_return",.1),("net_r",4),("hold_minutes",121),
    ("partial_fraction",.5),("funding_modelled",True),("closed",False),("ma",99),("signal_time","2023-01-01T22:00:00Z")])
def test_cost_and_original_entry_drift(field,value):
    data=fixture();data[0]["candidate"]["case_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field,value",[("transition_initial_open_time","2023-01-01T23:55:00Z"),
    ("transition_trigger_previous_open_time","2023-01-02T01:50:00Z"),
    ("transition_trigger_open_time","2023-01-02T01:55:00Z"),
    ("transition_trigger_available_at","2023-01-02T02:00:00.000000001Z"),
    ("transition_trigger_available_at",None),("outcome","colour_exit"),("launch_enabled",True),
    ("frozen_ma_enabled",True),("transition_decision_minutes",15)])
def test_fake_native15_or_different_exit_rejected(field,value):
    data=fixture();data[0]["candidate"]["case_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("minutes",[5,15])
@pytest.mark.parametrize("direction",[-1,1])
def test_first_native_exit_is_one_complete_post_entry_bar(minutes,direction):
    row=native(f.trade("x",0,direction=direction),minutes,hold=minutes)
    v.check_native_clock(row,minutes)
    for hold in (0,5 if minutes==15 else 1):
        early=native(row,minutes,hold=hold)
        with pytest.raises(v.VerificationError):v.check_native_clock(early,minutes)


@pytest.mark.parametrize("field,value",[("mg_entry_known",False),("mg_entry_known",1),("mg_entry_side",0),("mg_entry_side",None),
    ("mg_entry_ma",0),("mg_entry_hl2",98),("mg_entry_aligned",False),("mg_entry_state","opposite"),
    ("mg_entry_native_minutes",5),("mg_entry_management_segment_id",None),("mg_entry_reason","missing_management"),
    ("mg_entry_available_at","2023-01-02T00:00:00.000000001Z"),
    ("mg_entry_bar_open","2023-01-01T23:55:00Z"),("decision_time","2023-01-02T00:05:00Z")])
def test_native_seed_drift(field,value):
    data=fixture();rows,orig=contexts(data);row=next(x for x in rows if x["arm"]=="candidate")
    row[field]=value
    with pytest.raises(v.VerificationError):v.verify_native_context(rows,orig,data[0])


def test_native15_seed_can_differ_from5m_and_unknown_keeps_diagnostics():
    data=fixture()
    row=data[0]["candidate"]["case_trades"][0]
    row.update(native(row,state="opposite"))
    rows,orig=contexts(data);v.verify_native_context(rows,orig,data[0])
    row.update(native(row,state="unknown"))
    rows,orig=contexts(data)
    unknown=next(x for x in rows if x["arm"]=="candidate")
    assert unknown["mg_entry_ma"] is not None and unknown["mg_entry_bar_open"]
    v.verify_native_context(rows,orig,data[0])
    unknown["mg_entry_side"]=0
    with pytest.raises(v.VerificationError):v.verify_native_context(rows,orig,data[0])


@pytest.mark.parametrize("mutation",["drop_case","drop_control","control_parent","reused_time","unmatched_zero","delta_zero","effect_n"])
def test_fixed_control_and_unknown_denominators(mutation):
    data=fixture();t=data[0]
    if mutation=="drop_case":t["candidate"]["case_trades"].pop()
    elif mutation=="drop_control":t["candidate"]["control_trades"].pop()
    elif mutation=="control_parent":t["candidate"]["control_trades"][0]["parent_event_id"]="case2"
    elif mutation=="reused_time":t["candidate"]["control_trades"][0]["decision_time"]=t["candidate"]["control_trades"][1]["decision_time"]
    elif mutation=="unmatched_zero":t["candidate"]["matched"][-1]["control_mean_return"]=0
    elif mutation=="delta_zero":t["excess_delta"][-1]["difference"]=0
    else:data[2]["excess_delta"]["n"]=3
    with pytest.raises(v.VerificationError):run(data)


def test_longer_management_recomputes_serial_not_old_selection():
    data=fixture();t=data[0]
    old=t["baseline"]["case_trades"];new=t["candidate"]["case_trades"]
    new[0]=native(old[0],hold=300)
    data=f.assemble(old,t["baseline"]["control_trades"],new,t["candidate"]["control_trades"])
    run(data)
    assert not data[0]["candidate"]["single_pending"][1]["portfolio_selected"]
    data[0]["candidate"]["single_pending"][1]["portfolio_selected"]=True
    with pytest.raises(v.VerificationError):run(data)


def test_unknown_selected_preserves_nan_but_busy_skip_is_known_zero():
    data=fixture();t=data[0]
    unknown=t["candidate"]["case_trades"][0]
    unknown.update(closed=False,outcome="data_gap_censored",gross_return=None,net_return=None,net_r=None,
        transition_trigger_previous_open_time="",transition_trigger_open_time="",transition_trigger_available_at="")
    data=f.assemble(t["baseline"]["case_trades"],t["baseline"]["control_trades"],t["candidate"]["case_trades"],t["candidate"]["control_trades"])
    result=run(data)
    assert result["effects"]["case_delta"]["unknown_pairs"]==1
    assert result["effects"]["serial_delta"]["unknown_pairs"]==1
    data[0]["candidate"]["case_episodes"][0]["episode_net_return"]=0
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field,value",[("baseline_net_bp",1),("candidate_net_bp",0),("delta_net_bp",0),("exit_delay_minutes",0),
    ("baseline_exit_reason","hard_stop"),("candidate_exit_reason","colour_exit"),("outcome_transition","loss_to_win"),
    ("baseline_mfe_r",0),("candidate_hold_minutes",2),("candidate_exit_time","2023-01-02T02:00:00.000000001Z")])
def test_mechanics_fields_cannot_override_source(field,value):
    data=fixture();rows=mechanics(data);rows[0][field]=value
    with pytest.raises(v.VerificationError):v.verify_mechanics(rows,data[0])


def test_same_fill_and_hard_stop_unchanged_across_arms():
    a=native(f.trade("x",0),5);z=native(a)
    v.check_paired_path(a,z)
    z["exit_price"]+=1
    with pytest.raises(v.VerificationError):v.check_paired_path(a,z)
    z=native(a,hold=120);a["outcome"]="hard_stop"
    with pytest.raises(v.VerificationError):v.check_paired_path(a,z)


def test_offquarter_hard_stop_legal_and_colour_gap_priority():
    row=native(f.trade("x",0),15,hold=20,gross=-.1)
    row.update(outcome="hard_stop",exit_price=row["initial_stop"],transition_trigger_previous_open_time="",
        transition_trigger_open_time="",transition_trigger_available_at="")
    v.check_native_clock(row,15)
    row=native(f.trade("x",0),15,hold=30,gross=-.1)
    with pytest.raises(v.VerificationError):v.check_native_clock(row,15)


def mechanics_summary(rows):
    grouped=defaultdict(list)
    for row in rows:grouped[row["outcome_transition"]].append(row)
    groups=[]
    for name,part in sorted(grouped.items()):
        vals=[row["delta_net_bp"] for row in part if row["delta_net_bp"] is not None]
        groups.append(dict(group=name,n=len(part),known=len(vals),old_mean_net_bp=v.mean([r["baseline_net_bp"] for r in part]),
            new_mean_net_bp=v.mean([r["candidate_net_bp"] for r in part]),mean_delta_bp=v.mean(vals),sum_delta_event_bp=sum(vals) if vals else None))
    return dict(total=len(rows),known=sum(row["delta_net_bp"] is not None for row in rows),
        transitions=dict(Counter(row["outcome_transition"] for row in rows)),groups=groups,
        later_exits=sum(row["exit_delay_minutes"]>0 for row in rows),earlier_exits=sum(row["exit_delay_minutes"]<0 for row in rows),
        same_exit_time=sum(row["exit_delay_minutes"]==0 for row in rows))


def disk_fixture(tmp_path,monkeypatch):
    data=fixture(full=True);tables,arms,effects=data
    native_rows,orig=contexts(data)
    results=tmp_path/v.EXPERIMENT_PATH/"results"
    olddir,motherdir=tmp_path/v.PARENT_PATH,tmp_path/v.MOTHER_PATH
    assignments=[dict(event_id=row["event_id"],match_status="matched" if i<154 else "unmatchable") for i,row in enumerate(orig["case"])]
    for label in ("case","control"):
        # Original requests contain all known-at-entry fields, never outcomes.
        orig[label]=[{key:r[key] for key in v.FIXED_FIELDS if key not in ("entry_time","entry_price","risk_pct","risk_atr")}
            | ({"parent_event_id":r["parent_event_id"]} if label=="control" else {}) for r in tables["baseline"][label+"_trades"]]
        f.write_csv(olddir/("direct_k1_stop_"+label+"_context.csv.gz"),orig[label])
        f.write_csv(motherdir/("original_mothers.csv.gz" if label=="case" else "control_mothers.csv.gz"),orig[label])
        f.write_csv(results/(label+"_context.csv.gz"),orig[label])
    for name,file in v.TABLE_FILES.items():
        old=[{k:x for k,x in row.items() if k not in v.MG_FIELDS} for row in tables["baseline"][name]]
        f.write_csv(olddir/("direct_k1_stop__transition_colour_"+file),old)
        for arm in v.ARMS:f.write_csv(results/arm/file,tables[arm][name])
    f.write_json(olddir/"summary.json",{"old":"synthetic"});f.write_json(motherdir/"assignment_receipt.json",{"n":154})
    f.write_csv(motherdir/"assignments.csv",assignments);f.write_csv(results/"assignments.csv",assignments)
    for name in ("case_delta","excess_delta","serial_delta"):f.write_csv(results/(name+".csv"),tables[name])
    f.write_json(results/"anchor_parity.json",{name:dict(rows=len(tables["baseline"][name]),
        columns=len([k for k in tables["baseline"][name][0] if k not in v.MG_FIELDS])) for name in v.TABLE_FILES})
    f.write_csv(results/"native_entry_context.csv.gz",native_rows)
    counts=Counter((r["arm"],r["population"],r["mg_entry_state"]) for r in native_rows)
    counts=[dict(arm=a,population=p,mg_entry_state=s,n=n) for (a,p,s),n in sorted(counts.items())]
    f.write_csv(results/"native_initial_state_counts.csv",counts)
    f.write_json(results/"context_frozen.json",dict(at="2026-09-06T01:00:01Z",before_outcome_reads=True,
        outcomes_hashed_or_read=False,entry_gates=False,rows=1426,counts=counts,context_sha256=v.sha(results/"native_entry_context.csv.gz")))
    rows=mechanics(data);ms=mechanics_summary(rows)
    f.write_csv(results/"native_exit_mechanics.csv",rows);f.write_csv(results/"mechanism_groups.csv",ms["groups"])
    f.write_csv(results/"monthly_case_net.csv",[r for r in f.monthly(data) if r["n"]])
    semantics={}
    for label in ("case","control"):
        state=deepcopy(tables["candidate"][label+"_trades"])
        for row in state:row["outcome"]="colour_exit"
        fake=({"baseline":{"case_trades":state},"candidate":{"case_trades":tables["candidate"][label+"_trades"]}},)
        rows=mechanics(fake);semantics[label]={**mechanics_summary(rows),"same_net":len(rows)}
        f.write_csv(results/("semantic_state15_"+label+"_trades.csv.gz"),state)
        f.write_csv(results/("semantic_state15_"+label+"_delta.csv"),rows)
    base=dict(execution=dict(cost_fraction=.002,max_hours=72,stop_first=True),
        development_folds=[[fold,a,z] for fold,(a,z) in v.FOLDS.items()],source={"sha256":"synthetic-source-not-read"})
    f.write_json(tmp_path/v.BASE_PATH,base)
    config=dict(experiment_id=v.EXPERIMENT_ID,base_config=v.BASE_PATH,base_config_sha256=v.sha(tmp_path/v.BASE_PATH),
        parent_results=v.PARENT_PATH,mother_results=v.MOTHER_PATH,inputs={p.name:v.sha(p) for p in olddir.iterdir()},
        mother_inputs={p.name:v.sha(p) for p in motherdir.iterdir()},policies=v.POLICIES,native_contract=v.NATIVE_CONTRACT,
        known_support=dict(cases=251,controls=462,matched=154,coverage_gate_unattainable=True),
        inference=dict(draws=9999,seed=20260906,p_limit=.01,joint_required=["case_delta","excess_delta"],method="month_cluster"),
        selection=dict(minimum_events=80,minimum_per_fold=12,positive_folds=4,minimum_profit_factor=1.1,
            minimum_active_months=12,minimum_months_per_fold=3,matched_coverage=.9),
        no_audit_entry_point=True,holdout_consumed=False,production_eligible=False,training_eligible=False)
    f.write_json(tmp_path/v.EXPERIMENT_PATH/"config.json",config)
    sourcepaths=v.REQUIRED_CODE_SOURCES|{v.EXPERIMENT_PATH+"/config.json",v.EXPERIMENT_PATH+"/PROJECT_PLAN.md",v.BASE_PATH}
    committed={path:(tmp_path/path).read_bytes() if (tmp_path/path).exists() else ("synthetic source "+path).encode() for path in sourcepaths}
    sources=[dict(path=path,sha256=hashlib.sha256(content).hexdigest()) for path,content in sorted(committed.items())]
    f.write_json(results/"started.json",dict(at="2026-09-06T01:00:00Z",builder_commit="a"*40,sources=sources))
    for arm,policy in zip(v.ARMS,v.POLICIES):
        arms[arm]["policy"]=policy;f.write_json(results/arm/"summary.json",arms[arm])
    summary=dict(experiment_id=v.EXPERIMENT_ID,status="diagnostic_only_no_candidate_acceptance",arms=arms,effects=effects,
        holdout_consumed=False,production_eligible=False,training_eligible=False,audit_prices_loaded=False,
        config_sha256=v.sha(tmp_path/v.EXPERIMENT_PATH/"config.json"),native_context=counts,mechanics=ms,semantics=semantics,
        known_coverage_ceiling=154/251,coverage_required=.9,source=dict(sha256=base["source"]["sha256"],holdout_price_rows=0,
        phase_price_last_open="2024-12-31T23:55:00Z"),inputs=config["inputs"],mother_inputs=config["mother_inputs"],
        sources=sources,gates=dict(matched_coverage=False),all_financial_gates_pass=False)
    def refresh():
        summary["output_hashes"]={str(p.relative_to(results)):v.sha(p) for p in results.rglob("*") if p.is_file() and p!=results/"summary.json"}
        f.write_json(results/"summary.json",summary)
    refresh()
    def fake_git(command,**kwargs):
        assert command[:2]==["git","show"]
        if "--format=%ct" in command:return SimpleNamespace(stdout=str(int(datetime(2026,9,6,tzinfo=timezone.utc).timestamp())))
        commit,path=command[2].split(":",1);assert commit=="a"*40 and not path.startswith("data/")
        return SimpleNamespace(stdout=committed[path])
    monkeypatch.setattr(v.b.subprocess,"run",fake_git)
    return results,summary,config,committed,refresh


def test_full_directory_preserves_all_files_and_validates_committed_sources(tmp_path,monkeypatch):
    results,summary,config,committed,refresh=disk_fixture(tmp_path,monkeypatch)
    before={str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    output=v.verify(tmp_path)
    assert output["counts"]==dict(cases=251,controls=462,matched=154,unmatched=97)
    assert output["context_receipt"]["rows"]==1426 and output["committed_sources_verified"]==21
    assert output["effects"]["excess_delta"]["n"]==154
    assert {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}==before


@pytest.mark.parametrize("mutation",["output_hash","extra_output","missing_output","input_hash","source_bytes","omitted_source",
    "config_file","committed_config","context_hash","context_late_order","context_selected","counts","support_promotion","failure","semantic_delta","anchor"])
def test_directory_corruption_fails_closed(tmp_path,monkeypatch,mutation):
    results,summary,config,committed,refresh=disk_fixture(tmp_path,monkeypatch)
    if mutation=="output_hash":(results/"case_delta.csv").write_text("damaged")
    elif mutation=="extra_output":(results/"stray.csv").write_text("stray")
    elif mutation=="missing_output":(results/"case_delta.csv").unlink()
    elif mutation=="input_hash":(tmp_path/v.PARENT_PATH/"summary.json").write_text("changed old evidence")
    elif mutation=="source_bytes":committed["yoyo/layers/l3_backtest/hourly_impulse.py"]+=b"changed"
    elif mutation=="omitted_source":
        summary["sources"]=summary["sources"][1:];start=v.read_json(results/"started.json");start["sources"]=summary["sources"]
        f.write_json(results/"started.json",start);refresh()
    elif mutation=="config_file":
        config["policies"]=deepcopy(config["policies"]);config["policies"][1]["decision_minutes"]=15
        f.write_json(tmp_path/v.EXPERIMENT_PATH/"config.json",config)
    elif mutation=="committed_config":
        path=v.EXPERIMENT_PATH+"/config.json";committed[path]+=b"\n"
        for source in summary["sources"]:
            if source["path"]==path:source["sha256"]=hashlib.sha256(committed[path]).hexdigest()
        start=v.read_json(results/"started.json");start["sources"]=summary["sources"];f.write_json(results/"started.json",start);refresh()
    elif mutation in ("context_hash","context_late_order","context_selected"):
        path=results/"context_frozen.json";row=v.read_json(path)
        row[{"context_hash":"context_sha256","context_late_order":"outcomes_hashed_or_read","context_selected":"entry_gates"}[mutation]]="wrong" if mutation=="context_hash" else True
        f.write_json(path,row);refresh()
    elif mutation=="counts":summary["native_context"]=deepcopy(summary["native_context"]);summary["native_context"][0]["n"]-=1;refresh()
    elif mutation=="support_promotion":summary["all_financial_gates_pass"]=True;refresh()
    elif mutation=="semantic_delta":
        path=results/"semantic_state15_case_delta.csv";rows=v.read_csv(path);rows[0]["delta_net_bp"]=1;f.write_csv(path,rows);refresh()
    elif mutation=="anchor":
        path=results/"anchor_parity.json";row=v.read_json(path);row["case_trades"]["columns"]-=1;f.write_json(path,row);refresh()
    else:f.write_json(results/"failure.json",{"status":"failed"});refresh()
    with pytest.raises(v.VerificationError):v.verify(tmp_path)


def test_missing_results_fail_closed_without_writes(tmp_path):
    with pytest.raises(v.VerificationError):v.verify(tmp_path)
    assert not list(tmp_path.rglob("*"))

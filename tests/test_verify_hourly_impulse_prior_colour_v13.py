"""Synthetic three-state opportunity accounting and frozen-source corruptions.

Generic V12 test fixtures provide original artificial V5 trades only; no actual
results, source prices, research runner or gate helper are imported or executed.
"""
from collections import Counter,defaultdict
from copy import deepcopy
from datetime import datetime,timedelta
import csv
import gzip
import hashlib
import importlib.util
from itertools import product
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
v=load("verify_prior_colour",ROOT/"scripts/verify_hourly_impulse_prior_colour_v13.py")
f=load("synthetic_old_fixtures",ROOT/"tests/test_verify_hourly_impulse_frozen_ma_v12.py")


def gate(context,state):
    source=deepcopy(context)
    signal=datetime.fromisoformat(source["signal_time"])
    available=signal.replace(hour=signal.hour//4*4,minute=0,second=0,microsecond=0)
    side=source["direction"]*(1 if state=="accepted" else -1)
    source.update(prior_colour_bar_open=(available-timedelta(hours=4)).isoformat(),prior_colour_available_at=available.isoformat(),
        prior_colour_ma=100.,prior_colour_hl2=100.+10*side,prior_colour_side=None if state=="unknown" else side,
        prior_colour_known=state!="unknown",prior_colour_reason="warmup" if state=="unknown" else "known",
        prior_colour_count=39 if state=="unknown" else 47,prior_colour_raw_segment_id=3,prior_colour_gate_state=state)
    return source


def fixture(*,full=False,case_states=None,control_states=None):
    base=f.fixture(full=full)
    contexts,assignments,unused,unused_summary=f.geometry(base)
    old=deepcopy(base[0]["baseline"])
    gates=[];new={};counts={}
    for label,states in (("case",case_states),("control",control_states)):
        n=len(contexts[label])
        if states is None:states=["abstain" if i==0 else "accepted" for i in range(n)]
        assert len(states)==n
        admitted=[];episodes=[]
        for context,trade,episode,state in zip(contexts[label],old[label+"_trades"],old[label+"_episodes"],states):
            g=gate(context,state);gates.append(dict(g,population=label))
            row=deepcopy(episode)
            row.update({k:value for k,value in g.items() if k.startswith("prior_colour_")})
            row["policy_fee_fraction"]=.002
            if state=="accepted":admitted.append(deepcopy(trade))
            else:
                row.update(status="prior_colour_"+state,episode_status="prior_colour_"+state,episode_net_return=0. if state=="abstain" else None,
                    policy_fee_fraction=0. if state=="abstain" else None,observed=state=="abstain",executed=False,completed_trade=False,
                    entry_time=None,exit_time=None,terminal_time=row["mother_decision_time"],
                    occupied_until=row["mother_decision_time"] if state=="abstain" else row["mother_deadline"])
            episodes.append(row)
        new[label+"_trades"]=admitted;new[label+"_episodes"]=episodes
        counts[label]=dict(total=n,**{s:states.count(s) for s in v.STATES})
    tables={"baseline":old,"candidate":new};summaries={}
    for arm,items in tables.items():
        pairs=[]
        for case in items["case_episodes"]:
            controls=[row for row in items["control_episodes"] if row["parent_event_id"]==case["event_id"]]
            values=[row["episode_net_return"] for row in controls]
            cm=sum(values)/3 if len(values)==3 and None not in values else None
            net=case["episode_net_return"]
            pairs.append(dict(event_id=case["event_id"],mother_decision_time=case["mother_decision_time"],fold=case["fold"],
                event_net_return=net,assigned_controls=len(controls),control_mean_return=cm,excess=net-cm if net is not None and cm is not None else None))
        busy={};serial=[]
        for row in sorted(items["case_episodes"],key=lambda r:(r["mother_decision_time"],r["event_id"])):
            time=datetime.fromisoformat(row["mother_decision_time"])
            selected=row["fold"] not in busy or time>=busy[row["fold"]]
            if selected:busy[row["fold"]]=datetime.fromisoformat(row["occupied_until"])
            serial.append(dict(row,portfolio_selected=selected,portfolio_reason="accepted_mother" if selected else "pending_or_position_busy"))
        items["matched"]=pairs;items["single_pending"]=serial
        chosen={r["event_id"] for r in serial if r["portfolio_selected"]}
        means=[r["excess"] for r in pairs if r["excess"] is not None]
        nets=[r["episode_net_return"] for r in items["case_episodes"] if r["episode_net_return"] is not None]
        summaries[arm]=dict(metrics=f.metric(items["case_trades"]),control_metrics=f.metric(items["control_trades"]),
            single_position=f.metric([r for r in items["case_trades"] if r["event_id"] in chosen]),serial_selected_mothers=len(chosen),
            matching=dict(paired_events=len(means),mother_events=len(pairs),coverage=len(means)/len(pairs),assignment_coverage=sum(r["assigned_controls"]==3 for r in pairs)/len(pairs),
                mean_excess_bp=sum(means)/len(means)*1e4 if means else None),net_effect=dict(n=len(nets),mean_bp=sum(nets)/len(nets)*1e4 if nets else None),
            gates=dict(complete_evidence=all(r["observed"] for label in contexts for r in items[label+"_episodes"])))
        if arm=="candidate":summaries[arm]["gate_counts"]=counts
    effects={}
    for name,table,column in (("case_delta","case_episodes","episode_net_return"),("excess_delta","matched","excess"),("serial_delta","single_pending","episode_net_return")):
        rows=[]
        for a,c in zip(tables["baseline"][table],tables["candidate"][table]):
            before,after=a[column],c[column]
            if name=="serial_delta":
                before=before if a["portfolio_selected"] else 0.
                after=after if c["portfolio_selected"] else 0.
            rows.append(dict(event_id=a["event_id"],mother_decision_time=a["mother_decision_time"],before=before,after=after,
                difference=after-before if before is not None and after is not None else None))
        values=[r["difference"] for r in rows if r["difference"] is not None]
        effects[name]=dict(total_pairs=len(rows),n=len(values),unknown_pairs=len(rows)-len(values),mean_bp=sum(values)/len(values)*1e4 if values else None,
            improved=sum(x>1e-12 for x in values),worsened=sum(x< -1e-12 for x in values),unchanged=sum(abs(x)<=1e-12 for x in values))
        tables[name]=rows
    return tables,contexts,gates,summaries,effects


def run(data,full=False):return v.verify_tables(*data,expected_counts=(251,462,154) if full else (3,6,2))


def test_full_original_support_known_zero_and_old_allowed_parity():
    data=fixture(full=True);out=run(data,True)
    assert out["counts"]==dict(cases=251,controls=462,matched=154,unmatched=97)
    assert out["gate_counts"]["case"]==dict(accepted=250,abstain=1,unknown=0)
    assert out["effects"]["excess_delta"]["unknown_pairs"]==97
    assert out["raw_replay"] is out["inferential_p_recomputed"] is False


@pytest.mark.parametrize("states",list(product(v.STATES,repeat=4)))
def test_exhaustive_one_case_and_its_three_controls_admission_states(states):
    #81 state combinations: a group's observed mean needs every control,
    # including known abstention zero. Gates cannot follow their parent case.
    data=fixture(case_states=[states[0],"accepted","accepted"],control_states=list(states[1:])+["accepted"]*3)
    out=run(data)
    expected=1+int("unknown" in states)
    assert out["effects"]["excess_delta"]["unknown_pairs"]==expected
    controls=data[0]["candidate"]["control_episodes"][:3]
    pair=data[0]["candidate"]["matched"][0]
    if "unknown" not in states[1:]:assert pair["control_mean_return"]==sum(r["episode_net_return"] for r in controls)/3


def test_all_abstain_has_zero_opportunity_not_fake_completed_trades():
    data=fixture(case_states=["abstain"]*3,control_states=["abstain"]*6)
    out=run(data)
    assert data[0]["candidate"]["case_trades"]==[]
    assert data[3]["candidate"]["metrics"]["events"]==0
    assert data[3]["candidate"]["net_effect"]==dict(n=3,mean_bp=0.)
    assert out["effects"]["case_delta"]["n"]==3 and out["effects"]["excess_delta"]["n"]==2
    data[3]["candidate"]["metrics"]["events"]=3
    with pytest.raises(v.VerificationError):run(data)


def test_all_unknown_not_a_profitable_abstention():
    data=fixture(case_states=["unknown"]*3,control_states=["unknown"]*6)
    out=run(data)
    assert out["effects"]["case_delta"]["n"]==0 and out["effects"]["excess_delta"]["n"]==0
    assert data[3]["candidate"]["gates"]["complete_evidence"] is False


@pytest.mark.parametrize("field,value",[("prior_colour_side",0),("prior_colour_known",1),("prior_colour_count",39),
    ("prior_colour_count",40.5),("prior_colour_reason","warmup"),("prior_colour_ma",0),("prior_colour_hl2",0),
    ("prior_colour_raw_segment_id",float("inf")),("prior_colour_gate_state","unknown"),
    ("signal_time","2023-01-02T00:00:00Z"),("prior_colour_available_at","2023-01-02T00:00:00Z"),
    ("prior_colour_bar_open","2023-01-01T16:00:00.000000001Z")])
def test_known_gate_causal_support_corruption(field,value):
    context=fixture()[1]["case"][0];row=gate(context,"accepted");row[field]=value
    with pytest.raises(v.VerificationError):v.check_gate(row,context)


@pytest.mark.parametrize("phase",range(4))
@pytest.mark.parametrize("direction",[-1,1])
def test_k1_open_four_hour_phase_and_equality_convention(phase,direction):
    context=fixture()[1]["case"][0]
    signal=datetime(2023,1,2,phase,tzinfo=f.T0.tzinfo)
    context.update(direction=direction,signal_time=signal.isoformat(),decision_time=(signal+timedelta(hours=1)).isoformat())
    row=gate(context,"accepted")
    assert v.check_gate(row,context)=="accepted"
    row["prior_colour_hl2"]=row["prior_colour_ma"]
    row["prior_colour_side"]=1;row["prior_colour_gate_state"]="accepted" if direction==1 else "abstain"
    assert v.check_gate(row,context)==row["prior_colour_gate_state"]


def test_stale_or_absent_unknown_diagnostics_allowed_but_side_not_filled():
    context=fixture()[1]["case"][0];row=gate(context,"unknown")
    for reason in ("stale_context","source_gap"):
        row["prior_colour_reason"]=reason
        row["prior_colour_bar_open"]="2022-12-30T00:00:00Z";row["prior_colour_available_at"]="2022-12-30T04:00:00Z"
        assert v.check_gate(row,context)=="unknown"
    for suffix in ("bar_open","available_at","count","ma","hl2","raw_segment_id"):row["prior_colour_"+suffix]=None
    row["prior_colour_reason"]="no_complete_4h"
    assert v.check_gate(row,context)=="unknown"
    row["prior_colour_side"]=0
    with pytest.raises(v.VerificationError):v.check_gate(row,context)


@pytest.mark.parametrize("field,value",[("policy_fee_fraction",.002),("executed",True),("completed_trade",True),("observed",False),
    ("episode_net_return",None),("entry_time","2023-01-02T00:00:00Z"),("exit_time","2023-01-02T00:05:00Z"),
    ("occupied_until","2023-01-05T00:00:00Z"),("status","expired_no_k2"),("terminal_time","2023-01-02T01:00:00Z")])
def test_known_abstention_zero_fee_no_execution(field,value):
    data=fixture();data[0]["candidate"]["case_episodes"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field,value",[("policy_fee_fraction",0),("episode_net_return",0),("observed",True),
    ("occupied_until","2023-01-02T00:00:00Z"),("status","prior_colour_abstain")])
def test_unknown_not_zero_or_known_no_position(field,value):
    data=fixture(case_states=["unknown","accepted","accepted"]);data[0]["candidate"]["case_episodes"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field,value",[("entry_price",101),("net_return",.2),("initial_stop",80),("ma",99),
    ("hold_minutes",20),("transition_initial_state","opposite"),("exit_time","2023-01-02T05:30:00.000000001Z")])
def test_admission_only_cannot_change_accepted_trade(field,value):
    data=fixture();data[0]["candidate"]["case_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


def test_disallowed_control_not_replaced_by_new_selected_control():
    data=fixture();new=data[0]["candidate"]["control_trades"]
    new[0]=deepcopy(data[0]["baseline"]["control_trades"][0])
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("table",list(v.TABLE_FILES))
def test_full_opportunities_or_accepted_trade_id_missing(table):
    data=fixture();data[0]["candidate"][table].pop()
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("table",["case_delta","excess_delta","serial_delta"])
def test_policy_effect_denominator_no_survivor_only_mean(table):
    data=fixture();data[0][table].pop()
    with pytest.raises(v.VerificationError):run(data)


def test_control_zero_kept_in_three_mean_not_two():
    data=fixture();pair=data[0]["candidate"]["matched"][0]
    pair["control_mean_return"]*=1.5
    with pytest.raises(v.VerificationError):run(data)


def test_no_alignment_does_not_borrow_parent_case_gate():
    data=fixture(case_states=["abstain","accepted","accepted"],control_states=["accepted"]*6)
    out=run(data)
    assert out["gate_counts"]["control"]["accepted"]==6
    data[0]["candidate"]["matched"][0]["control_mean_return"]=0
    with pytest.raises(v.VerificationError):run(data)


def test_unknown_occupancy_changes_serial_without_filling_selected_unknown():
    data=fixture(case_states=["unknown","accepted","accepted"])
    out=run(data)
    assert [r["portfolio_selected"] for r in data[0]["candidate"]["single_pending"]]==[True,False,False]
    assert out["effects"]["serial_delta"]["unknown_pairs"]==1
    assert data[0]["serial_delta"][1]["after"]==0
    data[0]["serial_delta"][0].update(after=0,difference=-.001)
    with pytest.raises(v.VerificationError):run(data)


def test_gate_rows_cannot_drop_unknown_or_duplicate_ids():
    for duplicate in (False,True):
        data=fixture();data[2].append(deepcopy(data[2][0])) if duplicate else data[2].pop()
        with pytest.raises(v.VerificationError):run(data)


def mechanism(data,label):
    old,new=(data[0][arm][label+"_episodes"] for arm in v.ARMS)
    rows=[];grouped=defaultdict(list)
    for before,after in zip(old,new):
        a,c=before["episode_net_return"],after["episode_net_return"]
        delta=c-a if c is not None and a is not None else None
        state=after["prior_colour_gate_state"]
        row=dict(after,baseline_net_return=a,difference=delta,avoided_net_loser=state=="abstain" and a<0,missed_net_winner=state=="abstain" and a>0)
        rows.append(row);grouped[state].append(row)
    groups=[]
    for state,part in grouped.items():
        known=[r for r in part if r["difference"] is not None]
        avg=lambda col:sum(r[col] for r in known)/len(known)*1e4 if known else None
        groups.append(dict(gate_state=state,n=len(part),known_pairs=len(known),old_mean_net_bp=avg("baseline_net_return"),
            new_mean_net_bp=avg("episode_net_return"),mean_delta_bp=avg("difference"),sum_delta_event_bp=sum(r["difference"] for r in known)*1e4 if known else None,
            avoided_net_losers=sum(r["avoided_net_loser"] for r in part),missed_net_winners=sum(r["missed_net_winner"] for r in part)))
    info=dict(total=len(rows),**{state:len(grouped.get(state,[])) for state in v.STATES},known_pairs=sum(r["difference"] is not None for r in rows),
        avoided_net_losers=sum(r["avoided_net_loser"] for r in rows),missed_net_winners=sum(r["missed_net_winner"] for r in rows),
        avoided_loss_total_bp=-sum(r["baseline_net_return"] for r in rows if r["avoided_net_loser"])*1e4,
        missed_winner_total_bp=sum(r["baseline_net_return"] for r in rows if r["missed_net_winner"])*1e4,groups=deepcopy(groups))
    return rows,groups,info


@pytest.mark.parametrize("label",["case","control"])
def test_mechanics_preserves_all_states_and_retrospective_labels(label):
    data=fixture(case_states=["abstain","unknown","accepted"],control_states=["abstain","unknown"]+["accepted"]*4)
    rows,groups,summary=mechanism(data,label)
    assert v.verify_mechanics(data[0],label,rows,groups,summary)["unknown"]==1
    rows[0]["avoided_net_loser"]=not rows[0]["avoided_net_loser"]
    with pytest.raises(v.VerificationError):v.verify_mechanics(data[0],label,rows,groups,summary)


def test_missing_results_and_failure_reject_without_writes(tmp_path):
    with pytest.raises(v.VerificationError):v.verify(tmp_path)
    results=tmp_path/v.EXPERIMENT_PATH/"results";results.mkdir(parents=True)
    marker=results/"failure.json";marker.write_text('{"status":"failed"}')
    old=marker.read_bytes()
    with pytest.raises(v.VerificationError,match="Failed attempt"):v.verify(tmp_path)
    assert marker.read_bytes()==old


def test_no_yoyo_or_external_library_import():
    import ast
    tree=ast.parse((ROOT/"scripts/verify_hourly_impulse_prior_colour_v13.py").read_text())
    names=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]+[a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
    assert not any(name and name.split(".")[0] in {"yoyo","numpy","pandas","scipy"} for name in names)


def monthly(data):
    rows=[]
    for arm in v.ARMS:
        groups=defaultdict(list)
        for row in data[0][arm]["case_episodes"]:groups[(row["fold"],row["mother_decision_time"][:7])].append(row)
        for (fold,month),part in groups.items():
            values=[r["episode_net_return"] for r in part if r["observed"]]
            rows.append(dict(arm=arm,fold=fold,month=month,n=len(part),known=len(values),executed=sum(r["executed"] for r in part),
                mean_net_bp=sum(values)/len(values)*1e4 if values else None))
    return rows


def disk_fixture(tmp_path,monkeypatch,*,all_abstain=False):
    """All713 rows are generated, only temporary files are read by verify()."""
    data=fixture(full=True,case_states=["abstain"]*251 if all_abstain else None,control_states=["abstain"]*462 if all_abstain else None)
    tables,contexts,gates,arms,effects=data
    results=tmp_path/v.EXPERIMENT_PATH/"results";parent=tmp_path/v.PARENT_PATH;mother=tmp_path/v.MOTHER_PATH
    def write_table(path,rows,columns=None):
        path.parent.mkdir(parents=True,exist_ok=True);opener=gzip.open if path.suffix==".gz" else open
        with opener(path,"wt",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=columns or list(rows[0]));writer.writeheader();writer.writerows(rows)
    #All V5 receipt fixtures are generated from the same artificial baseline.
    for label in contexts:
        f.write_csv(parent/("direct_k1_stop_"+label+"_context.csv.gz"),contexts[label])
        f.write_csv(mother/("original_mothers.csv.gz" if label=="case" else "control_mothers.csv.gz"),contexts[label])
        f.write_csv(results/(label+"_context.csv.gz"),contexts[label])
    parents={r["parent_event_id"] for r in contexts["control"]}
    assignments=[dict(event_id=r["event_id"],match_status="matched" if r["event_id"] in parents else "unmatchable") for r in contexts["case"]]
    f.write_csv(mother/"assignments.csv",assignments);f.write_csv(results/"assignments.csv",assignments)
    f.write_json(mother/"assignment_receipt.json",dict(matched=154));f.write_json(parent/"summary.json",dict(old="synthetic"))
    for name,file in v.TABLE_FILES.items():
        f.write_csv(parent/("direct_k1_stop__transition_colour_"+file),tables["baseline"][name])
        for arm in v.ARMS:write_table(results/arm/file,tables[arm][name],list(tables["baseline"][name][0]) if name.endswith("trades") else None)
    for name in ("case_delta","excess_delta","serial_delta"):f.write_csv(results/(name+".csv"),tables[name])
    f.write_json(results/"anchor_parity.json",{name:dict(rows=len(tables["baseline"][name]),columns=len(tables["baseline"][name][0])) for name in v.TABLE_FILES})
    #Concat preserves union columns, including control-only parent linkage.
    columns=list(dict.fromkeys(k for row in gates for k in row))
    write_table(results/"context_gates.csv",gates,columns)
    receipt=dict(at="2026-09-06T01:00:01Z",sha256=v.sha(results/"context_gates.csv"),before_any_arm_outcomes=True,
        populations=arms["candidate"]["gate_counts"])
    f.write_json(results/"context_gates_frozen.json",receipt)
    arms["candidate"]["parity"]={"accepted_trade_fields_unchanged":{label:dict(rows=len(tables["candidate"][label+"_trades"]),
        columns=len(tables["baseline"][label+"_trades"][0])) for label in contexts}}
    root_mechanics={}
    for label in contexts:
        rows,groups,summary=mechanism(data,label)
        f.write_csv(results/("paired_"+label+"_mechanics.csv.gz"),rows);f.write_csv(results/(label+"_mechanism_groups.csv"),groups)
        root_mechanics["mechanics" if label=="case" else "control_mechanics"]=summary
    f.write_csv(results/"monthly_case_net.csv",monthly(data))
    for arm in v.ARMS:f.write_json(results/arm/"summary.json",arms[arm])
    base=dict(execution=dict(cost_fraction=.002,max_hours=72,stop_first=True),development_folds=[[fold,a,c] for fold,(a,c) in v.FOLDS.items()],
        source=dict(sha256="synthetic-raw-not-opened"))
    f.write_json(tmp_path/v.BASE_PATH,base)
    policy=dict(id="5m_native40",management_minutes=5,ma_kind="SMA",ma_length=40,exit_mode="transition_colour",confirmations=1)
    config=dict(experiment_id=v.EXPERIMENT_ID,base_config=v.BASE_PATH,base_config_sha256=v.sha(tmp_path/v.BASE_PATH),
        parent_results=v.PARENT_PATH,mother_results=v.MOTHER_PATH,
        inputs={str(path.relative_to(parent)):v.sha(path) for path in parent.iterdir()},mother_inputs={str(path.relative_to(mother)):v.sha(path) for path in mother.iterdir()},
        policies=[policy,dict(policy,id="5m_native40_prior4h_colour",entry_gate="prior4h_colour_at_k1_open")],gate_contract=v.GATE_CONTRACT,
        known_support=dict(cases=251,controls=462,matched=154,coverage_gate_unattainable=True),
        selection=dict(minimum_events=80,minimum_per_fold=12,positive_folds=4,minimum_profit_factor=1.1,minimum_active_months=12,minimum_months_per_fold=3,matched_coverage=.9),
        inference=dict(draws=9999,seed=20260906,p_limit=.01,joint_required=["case_delta","excess_delta"],method="month_cluster"),
        no_audit_entry_point=True,holdout_consumed=False,production_eligible=False,training_eligible=False)
    f.write_json(tmp_path/v.EXPERIMENT_PATH/"config.json",config)
    paths=v.REQUIRED_CODE_SOURCES | {v.EXPERIMENT_PATH+"/config.json",v.EXPERIMENT_PATH+"/PROJECT_PLAN.md",v.BASE_PATH}
    committed={path:(tmp_path/path).read_bytes() if (tmp_path/path).exists() else ("synthetic committed source:"+path).encode() for path in paths}
    sources=[dict(path=path,sha256=hashlib.sha256(content).hexdigest()) for path,content in sorted(committed.items())]
    started=dict(at="2026-09-06T01:00:00Z",sources=sources,inputs=config["inputs"],mother_inputs=config["mother_inputs"],builder_commit="b"*40)
    f.write_json(results/"started.json",started)
    summary=dict(experiment_id=v.EXPERIMENT_ID,status="diagnostic_only_no_candidate_acceptance",arms=arms,effects=effects,
        gate_contract=v.GATE_CONTRACT,context_receipt=receipt,gates=dict(matched_coverage=False),all_financial_gates_pass=False,
        known_coverage_ceiling=154/251,coverage_required=.9,source=dict(sha256=base["source"]["sha256"],holdout_price_rows=0,phase_price_last_open="2024-12-31T23:55:00Z"),
        sources=sources,config_sha256=v.sha(tmp_path/v.EXPERIMENT_PATH/"config.json"),audit_prices_loaded=False,holdout_consumed=False,
        production_eligible=False,training_eligible=False,inputs=config["inputs"],mother_inputs=config["mother_inputs"],**root_mechanics)
    def refresh():
        summary["output_hashes"]={str(path.relative_to(results)):v.sha(path) for path in results.rglob("*") if path.is_file() and path!=results/"summary.json"}
        f.write_json(results/"summary.json",summary)
    refresh()
    def fake_git(command,**kwargs):
        assert command[:2]==["git","show"]
        if "--format=%ct" in command:return SimpleNamespace(stdout="1788652800")
        commit,path=command[2].split(":",1)
        assert commit=="b"*40 and not path.startswith("data/")
        return SimpleNamespace(stdout=committed[path])
    monkeypatch.setattr(v.b.subprocess,"run",fake_git)
    return results,summary,config,committed,refresh


@pytest.mark.parametrize("all_abstain",[False,True])
def test_complete_saved_only_directory_and_empty_accepted_schema(tmp_path,monkeypatch,all_abstain):
    results,summary,config,committed,refresh=disk_fixture(tmp_path,monkeypatch,all_abstain=all_abstain)
    before={str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result=v.verify(tmp_path)
    assert result["counts"]["unmatched"]==97 and result["committed_sources_verified"]==19
    assert result["status"]=="passed" and result["gate_counts"]["case"]["accepted"]==(0 if all_abstain else 250)
    assert {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}==before


@pytest.mark.parametrize("mutation",["gate_hash","checkpoint_late_false","checkpoint_counts","input_hash","source_bytes","omitted_source",
    "config","summary_count","known97","output_hash","extra_file","failure","empty_schema","committed_config"])
def test_complete_directory_corruptions(tmp_path,monkeypatch,mutation):
    results,summary,config,committed,refresh=disk_fixture(tmp_path,monkeypatch,all_abstain=mutation=="empty_schema")
    if mutation=="gate_hash":
        receipt=v.read_json(results/"context_gates_frozen.json");receipt["sha256"]="wrong";summary["context_receipt"]=receipt
        f.write_json(results/"context_gates_frozen.json",receipt);refresh()
    elif mutation=="checkpoint_late_false":
        receipt=v.read_json(results/"context_gates_frozen.json");receipt["before_any_arm_outcomes"]=False;summary["context_receipt"]=receipt
        f.write_json(results/"context_gates_frozen.json",receipt);refresh()
    elif mutation=="checkpoint_counts":
        receipt=v.read_json(results/"context_gates_frozen.json");receipt["populations"]["case"]["accepted"]+=1;summary["context_receipt"]=receipt
        f.write_json(results/"context_gates_frozen.json",receipt);refresh()
    elif mutation=="input_hash":(tmp_path/v.PARENT_PATH/"summary.json").write_text("changed")
    elif mutation=="source_bytes":committed["yoyo/data/hourly_impulse_prior_colour.py"]+=b"changed"
    elif mutation=="omitted_source":
        summary["sources"]=summary["sources"][1:];start=v.read_json(results/"started.json");start["sources"]=summary["sources"]
        f.write_json(results/"started.json",start);refresh()
    elif mutation=="config":
        config["gate_contract"]["require_slope"]=True;f.write_json(tmp_path/v.EXPERIMENT_PATH/"config.json",config)
    elif mutation=="summary_count":summary["arms"]["candidate"]["metrics"]["events"]+=1;refresh()
    elif mutation=="known97":
        rows=v.read_csv(results/"excess_delta.csv");rows[-1].update(before="0",after="0",difference="0")
        f.write_csv(results/"excess_delta.csv",rows);refresh()
    elif mutation=="output_hash":(results/"case_delta.csv").write_text("bad")
    elif mutation=="extra_file":(results/"extra.csv").write_text("extra")
    elif mutation=="failure":f.write_json(results/"failure.json",dict(status="failed"));refresh()
    elif mutation=="empty_schema":
        with gzip.open(results/"candidate"/"case_trades.csv.gz","wt") as handle:handle.write("event_id\n")
        refresh()
    else:
        path=v.EXPERIMENT_PATH+"/config.json";committed[path]+=b"\n";digest=hashlib.sha256(committed[path]).hexdigest()
        for row in summary["sources"]:
            if row["path"]==path:row["sha256"]=digest
        start=v.read_json(results/"started.json");start["sources"]=summary["sources"];f.write_json(results/"started.json",start);refresh()
    with pytest.raises(v.VerificationError):v.verify(tmp_path)

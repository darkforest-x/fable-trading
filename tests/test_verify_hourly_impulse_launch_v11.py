"""Synthetic saved-ledger corruption tests; no archive/research imports."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import hashlib
from pathlib import Path
import statistics
import subprocess
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("verify_launch", Path(__file__).resolve().parents[1]/"scripts/verify_hourly_impulse_launch_v11.py")
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)


def iso(value):
    return value.isoformat()


def trade(key, day, *, direction=1, parent=None, hold=90, change=.003):
    start = datetime(2023, 1, day, tzinfo=timezone.utc)
    entry = 100. if direction == 1 else 200.
    risk = 10. if direction == 1 else 7.
    stop, atr = entry-direction*risk, 4.
    row = dict(event_id=key, direction=direction, entry_price=entry, initial_stop=stop,
               signal_atr=atr, risk_pct=risk/entry, risk_atr=risk/atr,
               decision_time=iso(start), mother_decision_time=iso(start), entry_time=iso(start),
               mother_signal_time=iso(start-timedelta(hours=1)), mother_deadline=iso(start+timedelta(hours=72)),
               fold="2023H1", wait_hours=0, closed=True, outcome="transition_colour_exit",
               exit_time=iso(start+timedelta(minutes=hold)), exit_price=entry*(1+direction*change),
               gross_return=change, net_return=change-.002, net_r=(change-.002)/(risk/entry),
               hold_minutes=hold, partial_fraction=0., exit_remaining_fraction=1.,
               realised_partial_gross_return=0., funding_modelled=False,
               transition_initial_state="aligned", transition_initial_open_time=iso(start-timedelta(minutes=5)))
    if parent:
        row.update(parent_event_id=parent, matched_event_id=parent)
    return row


def launch(old, *, timeout=False, progress=False):
    row = deepcopy(old)
    start = datetime.fromisoformat(row["entry_time"])
    row.update(launch_enabled=True, launch_deadline_minutes=60, launch_progress_r=.5,
               launch_deadline_at=iso(start+timedelta(hours=1)), launch_progress_reached=progress,
               launch_progress_first_at=iso(start+timedelta(minutes=10)) if progress else "",
               launch_completed_close_count=min(old["hold_minutes"]//5,12),
               launch_max_completed_close_r=.8 if progress else .2,
               launch_deadline_checked_at=iso(start+timedelta(hours=1)) if timeout or old["hold_minutes"]>60 else "",
               launch_status="progress_confirmed" if progress else "timeout_exit" if timeout else "prior_exit")
    if timeout:
        gross = -.001
        row.update(outcome="launch_timeout_exit", exit_time=iso(start+timedelta(hours=1)),
                   exit_price=row["entry_price"]*(1+row["direction"]*gross), hold_minutes=60,
                   gross_return=gross, net_return=gross-.002, net_r=(gross-.002)/row["risk_pct"])
    return row


def episode(t):
    row = {k:t[k] for k in ("event_id","mother_decision_time","mother_signal_time","mother_deadline","fold")}
    if "parent_event_id" in t:
        row["parent_event_id"] = t["parent_event_id"]
    row.update(status="request_emitted",terminal_time=t["mother_decision_time"],
               episode_status=t["outcome"],episode_net_return=t["net_return"],executed=True,
               completed_trade=t["closed"],observed=t["closed"],entry_time=t["entry_time"],
               exit_time=t["exit_time"],occupied_until=t["exit_time"] if t["closed"] else t["mother_deadline"])
    return row


def metric(rows):
    x = [r["net_return"] for r in rows if r["closed"]]
    mu = sum(x)/len(x)
    loss = -sum(t for t in x if t<0)
    return dict(events=len(x),mean_net_bp=mu*1e4,win_rate=sum(t>0 for t in x)/len(x),
                profit_factor=sum(t for t in x if t>0)/loss if loss else None,
                extra_10bp_mean_net_bp=mu*1e4-10)


def fixture():
    old_cases = [trade("a",2),trade("b",4,direction=-1,hold=30,change=-.004),trade("c",6,hold=120)]
    old_controls = [trade(f"{parent}_{i}",8+3*j+i,parent=parent,direction=1 if parent=="a" else -1,change=-.002 if i==0 else .003)
                    for j,parent in enumerate(("a","b")) for i in range(3)]
    new_cases = [launch(old_cases[0],timeout=True),launch(old_cases[1]),launch(old_cases[2],progress=True)]
    new_controls = [launch(t,timeout=i==0,progress=i!=0) for i,t in enumerate(old_controls)]
    tables, summaries = {}, {}
    for arm,cases,controls in (("baseline",old_cases,old_controls),("candidate",new_cases,new_controls)):
        ce, re = list(map(episode,cases)),list(map(episode,controls))
        pairs=[]
        for c in ce:
            cv=[x["episode_net_return"] for x in re if x["parent_event_id"]==c["event_id"]]
            cm=sum(cv)/3 if cv else None
            pairs.append(dict(event_id=c["event_id"],mother_decision_time=c["mother_decision_time"],fold=c["fold"],
                              event_net_return=c["episode_net_return"],assigned_controls=len(cv),control_mean_return=cm,
                              excess=c["episode_net_return"]-cm if cm is not None else None))
        serial=[dict(x,portfolio_selected=True,portfolio_reason="accepted_mother") for x in ce]
        tables[arm]=dict(case_trades=cases,control_trades=controls,case_episodes=ce,control_episodes=re,matched=pairs,single_pending=serial)
        excess=[r["excess"] for r in pairs if r["excess"] is not None]
        summaries[arm]=dict(metrics=metric(cases),control_metrics=metric(controls),single_position=metric(cases),
                            serial_selected_mothers=3,matching=dict(paired_events=2,mother_events=3,coverage=2/3,
                                                                   mean_excess_bp=sum(excess)/2*1e4))
    effects={}
    for name,table,col in (("case_delta","case_episodes","episode_net_return"),("excess_delta","matched","excess"),("serial_delta","single_pending","episode_net_return")):
        rows=[]
        for old,new in zip(tables["baseline"][table],tables["candidate"][table]):
            a,b=old[col],new[col]
            rows.append(dict(event_id=old["event_id"],mother_decision_time=old["mother_decision_time"],before=a,after=b,
                             difference=b-a if a is not None and b is not None else None))
        x=[r["difference"] for r in rows if r["difference"] is not None]
        effects[name]=dict(total_pairs=3,n=len(x),unknown_pairs=3-len(x),improved=sum(t>1e-12 for t in x),
                           worsened=sum(t< -1e-12 for t in x),unchanged=sum(abs(t)<=1e-12 for t in x),mean_bp=sum(x)/len(x)*1e4)
        tables[name]=rows
    return tables,summaries,effects


def run(data):
    return v.verify_tables(*data,expected_counts=(3,6,2))


def test_full_synthetic_baseline_candidate_and_fixed_unknown():
    result=run(fixture())
    assert result["timeout_exits"]=={"case":1,"control":1}
    assert result["effects"]["case_delta"]["total_pairs"]==3
    assert result["effects"]["excess_delta"]["unknown_pairs"]==1
    assert result["raw_replay"] is False and result["inferential_p_recomputed"] is False


@pytest.mark.parametrize("field,value",[("entry_price",101),("initial_stop",89),("signal_atr",5),("risk_pct",.2),
                                      ("risk_atr",3),("direction",-1),("net_return",.1),("gross_return",.1),
                                      ("net_r",4),("hold_minutes",61),("partial_fraction",.5),
                                      ("funding_modelled",True),("closed",False)])
def test_trade_arithmetic_mutations_rejected(field,value):
    data=fixture();data[0]["candidate"]["case_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field,value",[("launch_deadline_minutes",65),("launch_progress_r",.6),
                                      ("launch_completed_close_count",11),("launch_completed_close_count",12.5),
                                      ("launch_progress_reached",True),("launch_max_completed_close_r",.6),
                                      ("launch_max_completed_close_r",None),("launch_status","pending"),
                                      ("launch_deadline_checked_at",""),("launch_enabled",False)])
def test_timeout_diagnostics_mutations_rejected(field,value):
    data=fixture();data[0]["candidate"]["case_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("minutes",[55,65,90])
def test_timeout_at_wrong_clock_rejected(minutes):
    data=fixture();r=data[0]["candidate"]["case_trades"][0]
    r["exit_time"]=iso(datetime.fromisoformat(r["entry_time"])+timedelta(minutes=minutes));r["hold_minutes"]=minutes
    with pytest.raises(v.VerificationError):run(data)


def test_existing_exit_at_exact_deadline_has_priority():
    data=fixture();old=data[0]["baseline"]["case_trades"][0]
    old["exit_time"]=data[0]["candidate"]["case_trades"][0]["exit_time"];old["hold_minutes"]=60
    with pytest.raises(v.VerificationError):v.check_launch(old,data[0]["candidate"]["case_trades"][0])


@pytest.mark.parametrize("field",["outcome","transition_initial_state","transition_initial_open_time"])
def test_retained_path_all_old_fields_must_survive(field):
    data=fixture();data[0]["candidate"]["case_trades"][1][field]="corrupt"
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("table",list(v.TABLE_FILES))
def test_dropped_or_duplicated_original_rows_rejected(table):
    for duplicate in (False,True):
        data=fixture();rows=data[0]["candidate"][table]
        rows.append(deepcopy(rows[0])) if duplicate else rows.pop()
        with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field,value",[("parent_event_id","c"),("decision_time","2023-01-09T00:00:00+00:00")])
def test_control_mapping_or_time_reuse_rejected(field,value):
    data=fixture();data[0]["candidate"]["control_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field,value",[("observed",False),("executed",False),("completed_trade",False),
                                      ("episode_net_return",None),("status","expired_no_k2"),
                                      ("episode_status","time_exit"),("occupied_until","2023-01-02T01:00:00Z")])
def test_episode_status_and_occupancy_rejected(field,value):
    data=fixture();data[0]["baseline"]["case_episodes"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("table",["case_delta","excess_delta","serial_delta"])
@pytest.mark.parametrize("field",["before","after","difference"])
def test_paired_values_cannot_change(table,field):
    data=fixture();data[0][table][0][field]=.123
    with pytest.raises(v.VerificationError):run(data)


def test_unmatched_excess_is_unknown_not_zero():
    data=fixture();data[0]["candidate"]["matched"][2].update(control_mean_return=0,excess=.001)
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field",["total_pairs","n","unknown_pairs","improved","worsened","unchanged","mean_bp"])
def test_effect_summary_reconciles_whole_population(field):
    data=fixture();data[2]["case_delta"][field]+=1
    with pytest.raises(v.VerificationError):run(data)


def test_serial_selection_recomputed_not_taken_on_trust():
    data=fixture();data[0]["candidate"]["single_pending"][0]["portfolio_selected"]=False
    with pytest.raises(v.VerificationError):run(data)


def test_serial_unknown_selected_reserves_whole_horizon():
    start=datetime(2023,1,2,tzinfo=timezone.utc)
    a=dict(event_id="a",mother_decision_time=iso(start),fold="2023H1",episode_net_return=None,
           occupied_until=iso(start+timedelta(hours=72)))
    b=dict(event_id="b",mother_decision_time=iso(start+timedelta(hours=1)),fold="2023H1",episode_net_return=.1,
           occupied_until=iso(start+timedelta(hours=2)))
    serial=[dict(a,portfolio_selected=True,portfolio_reason="accepted_mother"),
            dict(b,portfolio_selected=False,portfolio_reason="pending_or_position_busy")]
    assert v.check_serial([a,b],serial)=={"a":None,"b":0.}


def test_nanosecond_and_timezone_equality_are_exact():
    a="2023-01-02T00:00:00.000000001Z"
    assert v.stamp(a)-v.stamp("2023-01-02T00:00:00Z")==1
    assert v.stamp(a)==v.stamp("2023-01-02T08:00:00.000000001+08:00")
    with pytest.raises(v.VerificationError):v.parity([dict(event_id="a",entry_time=a)],[dict(event_id="a",entry_time="2023-01-02T00:00:00Z")])


@pytest.mark.parametrize("value",[123,"2023-01-02 00:00:00","2023-01-02",""])
def test_invalid_or_unzoned_timestamp_rejected(value):
    with pytest.raises(v.VerificationError):v.stamp(value)


@pytest.mark.parametrize("value",[float("nan"),float("inf"),"nan","inf",True])
def test_nonfinite_not_silently_zero(value):
    with pytest.raises(v.VerificationError):v.number(value)


@pytest.mark.parametrize("identity",["../raw.csv","/tmp/raw.csv","data/../raw.csv","x//y"])
def test_unsafe_evidence_identity_rejected(tmp_path,identity):
    with pytest.raises(v.VerificationError):v.safe_path(tmp_path,identity)


def test_csv_duplicate_header_and_ragged_rejected(tmp_path):
    path=tmp_path/"x.csv"
    for content in ("event_id,event_id\na,a\n","event_id,value\na\n","event_id,value\na,1,2\n"):
        path.write_text(content)
        with pytest.raises(v.VerificationError):v.read_csv(path)


def test_json_duplicate_and_nonfinite_rejected(tmp_path):
    path=tmp_path/"x.json"
    for content in ('{"x":1,"x":2}','{"x":NaN}'):
        path.write_text(content)
        with pytest.raises(v.VerificationError):v.read_json(path)


def test_no_strategy_or_external_package_imports():
    import ast
    tree=ast.parse(Path(v.__file__).read_text())
    modules=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    modules += [a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
    assert all(not m.startswith(("yoyo","pandas","numpy","scipy")) for m in modules)


def diagnostics(tables):
    rows=[]
    for old,new in zip(tables["baseline"]["case_trades"],tables["candidate"]["case_trades"]):
        row={"event_id":old["event_id"]}
        row.update({k+"_before":value for k,value in old.items() if k!="event_id"})
        row.update({k+"_after":value for k,value in new.items() if k!="event_id"})
        a,b=old["net_return"],new["net_return"]
        timeout=new["outcome"]=="launch_timeout_exit"
        row.update(difference=b-a,timeout_exit=timeout,
                   win_loss_transition=("win" if a>0 else "loss")+"_to_"+("win" if b>0 else "loss"),
                   mechanism_group="launch_timeout" if timeout else "original_exit_retained")
        rows.append(row)
    groups=[]
    for name in sorted({r["mechanism_group"] for r in rows}):
        part=[r for r in rows if r["mechanism_group"]==name]
        a=[r["net_return_before"] for r in part];b=[r["net_return_after"] for r in part];d=[r["difference"] for r in part]
        groups.append(dict(group=name,n=len(part),known=len(part),old_mean_net_bp=sum(a)/len(a)*1e4,
                           new_mean_net_bp=sum(b)/len(b)*1e4,mean_delta_bp=sum(d)/len(d)*1e4,
                           sum_delta_event_bp=sum(d)*1e4,wins_before=sum(x>0 for x in a),wins_after=sum(x>0 for x in b)))
    ds={}
    for col in ("net_return_before","net_return_after","difference"):
        x=sorted(r[col]*1e4 for r in rows)
        # Three-point linear quantiles, independent from the verifier helper.
        qs={str(p):x[0]+(x[1]-x[0])*(2*p) if p<=.5 else x[1]+(x[2]-x[1])*(2*p-1) for p in (0.,.05,.25,.5,.75,.95,1.)}
        ds[col]=dict(n=3,unknown=0,outliers_removed=0,quantiles_bp=qs,sd_bp=statistics.stdev(x))
    counts={name:sum(r["win_loss_transition"]==name for r in rows) for name in {r["win_loss_transition"] for r in rows}}
    summary=dict(total=3,known=3,timeout_exits=1,transitions=counts,groups=deepcopy(groups),distributions=ds)
    monthly=[dict(arm=arm,fold="2023H1",month="2023-01",n=3,known=3,
                  mean_net_bp=sum(r["episode_net_return"] for r in tables[arm]["case_episodes"])/3*1e4) for arm in v.ARMS]
    return rows,groups,monthly,summary


def test_all_mechanics_months_and_distributions_reconciled():
    tables,_,_=fixture()
    assert v.verify_diagnostics(tables,*diagnostics(tables))==dict(paired_rows=3,monthly_rows=2,untrimmed_distributions=3)


@pytest.mark.parametrize("target",["paired_time","paired_net","paired_difference","paired_group","paired_timeout",
                                  "group_count","group_mean","month_n","month_known","month_mean",
                                  "month_drop","month_duplicate","quantile","sd","trim","transition"])
def test_saved_diagnostics_mutations_rejected(target):
    tables,_,_=fixture();rows,groups,monthly,summary=diagnostics(tables)
    if target=="paired_time":rows[0]["entry_time_after"]="2023-01-02T00:00:00.000000001Z"
    elif target=="paired_net":rows[0]["net_return_before"]+=.1
    elif target=="paired_difference":rows[0]["difference"]+=.1
    elif target=="paired_group":rows[0]["mechanism_group"]="original_exit_retained"
    elif target=="paired_timeout":rows[0]["timeout_exit"]=False
    elif target=="group_count":groups[0]["n"]+=1
    elif target=="group_mean":groups[0]["mean_delta_bp"]+=1
    elif target=="month_n":monthly[0]["n"]+=1
    elif target=="month_known":monthly[0]["known"]-=1
    elif target=="month_mean":monthly[0]["mean_net_bp"]+=1
    elif target=="month_drop":monthly.pop()
    elif target=="month_duplicate":monthly.append(deepcopy(monthly[0]))
    elif target=="quantile":summary["distributions"]["difference"]["quantiles_bp"]["0.5"]+=1
    elif target=="sd":summary["distributions"]["difference"]["sd_bp"]+=1
    elif target=="trim":summary["distributions"]["difference"]["outliers_removed"]=1
    else:summary["transitions"]["win_to_loss"]+=1
    with pytest.raises(v.VerificationError):v.verify_diagnostics(tables,rows,groups,monthly,summary)


def test_exact_half_r_at_deadline_cannot_be_timeout():
    data=fixture();row=data[0]["candidate"]["case_trades"][0]
    row["launch_max_completed_close_r"]=.5
    with pytest.raises(v.VerificationError):run(data)


def test_without_progress_cannot_retain_late_original_exit():
    data=fixture();row=data[0]["candidate"]["case_trades"][2]
    row.update(launch_progress_reached=False,launch_progress_first_at="",launch_max_completed_close_r=.2,launch_status="prior_exit")
    with pytest.raises(v.VerificationError):run(data)


def test_committed_source_not_working_tree(monkeypatch,tmp_path):
    code=b"original committed code\n";path=tmp_path/"builder.py";path.write_text("later changes")
    sources=[dict(path="builder.py",sha256=hashlib.sha256(code).hexdigest())]
    started=dict(builder_commit="a"*40,sources=sources);summary=dict(sources=deepcopy(sources))
    calls=[]
    def fake(args,**kwargs):
        calls.append(args);return SimpleNamespace(stdout=code)
    monkeypatch.setattr(v.subprocess,"run",fake)
    assert v.verify_committed_sources(tmp_path,started,summary,{"builder.py"})==1
    assert calls==[["git","show","a"*40+":builder.py"]]


@pytest.mark.parametrize("failure",["unavailable","hash","missing","duplicate","raw"])
def test_committed_source_cannot_be_silently_skipped(monkeypatch,tmp_path,failure):
    code=b"code";sources=[dict(path="builder.py",sha256=hashlib.sha256(code).hexdigest())]
    if failure=="missing":sources=[]
    if failure=="duplicate":sources*=2
    if failure=="raw":sources[0]["path"]="data/raw.csv"
    started=dict(builder_commit="b"*40,sources=sources);summary=dict(sources=deepcopy(sources))
    def fake(args,**kwargs):
        if failure=="unavailable":raise subprocess.CalledProcessError(128,args)
        return SimpleNamespace(stdout=b"wrong" if failure=="hash" else code)
    monkeypatch.setattr(v.subprocess,"run",fake)
    with pytest.raises(v.VerificationError):v.verify_committed_sources(tmp_path,started,summary,{r["path"] for r in sources} or {"builder.py"})


@pytest.mark.parametrize("failure",["changed","omitted","extra","missing"])
def test_all_output_hashes_required(tmp_path,failure):
    path=tmp_path/"case.csv";path.write_text("a,b\n1,2\n");(tmp_path/"summary.json").write_text("{}")
    hashes={"case.csv":hashlib.sha256(path.read_bytes()).hexdigest()}
    if failure=="changed":path.write_text("a,b\n3,4\n")
    elif failure=="omitted":hashes={}
    elif failure=="extra":(tmp_path/"other.csv").write_text("x\n")
    else:path.unlink()
    with pytest.raises(v.VerificationError):v.verify_output_hashes(tmp_path,hashes)


def test_valid_output_hash_coverage(tmp_path):
    path=tmp_path/"case.csv";path.write_text("a\n1\n");(tmp_path/"summary.json").write_text("{}")
    assert v.verify_output_hashes(tmp_path,{"case.csv":hashlib.sha256(path.read_bytes()).hexdigest()})==1

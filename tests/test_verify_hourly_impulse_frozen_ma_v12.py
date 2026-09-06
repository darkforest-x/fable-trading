"""Synthetic CSV-only V12 invariants; no strategy, outcome archive or raw reads."""
from collections import Counter, defaultdict
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("verify_frozen_v12", Path(__file__).resolve().parents[1]/"scripts/verify_hourly_impulse_frozen_ma_v12.py")
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)
T0 = datetime(2023,1,2,tzinfo=timezone.utc)


def trade(key, offset, *, direction=1, parent=None, hold=90, gross=.003, g=.5):
    start = T0+timedelta(hours=offset)
    entry,risk,atr = 100.,10.,4.
    end = start+timedelta(minutes=hold)
    ma = entry-direction*risk*g
    row = dict(event_id=key,direction=direction,entry_price=entry,initial_stop=entry-direction*risk,
        signal_atr=atr,risk_pct=risk/entry,risk_atr=risk/atr,ma=ma,signal_close=ma+direction,
        signal_time=(start-timedelta(hours=1)).isoformat(),decision_time=start.isoformat(),
        mother_decision_time=start.isoformat(),entry_time=start.isoformat(),
        mother_signal_time=(start-timedelta(hours=1)).isoformat(),mother_deadline=(start+timedelta(hours=72)).isoformat(),
        fold="2023H1",wait_hours=0,closed=True,outcome="transition_colour_exit",exit_time=end.isoformat(),
        exit_price=entry*(1+direction*gross),gross_return=gross,net_return=gross-.002,net_r=(gross-.002)/.1,
        hold_minutes=hold,partial_fraction=0.,exit_remaining_fraction=1.,realised_partial_gross_return=0.,funding_modelled=False,
        transition_initial_state="aligned",transition_initial_open_time=(start-timedelta(minutes=5)).isoformat(),
        transition_trigger_previous_open_time=(end-timedelta(minutes=10)).isoformat(),
        transition_trigger_open_time=(end-timedelta(minutes=5)).isoformat(),transition_trigger_available_at=end.isoformat())
    if parent is not None:
        row.update(parent_event_id=parent,matched_event_id=parent)
    return row


def frozen(old, *, exit=False):
    row = deepcopy(old)
    start = datetime.fromisoformat(row["entry_time"])
    row.update(frozen_ma_enabled=True,frozen_ma_boundary=row["ma"],frozen_ma_available_at=row["entry_time"],
        frozen_ma_entry_distance_atr=row["direction"]*(row["entry_price"]-row["ma"])/row["signal_atr"],
        frozen_ma_trigger_open_time="",frozen_ma_trigger_available_at="",frozen_ma_trigger_close=None,
        frozen_ma_completed_close_count=row["hold_minutes"]//5,frozen_ma_status="prior_exit")
    if exit:
        # A completed close can be wrong-side while next open rebounds; latch remains.
        end = start+timedelta(minutes=20)
        gross = -.004
        row.update(outcome="frozen_ma_exit",exit_time=end.isoformat(),exit_price=row["entry_price"]*(1+row["direction"]*gross),
            gross_return=gross,net_return=gross-.002,net_r=(gross-.002)/row["risk_pct"],hold_minutes=20,
            frozen_ma_trigger_open_time=(end-timedelta(minutes=5)).isoformat(),frozen_ma_trigger_available_at=end.isoformat(),
            frozen_ma_trigger_close=row["ma"]-row["direction"],frozen_ma_completed_close_count=4,frozen_ma_status="structure_exit",
            transition_trigger_previous_open_time="",transition_trigger_open_time="",transition_trigger_available_at="")
    return row


def episode(row):
    out = {key:row[key] for key in ("event_id","mother_decision_time","mother_signal_time","mother_deadline","fold")}
    if "parent_event_id" in row:
        out["parent_event_id"] = row["parent_event_id"]
    out.update(status="request_emitted",terminal_time=row["mother_decision_time"],episode_status=row["outcome"],
        episode_net_return=row["net_return"],executed=True,completed_trade=row["closed"],observed=row["closed"],
        entry_time=row["entry_time"],exit_time=row["exit_time"],occupied_until=row["exit_time"] if row["closed"] else row["mother_deadline"])
    return out


def metric(rows):
    values = [row["net_return"] for row in rows if row["closed"]]
    mu = sum(values)/len(values) if values else None
    loss = -sum(value for value in values if value < 0)
    return dict(events=len(values),mean_net_bp=None if mu is None else mu*1e4,
        win_rate=sum(value>0 for value in values)/len(values) if values else None,
        profit_factor=sum(value for value in values if value>0)/loss if loss else None,
        extra_10bp_mean_net_bp=None if mu is None else mu*1e4-10)


def assemble(old_cases, old_controls, new_cases, new_controls):
    tables,summaries,effects = {},{},{}
    for arm,cases,controls in (("baseline",old_cases,old_controls),("candidate",new_cases,new_controls)):
        ce,re = list(map(episode,cases)),list(map(episode,controls))
        pairs = []
        for case in ce:
            values = [row["episode_net_return"] for row in re if row["parent_event_id"]==case["event_id"]]
            cm = sum(values)/3 if len(values)==3 and all(x is not None for x in values) else None
            net = case["episode_net_return"]
            pairs.append(dict(event_id=case["event_id"],mother_decision_time=case["mother_decision_time"],fold=case["fold"],
                event_net_return=net,assigned_controls=len(values),control_mean_return=cm,
                excess=net-cm if net is not None and cm is not None else None))
        busy,serial = {},[]
        for case in sorted(ce,key=lambda row:(row["mother_decision_time"],row["event_id"])):
            start = datetime.fromisoformat(case["mother_decision_time"])
            selected = start >= busy.get(case["fold"],T0-timedelta(days=1))
            if selected:
                busy[case["fold"]] = datetime.fromisoformat(case["occupied_until"])
            serial.append(dict(case,portfolio_selected=selected,portfolio_reason="accepted_mother" if selected else "pending_or_position_busy"))
        tables[arm] = dict(case_trades=cases,control_trades=controls,case_episodes=ce,control_episodes=re,matched=pairs,single_pending=serial)
        excess = [row["excess"] for row in pairs if row["excess"] is not None]
        selected = {row["event_id"] for row in serial if row["portfolio_selected"]}
        summaries[arm] = dict(metrics=metric(cases),control_metrics=metric(controls),single_position=metric([r for r in cases if r["event_id"] in selected]),
            serial_selected_mothers=len(selected),matching=dict(paired_events=len(excess),mother_events=len(cases),coverage=len(excess)/len(cases),
            assignment_coverage=sum(row["assigned_controls"]==3 for row in pairs)/len(cases),mean_excess_bp=sum(excess)/len(excess)*1e4 if excess else None))
    for name,table,column in (("case_delta","case_episodes","episode_net_return"),("excess_delta","matched","excess"),
                              ("serial_delta","single_pending","episode_net_return")):
        before,after = (dict((r["event_id"],r) for r in tables[arm][table]) for arm in v.ARMS)
        rows = []
        for key,old in before.items():
            a,b = old[column],after[key][column]
            if name=="serial_delta":
                a = a if old["portfolio_selected"] else 0.
                b = b if after[key]["portfolio_selected"] else 0.
            rows.append(dict(event_id=key,mother_decision_time=old["mother_decision_time"],before=a,after=b,
                difference=b-a if a is not None and b is not None else None))
        values = [row["difference"] for row in rows if row["difference"] is not None]
        effects[name] = dict(total_pairs=len(rows),n=len(values),unknown_pairs=len(rows)-len(values),
            improved=sum(x>1e-12 for x in values),worsened=sum(x< -1e-12 for x in values),unchanged=sum(abs(x)<=1e-12 for x in values),
            mean_bp=sum(values)/len(values)*1e4 if values else None)
        tables[name] = rows
    return tables,summaries,effects


def fixture(*, full=False):
    n,m = (251,154) if full else (3,2)
    cases = [trade("case"+str(i),4*i,direction=1 if i%2==0 else -1,g=(-.5,0,.5,1,1.5)[i%5],gross=.003 if i%2==0 else -.004) for i in range(n)]
    controls = [trade("case{}::control{}".format(i,j),4*n+4*(i*3+j),parent="case"+str(i),
        direction=cases[i]["direction"],gross=.003 if j else -.002,g=(-.5,0,.5,1,1.5)[(i*3+j)%5]) for i in range(m) for j in range(3)]
    return assemble(cases,controls,[frozen(row,exit=i==0) for i,row in enumerate(cases)],
        [frozen(row,exit=i==0) for i,row in enumerate(controls)])


def run(data,full=False):
    return v.verify_tables(*data,expected_counts=(251,462,154) if full else (3,6,2))


def geometry(data):
    tables = data[0]
    contexts = {label:[{k:r[k] for k in ("event_id","fold","signal_time","decision_time","direction","ma","signal_close","signal_atr","initial_stop")}
        | ({"parent_event_id":r["parent_event_id"]} if label=="control" else {}) for r in tables["baseline"][label+"_trades"]] for label in ("case","control")}
    matched = {r["parent_event_id"] for r in contexts["control"]}
    assignments = [dict(event_id=r["event_id"],match_status="matched" if r["event_id"] in matched else "unmatchable") for r in contexts["case"]]
    rows = []
    counts = {group:dict.fromkeys(v.BINS,0) for group in ("all_cases","matched_cases","unmatched_cases","controls")}
    for label in ("case","control"):
        for context,trade_row in zip(contexts[label],tables["baseline"][label+"_trades"]):
            d,p,ma,atr = (trade_row[k] for k in ("direction","entry_price","ma","signal_atr"))
            risk = d*(p-trade_row["initial_stop"])
            ed,cd = d*(p-ma),d*(trade_row["signal_close"]-ma)
            g = ed/risk
            cat = "negative" if g<0 else "zero" if g==0 else "inside" if g<1 else "equal_stop" if g==1 else "beyond_stop"
            is_matched = context["event_id"] in matched if label=="case" else context["parent_event_id"] in matched
            row = dict(context,population=label,parent_event_id=context.get("parent_event_id",""),matched_case=is_matched,
                entry_open=p,raw_entry_segment_id=8,entry_distance_atr=ed/atr,entry_side=(ed>0)-(ed<0),
                previous_hour_close_distance_atr=cd/atr,previous_hour_close_side=(cd>0)-(cd<0),initial_R=risk,entry_distance_r=g,geometry_bin=cat)
            rows.append(row)
            for group in (("all_cases","matched_cases" if is_matched else "unmatched_cases") if label=="case" else ("controls",)):
                counts[group][cat]+=1
    summary = {group:dict(n=sum(bins.values()),geometry_bins=bins) for group,bins in counts.items()}
    return contexts,assignments,rows,summary


def mechanics(data,label):
    old,new = (data[0][arm][label+"_trades"] for arm in v.ARMS)
    rows,groups,transitions,dist = [],defaultdict(list),Counter(),defaultdict(list)
    for a,b in zip(old,new):
        shared = a.keys() & b.keys()
        row = {"event_id":a["event_id"]}
        for suffix,source in (("before",a),("after",b)):
            row.update({key+"_"+suffix if key in shared else key:value for key,value in source.items() if key!="event_id"})
        x,y = a["net_return"],b["net_return"]
        delta = y-x
        structural = b["outcome"]=="frozen_ma_exit"
        transition = "includes_flat" if x==0 or y==0 else ("win" if x>0 else "loss")+"_to_"+("win" if y>0 else "loss")
        group = "frozen_ma_exit" if structural else "original_exit_retained"
        row.update(difference=delta,frozen_exit=structural,win_loss_transition=transition,mechanism_group=group)
        rows.append(row);groups[group].append((x,y,delta));transitions[transition]+=1
        for field,value in (("net_return_before",x),("net_return_after",y),("difference",delta)):
            dist[field].append(value*1e4)
    group_rows = []
    for group,values in groups.items():
        group_rows.append(dict(group=group,n=len(values),known=len(values),old_mean_net_bp=sum(a for a,b,d in values)/len(values)*1e4,
            new_mean_net_bp=sum(b for a,b,d in values)/len(values)*1e4,mean_delta_bp=sum(d for a,b,d in values)/len(values)*1e4,
            sum_delta_event_bp=sum(d for a,b,d in values)*1e4,wins_before=sum(a>0 for a,b,d in values),wins_after=sum(b>0 for a,b,d in values)))
    def quantile(values,q):
        values = sorted(values);x=(len(values)-1)*q;i=int(x)
        return values[i]+(values[min(i+1,len(values)-1)]-values[i])*(x-i)
    distributions = {field:dict(n=len(values),unknown=0,outliers_removed=0,sd_bp=statistics.stdev(values) if len(values)>1 else None,
        quantiles_bp={str(q):quantile(values,q) for q in (0.,.05,.25,.5,.75,.95,1.)}) for field,values in dist.items()}
    return rows,group_rows,dict(total=len(rows),known=len(rows),frozen_ma_exits=sum(r["frozen_exit"] for r in rows),
        transitions=dict(transitions),groups=deepcopy(group_rows),distributions=distributions)


def monthly(data):
    rows=[]
    for arm in v.ARMS:
        for fold,(start,end) in v.FOLDS.items():
            year,month = map(int,start.split("-")[:2])
            for offset in range(6):
                tag="{:04d}-{:02d}".format(year,month+offset)
                values=[r["episode_net_return"] for r in data[0][arm]["case_episodes"] if r["mother_decision_time"].startswith(tag)]
                known=[x for x in values if x is not None]
                rows.append(dict(arm=arm,fold=fold,month=tag,n=len(values),known=len(known),mean_net_bp=sum(known)/len(known)*1e4 if known else None))
    return rows


def test_small_and_full_frozen_denominators():
    for full in (False,True):
        data=fixture(full=full);result=run(data,full)
        assert result["frozen_ma_exits"]=={"case":1,"control":1}
        assert result["effects"]["excess_delta"]["unknown_pairs"]==(97 if full else 1)
        assert result["raw_replay"] is result["inferential_p_recomputed"] is False


@pytest.mark.parametrize("field,value",[("entry_price",101),("initial_stop",89),("signal_atr",5),("risk_pct",.2),
    ("risk_atr",3),("direction",-1),("net_return",.1),("gross_return",.1),("net_r",4),("hold_minutes",21),
    ("partial_fraction",.5),("funding_modelled",True),("closed",False),("ma",99),("signal_close",0)])
def test_financial_and_entry_drift(field,value):
    data=fixture();data[0]["candidate"]["case_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field,value",[("frozen_ma_enabled",False),("frozen_ma_enabled",1),("frozen_ma_boundary",0),
    ("frozen_ma_boundary",94),("frozen_ma_entry_distance_atr",0),("frozen_ma_trigger_close",None),
    ("frozen_ma_completed_close_count",0),("frozen_ma_completed_close_count",4.5),("frozen_ma_status","prior_exit"),
    ("frozen_ma_available_at","2023-01-02T00:05:00Z"),("frozen_ma_trigger_open_time","2023-01-02T00:15:00.000000001Z"),
    ("frozen_ma_trigger_available_at","2023-01-02T00:20:00.000000001Z")])
def test_frozen_diagnostics_drift(field,value):
    data=fixture();data[0]["candidate"]["case_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("direction",[-1,1])
def test_strict_wrong_close_and_rebound_fill(direction):
    old=trade("a",0,direction=direction);new=frozen(old,exit=True)
    assert v.check_frozen(old,new)
    new["frozen_ma_trigger_close"]=old["ma"]
    with pytest.raises(v.VerificationError):v.check_frozen(old,new)
    new["frozen_ma_trigger_close"]=old["ma"]+direction
    with pytest.raises(v.VerificationError):v.check_frozen(old,new)


@pytest.mark.parametrize("collision",["same_old_exit","colour","gap","deadline"])
def test_higher_priority_original_exit(collision):
    old=trade("a",0);new=frozen(old,exit=True)
    if collision=="same_old_exit":old["exit_time"]=new["exit_time"]
    elif collision=="colour":new["transition_trigger_available_at"]=new["exit_time"]
    elif collision=="gap":new["exit_price"]=new["initial_stop"]
    else:
        end=T0+timedelta(hours=72);old["exit_time"]=(end+timedelta(minutes=5)).isoformat()
        new.update(exit_time=end.isoformat(),frozen_ma_trigger_available_at=end.isoformat(),
            frozen_ma_trigger_open_time=(end-timedelta(minutes=5)).isoformat(),frozen_ma_completed_close_count=864)
    with pytest.raises(v.VerificationError):v.check_frozen(old,new)


def test_latched_trigger_on_same_clock_old_colour_exit_is_not_new_exit():
    old=trade("a",0);new=frozen(old)
    new.update(frozen_ma_trigger_open_time=old["transition_trigger_open_time"],frozen_ma_trigger_available_at=old["exit_time"],
        frozen_ma_trigger_close=old["ma"]-1)
    assert v.check_frozen(old,new) is False


@pytest.mark.parametrize("field",["outcome","transition_initial_state","transition_initial_open_time","ma","signal_close"])
def test_all_old_retained_fields_enforced(field):
    data=fixture();data[0]["candidate"]["case_trades"][1][field]="corrupt"
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("table",list(v.TABLE_FILES))
@pytest.mark.parametrize("duplicate",[False,True])
def test_population_loss_or_duplication(table,duplicate):
    data=fixture();rows=data[0]["candidate"][table]
    rows.append(deepcopy(rows[0])) if duplicate else rows.pop()
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("field,value",[("parent_event_id","case2"),("decision_time","2023-01-02T16:00:00Z")])
def test_original_control_identity_and_no_reuse(field,value):
    data=fixture();data[0]["candidate"]["control_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("name",["case_delta","excess_delta","serial_delta"])
@pytest.mark.parametrize("field",["before","after","difference"])
def test_fixed_D_I_and_serial_values(name,field):
    data=fixture();data[0][name][0][field]=.123
    with pytest.raises(v.VerificationError):run(data)


def test_missing_control_outcome_never_two_control_mean_or_zero():
    data=fixture();old=data[0]["baseline"]["control_trades"];new=data[0]["candidate"]["control_trades"]
    for rows in (old,new):
        row=rows[1];row.update(closed=False,outcome="data_gap_censored",gross_return=None,net_return=None,net_r=None)
        if rows is new:row["frozen_ma_status"]="unknown_source"
    data=assemble(data[0]["baseline"]["case_trades"],old,data[0]["candidate"]["case_trades"],new)
    assert run(data)["effects"]["excess_delta"]["unknown_pairs"]==2
    data[0]["candidate"]["matched"][0].update(control_mean_return=0,excess=-.006)
    with pytest.raises(v.VerificationError):run(data)


def test_unknown_serial_reserves_whole_horizon_and_skipped_is_zero():
    data=fixture();old=data[0]["baseline"]["case_trades"];new=data[0]["candidate"]["case_trades"]
    for rows in (old,new):
        rows[0].update(closed=False,outcome="data_gap_censored",gross_return=None,net_return=None,net_r=None)
    new[0].update(frozen_ma_status="unknown_source",frozen_ma_trigger_open_time="",frozen_ma_trigger_available_at="",frozen_ma_trigger_close=None)
    data=assemble(old,data[0]["baseline"]["control_trades"],new,data[0]["candidate"]["control_trades"])
    assert run(data)["effects"]["serial_delta"]["unknown_pairs"]==1
    assert [r["portfolio_selected"] for r in data[0]["candidate"]["single_pending"]]==[True,False,False]
    data[0]["serial_delta"][0].update(before=0,after=0,difference=0)
    with pytest.raises(v.VerificationError):run(data)


def test_complete_geometry_all_five_bins_and_mirrors():
    data=fixture(full=True);contexts,assignments,rows,summary=geometry(data)
    assert v.verify_geometry(data[0],contexts,assignments,rows,summary)["rows"]==713
    assert set(r["geometry_bin"] for r in rows)==set(v.BINS)
    assert summary["unmatched_cases"]["n"]==97


@pytest.mark.parametrize("field,value",[("ma",99),("signal_atr",5),("signal_close",1),("initial_stop",89),("entry_open",101),
    ("entry_distance_atr",0),("entry_side",0),("previous_hour_close_distance_atr",0),("previous_hour_close_side",0),
    ("initial_R",1),("entry_distance_r",0),("geometry_bin","inside"),("matched_case",False),("parent_event_id","foreign"),
    ("raw_entry_segment_id",float("inf")),("signal_time","2023-01-02T00:00:00Z")])
def test_geometry_corruption(field,value):
    data=fixture();contexts,assignments,rows,summary=geometry(data);rows[0][field]=value
    with pytest.raises(v.VerificationError):v.verify_geometry(data[0],contexts,assignments,rows,summary)


def test_geometry_control_uses_own_ma_and_parent_not_case_boundary():
    data=fixture();contexts,assignments,rows,summary=geometry(data)
    row=next(r for r in rows if r["population"]=="control" and r["event_id"].endswith("1"))
    row["ma"]=rows[0]["ma"]
    with pytest.raises(v.VerificationError):v.verify_geometry(data[0],contexts,assignments,rows,summary)


@pytest.mark.parametrize("label",["case","control"])
def test_authoritative_merged_schema_and_whole_distributions(label):
    data=fixture();rows,groups,summary=mechanics(data,label)
    assert "frozen_ma_status" in rows[0] and "frozen_ma_status_after" not in rows[0]
    assert v.verify_mechanics(data[0],label,rows,groups,summary)["paired_rows"]==len(rows)
    rows[0]["frozen_ma_trigger_available_at"]="2023-01-02T00:20:00.000000001Z"
    with pytest.raises(v.VerificationError):v.verify_mechanics(data[0],label,rows,groups,summary)


@pytest.mark.parametrize("mutation",["unique_suffix","shared_missing","source_value","difference","classification","group_mean","sd","trim","quantile"])
def test_mechanics_saved_source_corruption(mutation):
    data=fixture();rows,groups,summary=mechanics(data,"case")
    if mutation=="unique_suffix":rows[0]["frozen_ma_status_after"]=rows[0].pop("frozen_ma_status")
    elif mutation=="shared_missing":rows[0].pop("ma_before")
    elif mutation=="source_value":rows[0]["entry_price_after"]=101
    elif mutation=="difference":rows[0]["difference"]=1
    elif mutation=="classification":rows[0]["frozen_exit"]=False
    elif mutation=="group_mean":groups[0]["mean_delta_bp"]+=1
    elif mutation=="sd":summary["distributions"]["difference"]["sd_bp"]+=1
    elif mutation=="trim":summary["distributions"]["difference"]["outliers_removed"]=1
    else:summary["distributions"]["difference"]["quantiles_bp"]["0.5"]+=1
    with pytest.raises(v.VerificationError):v.verify_mechanics(data[0],"case",rows,groups,summary)


def test_all48_months_including_empty_are_required():
    data=fixture();rows=monthly(data)
    assert v.verify_monthly(data[0],rows)=={"monthly_rows":48}
    rows.pop()
    with pytest.raises(v.VerificationError):v.verify_monthly(data[0],rows)


def boundary_receipt(n=251,m=462):
    return dict(at="2026-09-06T01:00:01Z",feature_spec=dict(minutes=60,ma_kind="SMA",ma_length=40,ma_source="HL2"),
        join="exact_own_signal_time",available_at="signal_time+1h == decision_time",relative_tolerance=1e-12,absolute_tolerance=1e-12,
        before_any_arm_outcomes=True,saved_values_changed=False,populations={label:dict(n=count,ma_matched=count,ma_max_abs_error=0,
        signal_close_matched=count,signal_close_max_abs_error=0) for label,count in (("case",n),("control",m))})


@pytest.mark.parametrize("mutation",[None,"late","partial","changed","ma_kind","negative_error"])
def test_boundary_source_receipt(mutation):
    row=boundary_receipt()
    if mutation=="late":row["at"]="2026-09-06T02:00:00Z"
    elif mutation=="partial":row["populations"]["control"]["ma_matched"]-=1
    elif mutation=="changed":row["saved_values_changed"]=True
    elif mutation=="ma_kind":row["feature_spec"]["ma_kind"]="EMA"
    elif mutation=="negative_error":row["populations"]["case"]["ma_max_abs_error"]=-1
    args=(row,{k:x for k,x in row.items() if k!="at"},{"at":"2026-09-06T01:00:00Z"},{"at":"2026-09-06T01:00:02Z"})
    if mutation:
        with pytest.raises(v.VerificationError):v.verify_boundary_receipt(*args)
    else:assert v.verify_boundary_receipt(*args)["independent_hourly_recomputation"] is False


def test_missing_results_and_failure_record_fail_closed_without_mutation(tmp_path):
    with pytest.raises(v.VerificationError):v.verify(tmp_path)
    results=tmp_path/v.EXPERIMENT_PATH/"results";results.mkdir(parents=True)
    path=results/"failure.json";path.write_text('{"status":"failed"}')
    before=path.read_bytes()
    with pytest.raises(v.VerificationError,match="Failed attempt"):v.verify(tmp_path)
    assert path.read_bytes()==before


def test_no_strategy_or_external_package_imports():
    import ast
    tree=ast.parse(Path(SPEC.origin).read_text())
    names=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]+[a.name for node in ast.walk(tree) if isinstance(node,ast.Import) for a in node.names]
    assert not any(name and name.split(".")[0] in {"yoyo","pandas","numpy","scipy"} for name in names)


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,sort_keys=True,allow_nan=False))


def write_csv(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    opener=gzip.open if path.suffix==".gz" else open
    with opener(path,"wt",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def disk_fixture(tmp_path,monkeypatch):
    """A full713 synthetic evidence directory, including fake committed bytes."""
    data=fixture(full=True);tables,arms,effects=data
    contexts,assignments,geo,geo_summary=geometry(data)
    results=tmp_path/v.EXPERIMENT_PATH/"results"
    olddir,motherdir=tmp_path/v.PARENT_PATH,tmp_path/v.MOTHER_PATH
    for label in ("case","control"):
        write_csv(olddir/("direct_k1_stop_"+label+"_context.csv.gz"),contexts[label])
        write_csv(motherdir/("original_mothers.csv.gz" if label=="case" else "control_mothers.csv.gz"),contexts[label])
        write_csv(results/(label+"_context.csv.gz"),contexts[label])
    for name,file in v.TABLE_FILES.items():
        write_csv(olddir/("direct_k1_stop__transition_colour_"+file),tables["baseline"][name])
        for arm in v.ARMS:write_csv(results/arm/file,tables[arm][name])
    write_json(olddir/"summary.json",{"prior":"synthetic"})
    write_json(motherdir/"assignment_receipt.json",{"assignments":154})
    write_csv(motherdir/"assignments.csv",assignments);write_csv(results/"assignments.csv",assignments)
    for name in ("case_delta","excess_delta","serial_delta"):write_csv(results/(name+".csv"),tables[name])
    write_json(results/"anchor_parity.json",{name:dict(rows=len(tables["baseline"][name]),columns=len(tables["baseline"][name][0])) for name in v.TABLE_FILES})
    root_mechanics={}
    for label in ("case","control"):
        rows,groups,summary=mechanics(data,label)
        write_csv(results/("paired_"+label+"_mechanics.csv.gz"),rows)
        write_csv(results/("mechanism_groups.csv" if label=="case" else "control_mechanism_groups.csv"),groups)
        root_mechanics["mechanics" if label=="case" else "control_mechanics"]=summary
    write_csv(results/"monthly_case_net.csv",monthly(data))
    write_csv(results/"entry_geometry.csv",geo)
    write_json(results/"entry_geometry_frozen.json",dict(at="2026-09-06T01:00:02Z",sha256=v.sha(results/"entry_geometry.csv"),
        before_any_arm_outcomes=True,population=geo_summary,used_for_selection=False))
    boundary=boundary_receipt();write_json(results/"boundary_source_parity.json",boundary)
    base=dict(execution=dict(cost_fraction=.002,max_hours=72,stop_first=True),
        development_folds=[[fold,a,b] for fold,(a,b) in v.FOLDS.items()],source={"sha256":"source-hash-not-opened"})
    write_json(tmp_path/v.BASE_PATH,base)
    policy=dict(id="5m_native40",management_minutes=5,ma_kind="SMA",ma_length=40,exit_mode="transition_colour",confirmations=1)
    config=dict(experiment_id=v.EXPERIMENT_ID,base_config=v.BASE_PATH,base_config_sha256=v.sha(tmp_path/v.BASE_PATH),
        parent_results=v.PARENT_PATH,mother_results=v.MOTHER_PATH,
        inputs={str(p.relative_to(olddir)):v.sha(p) for p in olddir.iterdir()},
        mother_inputs={str(p.relative_to(motherdir)):v.sha(p) for p in motherdir.iterdir()},
        policies=[policy,dict(policy,id="5m_native40_frozen_ma",frozen_ma_exit=True)],boundary_contract=v.BOUNDARY_CONTRACT,
        known_support=dict(cases=251,controls=462,matched=154,coverage_gate_unattainable=True),
        inference=dict(draws=9999,seed=20260906,p_limit=.01,joint_required=["case_delta","excess_delta"],method="month_cluster"),
        selection=dict(minimum_events=80,minimum_per_fold=12,positive_folds=4,minimum_profit_factor=1.1,
            minimum_active_months=12,minimum_months_per_fold=3,matched_coverage=.9),
        no_audit_entry_point=True,holdout_consumed=False,production_eligible=False,training_eligible=False)
    write_json(tmp_path/v.EXPERIMENT_PATH/"config.json",config)
    sourcepaths=v.REQUIRED_CODE_SOURCES | {v.EXPERIMENT_PATH+"/config.json",v.BASE_PATH,v.EXPERIMENT_PATH+"/PROJECT_PLAN.md"}
    committed={path:(tmp_path/path).read_bytes() if (tmp_path/path).exists() else ("synthetic committed source "+path).encode() for path in sourcepaths}
    sources=[dict(path=path,sha256=hashlib.sha256(content).hexdigest()) for path,content in sorted(committed.items())]
    started=dict(at="2026-09-06T01:00:00Z",builder_commit="a"*40,sources=sources,inputs=config["inputs"],mother_inputs=config["mother_inputs"])
    write_json(results/"started.json",started)
    summary=dict(experiment_id=v.EXPERIMENT_ID,status="diagnostic_only_no_candidate_acceptance",holdout_consumed=False,
        production_eligible=False,training_eligible=False,audit_prices_loaded=False,config_sha256=v.sha(tmp_path/v.EXPERIMENT_PATH/"config.json"),
        boundary_contract=v.BOUNDARY_CONTRACT,known_coverage_ceiling=154/251,coverage_required=.9,source=dict(sha256=base["source"]["sha256"],
        holdout_price_rows=0,phase_price_last_open="2024-12-31T23:55:00Z"),inputs=config["inputs"],mother_inputs=config["mother_inputs"],
        sources=sources,arms=arms,effects=effects,entry_geometry=geo_summary,boundary_source_parity={k:x for k,x in boundary.items() if k!="at"},
        gates=dict(matched_coverage=False),all_financial_gates_pass=False,**root_mechanics)
    for arm in v.ARMS:write_json(results/arm/"summary.json",arms[arm])
    def refresh():
        summary["output_hashes"]={str(path.relative_to(results)):v.sha(path) for path in results.rglob("*") if path.is_file() and path!=results/"summary.json"}
        write_json(results/"summary.json",summary)
    refresh()
    def fake_git(command,**kwargs):
        assert command[:2]==["git","show"]
        if "--format=%ct" in command:return SimpleNamespace(stdout=str(int(datetime(2026,9,6,tzinfo=timezone.utc).timestamp())))
        commit,path=command[2].split(":",1)
        assert commit=="a"*40 and not path.startswith("data/")
        return SimpleNamespace(stdout=committed[path])
    monkeypatch.setattr(v.h.subprocess,"run",fake_git)
    return results,summary,config,committed,refresh


def test_full_directory_receipts_sources_and_no_writes(tmp_path,monkeypatch):
    results,summary,config,committed,refresh=disk_fixture(tmp_path,monkeypatch)
    before={str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    output=v.verify(tmp_path)
    assert output["status"]=="passed" and output["counts"]==dict(cases=251,controls=462,matched=154,unmatched=97)
    assert output["geometry"]["rows"]==713 and output["committed_sources_verified"]==18
    assert output["diagnostics"]["monthly_rows"]==48 and output["effects"]["excess_delta"]["n"]==154
    assert {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}==before


@pytest.mark.parametrize("mutation",["output_hash","extra_output","missing_output","input_hash","source_bytes","omitted_source",
    "config_file","committed_config","geometry_hash","geometry_selection","boundary_receipt","support_promotion","failure"])
def test_full_directory_corruptions_fail_closed(tmp_path,monkeypatch,mutation):
    results,summary,config,committed,refresh=disk_fixture(tmp_path,monkeypatch)
    if mutation=="output_hash":(results/"case_delta.csv").write_text("damaged")
    elif mutation=="extra_output":(results/"stray.csv").write_text("stray")
    elif mutation=="missing_output":(results/"case_delta.csv").unlink()
    elif mutation=="input_hash":(tmp_path/v.PARENT_PATH/"summary.json").write_text("changed old evidence")
    elif mutation=="source_bytes":committed["yoyo/layers/l3_backtest/hourly_impulse.py"]+=b"changed"
    elif mutation=="omitted_source":
        summary["sources"]=summary["sources"][1:];start=v.read_json(results/"started.json");start["sources"]=summary["sources"]
        write_json(results/"started.json",start);refresh()
    elif mutation=="config_file":
        config["policies"][1]["frozen_ma_exit"]=1;write_json(tmp_path/v.EXPERIMENT_PATH/"config.json",config)
    elif mutation=="committed_config":
        path=v.EXPERIMENT_PATH+"/config.json";committed[path]+=b"\n"
        digest=hashlib.sha256(committed[path]).hexdigest()
        for source in summary["sources"]:
            if source["path"]==path:source["sha256"]=digest
        start=v.read_json(results/"started.json");start["sources"]=summary["sources"];write_json(results/"started.json",start);refresh()
    elif mutation in ("geometry_hash","geometry_selection"):
        path=results/"entry_geometry_frozen.json";row=v.read_json(path)
        row["sha256" if mutation=="geometry_hash" else "used_for_selection"]="x" if mutation=="geometry_hash" else True
        write_json(path,row);refresh()
    elif mutation=="boundary_receipt":
        row=v.read_json(results/"boundary_source_parity.json");row["populations"]["case"]["n"]=250
        write_json(results/"boundary_source_parity.json",row);refresh()
    elif mutation=="support_promotion":summary["all_financial_gates_pass"]=True;refresh()
    else:write_json(results/"failure.json",{"status":"failed"});refresh()
    with pytest.raises(v.VerificationError):v.verify(tmp_path)

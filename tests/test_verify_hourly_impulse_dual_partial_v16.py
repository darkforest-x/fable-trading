"""Independent synthetic saved-ledger examples; no strategy or price imports."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import hashlib
import csv
import gzip
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_dual_v16", ROOT/"scripts/verify_hourly_impulse_dual_partial_v16.py")
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)


def iso(time):
    return time.isoformat()


def bar(time, side, native="native-7", raw="raw-19"):
    return dict(open_time=iso(time), side=side, ma=100., hl2=100. if side == 1 else 99.,
        management_segment_id=native, raw_segment_id=raw)


def trade(key, offset, direction=1, gross=.01, parent=None, hold=120):
    start = datetime(2023, 1, 2, tzinfo=timezone.utc)+timedelta(hours=offset)
    end = start+timedelta(minutes=hold)
    r = dict(event_id=key, direction=direction, entry_time=iso(start), decision_time=iso(start),
        mother_decision_time=iso(start), mother_deadline=iso(start+timedelta(hours=72)),
        signal_time=iso(start-timedelta(hours=1)), fold="2023H1", entry_price=100., initial_stop=100.-direction*10.,
        signal_atr=5., risk_pct=.1, risk_atr=2., ma=100., signal_close=100., wait_hours=0,
        exit_time=iso(end), exit_price=100.*(1+direction*gross), outcome="transition_colour_exit", closed=True,
        gross_return=gross, net_return=gross-.002, net_r=(gross-.002)/.1, hold_minutes=hold,
        partial_fraction=0., exit_remaining_fraction=1., partial_exit_time=None, partial_exit_price=None,
        realised_partial_gross_return=0., marked_gross_return=gross, marked_net_return=gross-.002,
        max_favourable_r=2., max_adverse_r=-.5, funding_modelled=False,
        transition_initial_state="aligned", transition_initial_side=direction, transition_initial_reason="valid",
        transition_initial_open_time=iso(start-timedelta(minutes=15)),
        transition_trigger_previous_open_time=iso(end-timedelta(minutes=30)),
        transition_trigger_open_time=iso(end-timedelta(minutes=15)), transition_trigger_available_at=iso(end))
    if parent is not None: r["parent_event_id"] = parent
    return r


def candidate(old, partial=False):
    r = deepcopy(old)
    start = datetime.fromisoformat(r["entry_time"])
    seed = bar(start-timedelta(minutes=5), r["direction"])
    r.update(partial_fast_enabled=True, partial_fast_fraction=.5, partial_fast_profit_threshold=.002,
        partial_fast_initial_state="aligned", partial_fast_initial_side=r["direction"], partial_fast_initial_reason="valid",
        partial_fast_initial_open_time=seed["open_time"], partial_fast_initial_available_at=iso(start),
        partial_fast_initial_management_segment_id=seed["management_segment_id"], partial_fast_initial_raw_segment_id=seed["raw_segment_id"],
        partial_fast_initial_ma=seed["ma"], partial_fast_initial_hl2=seed["hl2"], partial_fast_first_armed_at=iso(start),
        partial_fast_reset_count=0, partial_fast_last_reset_reason="", partial_fast_flip_count=0, partial_fast_fill_count=0,
        partial_fast_realised_net_return=0., partial_fast_events="[]", partial_fast_status="no_partial_exit",
        partial_fast_trigger_previous_open_time=None, partial_fast_trigger_open_time=None, partial_fast_trigger_available_at=None,
        partial_fast_trigger_previous_side=None, partial_fast_trigger_side=None, partial_fast_trigger_gross_return=None,
        partial_fast_slow_open_time=None, partial_fast_slow_available_at=None, partial_fast_slow_side=None, partial_fast_slow_state="unknown")
    if partial:
        event = edge(r, minutes=5)
        r["partial_fast_events"] = json.dumps([event])
        fill(r, event)
    return r


def edge(row, minutes=5, price=None, slow="aligned", action="executed"):
    start = datetime.fromisoformat(row["entry_time"])
    now = start+timedelta(minutes=minutes)
    available = now.replace(minute=now.minute//15*15, second=0, microsecond=0)
    direction = row["direction"]
    price = 100.+direction*.5 if price is None else price
    slow_side = direction if slow == "aligned" else -direction if slow == "opposite" else None
    slow_bar = bar(available-timedelta(minutes=15), slow_side or direction, native="native-2")
    if slow == "unknown": slow_bar["side"] = None
    return dict(available_at=iso(now), open_price=price, gross_return=direction*(price/100.-1), profit_threshold=.002,
        profit_qualified=v.profit_qualified(price,100.,direction), action=action,
        previous_fast=bar(now-timedelta(minutes=10),direction), current_fast=bar(now-timedelta(minutes=5),-direction),
        slow=slow_bar, slow_available_at=iso(available), slow_state=slow, slow_reason="valid" if slow != "unknown" else "nonfinite_management")


def fill(row, event):
    row.update(partial_fraction=.5, exit_remaining_fraction=.5, partial_exit_time=event["available_at"], partial_exit_price=event["open_price"],
        realised_partial_gross_return=.5*event["gross_return"], partial_fast_fill_count=1, partial_fast_flip_count=len(json.loads(row["partial_fast_events"])),
        partial_fast_realised_net_return=.5*(event["gross_return"]-.002), partial_fast_status="partial_closed",
        partial_fast_trigger_previous_open_time=event["previous_fast"]["open_time"], partial_fast_trigger_open_time=event["current_fast"]["open_time"],
        partial_fast_trigger_available_at=event["available_at"], partial_fast_trigger_previous_side=event["previous_fast"]["side"],
        partial_fast_trigger_side=event["current_fast"]["side"], partial_fast_trigger_gross_return=event["gross_return"],
        partial_fast_slow_open_time=event["slow"]["open_time"], partial_fast_slow_available_at=event["slow_available_at"],
        partial_fast_slow_side=event["slow"]["side"], partial_fast_slow_state="aligned")
    gross = row["realised_partial_gross_return"]+.5*row["direction"]*(row["exit_price"]/row["entry_price"]-1)
    row.update(gross_return=gross, net_return=gross-.002, net_r=(gross-.002)/row["risk_pct"], marked_gross_return=gross, marked_net_return=gross-.002)


def episode(r):
    return dict(event_id=r["event_id"], fold=r["fold"], status="request_emitted", executed=True,
        terminal_time=r["mother_decision_time"], mother_decision_time=r["mother_decision_time"], mother_deadline=r["mother_deadline"],
        completed_trade=r["closed"], observed=r["closed"], episode_status=r["outcome"], episode_net_return=r["net_return"],
        entry_time=r["entry_time"], exit_time=r["exit_time"], occupied_until=r["exit_time"] if r["closed"] else r["mother_deadline"])


def metrics(rows):
    values = [r["net_return"] for r in rows if r["closed"]]
    losses = -sum(x for x in values if x < 0)
    avg = sum(values)/len(values) if values else None
    return dict(events=len(values), mean_net_bp=avg*1e4 if avg is not None else None,
        win_rate=sum(x > 0 for x in values)/len(values) if values else None,
        profit_factor=sum(x for x in values if x > 0)/losses if losses else None,
        extra_10bp_mean_net_bp=avg*1e4-10 if avg is not None else None)


def assemble(old_cases, old_controls, new_cases, new_controls):
    tables, summaries, serial = {}, {}, {}
    for arm,cases,controls in (("baseline",old_cases,old_controls),("candidate",new_cases,new_controls)):
        ce, co = [episode(r) for r in cases], [episode(r) for r in controls]
        triples = {}
        for r in controls: triples.setdefault(r["parent_event_id"],[]).append(r["net_return"])
        pairs = []
        for r in ce:
            values = triples.get(r["event_id"],[])
            cm = sum(values)/3 if len(values) == 3 and None not in values else None
            net = r["episode_net_return"]
            pairs.append(dict(event_id=r["event_id"], mother_decision_time=r["mother_decision_time"], fold=r["fold"],
                assigned_controls=len(values), event_net_return=net, control_mean_return=cm,
                excess=net-cm if net is not None and cm is not None else None))
        single = deepcopy(ce)
        free = {}
        serial[arm] = {}
        for r in sorted(single,key=lambda x:(x["mother_decision_time"],x["event_id"])):
            now = datetime.fromisoformat(r["mother_decision_time"])
            selected = r["fold"] not in free or now >= free[r["fold"]]
            if selected: free[r["fold"]] = datetime.fromisoformat(r["occupied_until"])
            r.update(portfolio_selected=selected, portfolio_reason="accepted_mother" if selected else "pending_or_position_busy")
            serial[arm][r["event_id"]] = r["episode_net_return"] if selected else 0.
        tables[arm] = dict(case_trades=cases,control_trades=controls,case_episodes=ce,control_episodes=co,matched=pairs,single_pending=single)
        excess = [r["excess"] for r in pairs if r["excess"] is not None]
        selected = {r["event_id"] for r in single if r["portfolio_selected"]}
        summaries[arm] = dict(policy=deepcopy(v.BASE_POLICY if arm == "baseline" else v.CANDIDATE_POLICY),
            metrics=metrics(cases),control_metrics=metrics(controls),serial_selected_mothers=len(selected),
            single_position=metrics([r for r in cases if r["event_id"] in selected]),
            matching=dict(paired_events=len(excess),mother_events=len(cases),coverage=len(excess)/len(cases),
                assignment_coverage=len(triples)/len(cases),mean_excess_bp=sum(excess)/len(excess)*1e4 if excess else None))
    effects = {}
    for name,table,column in (("case_delta","case_episodes","episode_net_return"),("excess_delta","matched","excess"),("serial_delta",None,None)):
        rows = []
        for i,case in enumerate(old_cases):
            a,b = (serial[arm][case["event_id"]] if table is None else tables[arm][table][i][column] for arm in v.ARMS)
            rows.append(dict(event_id=case["event_id"],mother_decision_time=case["mother_decision_time"],before=a,after=b,
                difference=b-a if a is not None and b is not None else None))
        tables[name] = rows
        delta = [r["difference"] for r in rows if r["difference"] is not None]
        effects[name] = dict(total_pairs=len(rows),n=len(delta),unknown_pairs=len(rows)-len(delta),
            improved=sum(x>1e-12 for x in delta),worsened=sum(x< -1e-12 for x in delta),unchanged=sum(abs(x)<=1e-12 for x in delta),
            mean_bp=sum(delta)/len(delta)*1e4 if delta else None)
    matched = len({r["parent_event_id"] for r in old_controls})
    summary = dict(experiment_id=v.EXPERIMENT_ID,status="diagnostic_only_no_candidate_acceptance",arms=summaries,effects=effects,
        holdout_consumed=False,audit_prices_loaded=False,production_eligible=False,training_eligible=False,
        known_coverage_ceiling=matched/len(old_cases),coverage_required=.9,gates={"matched_coverage":False},all_financial_gates_pass=False)
    return tables,summary


def fixture(full=False):
    n,m = (251,154) if full else (4,2)
    cases = [trade("case"+str(i),i*4,direction=1 if i%2 == 0 else -1,gross=.01 if i%2 == 0 else -.02) for i in range(n)]
    controls = [trade("control{}-{}".format(i,j),4*n+4*(i*3+j),parent="case"+str(i),direction=cases[i]["direction"],
        gross=.01 if j else -.02) for i in range(m) for j in range(3)]
    return assemble(cases,controls,[candidate(r,partial=i%3 != 2) for i,r in enumerate(cases)],
        [candidate(r,partial=i%2 == 0) for i,r in enumerate(controls)])


def run(data, full=False):
    return v.verify_tables(*data,expected_counts=(251,462,154) if full else (4,6,2))


@pytest.mark.parametrize("full",[False,True])
def test_complete_original_denominators_and_weighted_profit(full):
    result = run(fixture(full),full)
    assert result["counts"] == dict(cases=251 if full else 4,controls=462 if full else 6,matched=154 if full else 2,unmatched=97 if full else 2)
    assert result["effects"]["excess_delta"]["unknown_pairs"] == (97 if full else 2)
    assert result["accounting"]["unchanged_final_paths"] == (713 if full else 10)
    assert result["raw_replay"] is result["inferential_p_recomputed"] is False
    assert result["unlogged_edges_excluded_independently"] is False


def test_profitable_half_can_reduce_winner_or_leave_loser_negative():
    tables,summary = fixture()
    old,new = tables["baseline"]["case_trades"],tables["candidate"]["case_trades"]
    assert 0 < new[0]["net_return"] < old[0]["net_return"]
    assert old[1]["net_return"] < new[1]["net_return"] < 0
    run((tables,summary))


@pytest.mark.parametrize("field,value",[("entry_price",101),("initial_stop",89),("signal_atr",4),("risk_pct",.2),
    ("risk_atr",3),("direction",-1),("ma",101),("signal_close",101),("gross_return",.1),("net_return",.1),("net_r",3),
    ("hold_minutes",125),("exit_price",101.1),("closed",False),("max_favourable_r",999),("max_adverse_r",-999),
    ("funding_modelled",True),("partial_fraction",1),("exit_remaining_fraction",1),("realised_partial_gross_return",.1),
    ("marked_gross_return",.1),("marked_net_return",.1),("partial_fast_fill_count",2),
    ("partial_fast_realised_net_return",.1),("partial_fast_profit_threshold",.003),("partial_fast_fraction",.25)])
def test_original_path_fraction_accounting_and_cost_mutations_fail(field,value):
    data=fixture();data[0]["candidate"]["case_trades"][0][field]=value
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("direction",[-1,1])
def test_quote_equality_is_not_profitable_despite_binary_gross(direction):
    price = 100.+direction*.2
    assert not v.profit_qualified(price,100.,direction)
    assert v.profit_qualified(price+direction*.000001,100.,direction)
    row=candidate(trade("x",0,direction),partial=True)
    event=edge(row,price=price)
    row["partial_fast_events"]=json.dumps([event]);fill(row,event)
    with pytest.raises(v.VerificationError):v.check_partial(row)
    event["action"]="insufficient_profit"
    row=candidate(trade("x",0,direction));row.update(partial_fast_events=json.dumps([event]),partial_fast_flip_count=1)
    v.check_partial(row)


@pytest.mark.parametrize("slow,action",[("opposite","slow_not_aligned"),("unknown","slow_unknown"),("aligned","insufficient_profit")])
def test_first_qualifying_not_first_any_edge_and_only_one_fill(slow,action):
    row=candidate(trade("x",0))
    rejected=edge(row,minutes=5,price=100.1 if slow=="aligned" else 100.5,slow=slow,action=action)
    chosen=edge(row,minutes=15)
    later=edge(row,minutes=25,action="already_partial")
    row["partial_fast_events"]=json.dumps([rejected,chosen,later]);fill(row,chosen)
    v.check_trade(row,True)
    rejected["action"]="already_partial";row["partial_fast_events"]=json.dumps([rejected,chosen,later])
    with pytest.raises(v.VerificationError):v.check_partial(row)


@pytest.mark.parametrize("mutation",["segment","raw_segment","colour","equal_colour","future_slow","prior_slow","duplicate", "order", "terminal", "unarmed", "count", "second_fill", "omitted_fill", "fill_price", "fake_unknown"])
def test_edge_time_colour_firstness_and_scalar_audit(mutation):
    row=candidate(trade("x",0),True);events=json.loads(row["partial_fast_events"]);e=events[0]
    if mutation=="segment":e["current_fast"]["management_segment_id"]="other"
    elif mutation=="raw_segment":e["current_fast"]["raw_segment_id"]="other"
    elif mutation=="colour":e["current_fast"]["hl2"]=101
    elif mutation=="equal_colour":e["current_fast"]["hl2"]=100
    elif mutation=="future_slow":e["slow_available_at"]=e["available_at"]
    elif mutation=="prior_slow":e["slow_available_at"]=e["slow"]["open_time"]
    elif mutation=="duplicate":events.append(deepcopy(e));row["partial_fast_flip_count"]=2
    elif mutation=="order":events.insert(0,edge(row,15));row["partial_fast_flip_count"]=2
    elif mutation=="terminal":e["available_at"]=row["exit_time"]
    elif mutation=="unarmed":row["partial_fast_first_armed_at"]=e["available_at"]
    elif mutation=="count":row["partial_fast_flip_count"]=0
    elif mutation=="second_fill":events.append(edge(row,15));row["partial_fast_flip_count"]=2
    elif mutation=="omitted_fill":e["action"]="insufficient_profit"
    elif mutation=="fill_price":row["partial_exit_price"]=101
    elif mutation=="fake_unknown":e["slow_state"]="unknown"
    row["partial_fast_events"]=json.dumps(events)
    with pytest.raises(v.VerificationError):v.check_partial(row)


def test_native_and_raw_segment_counters_are_not_compared_across_domains():
    row=candidate(trade("x",0),True)
    assert json.loads(row["partial_fast_events"])[0]["slow"]["management_segment_id"] != "native-7"
    v.check_partial(row)


@pytest.mark.parametrize("mutation",["drop_case","drop_control","parent","reused_time","unmatched_zero","delta_zero","effect_n","serial_skip","missing_mfe","new_exit_time"])
def test_denominators_pairing_unknown_and_complete_path_parity(mutation):
    data=fixture();t,s=data
    if mutation=="drop_case":t["candidate"]["case_trades"].pop()
    elif mutation=="drop_control":t["candidate"]["control_trades"].pop()
    elif mutation=="parent":t["candidate"]["control_trades"][0]["parent_event_id"]="case3"
    elif mutation=="reused_time":t["candidate"]["control_trades"][0]["decision_time"]=t["candidate"]["control_trades"][1]["decision_time"]
    elif mutation=="unmatched_zero":t["candidate"]["matched"][-1]["control_mean_return"]=0
    elif mutation=="delta_zero":t["excess_delta"][-1]["difference"]=0
    elif mutation=="effect_n":s["effects"]["case_delta"]["n"]=3
    elif mutation=="serial_skip":t["candidate"]["single_pending"][0]["portfolio_selected"]=False
    elif mutation=="missing_mfe":del t["candidate"]["case_trades"][0]["max_favourable_r"]
    elif mutation=="new_exit_time":t["candidate"]["case_trades"][0]["exit_time"]="2023-01-02T02:00:00.000000001Z"
    with pytest.raises(v.VerificationError):run(data)


def test_partial_known_but_whole_unknown_and_occupancy_not_freed():
    data=fixture();a=data[0]["baseline"];z=data[0]["candidate"]
    for row in (a["case_trades"][0],z["case_trades"][0]):
        row.update(closed=False,outcome="data_gap_censored",gross_return=None,net_return=None,net_r=None,
            transition_trigger_previous_open_time=None,transition_trigger_open_time=None,transition_trigger_available_at=None)
    z["case_trades"][0]["partial_fast_status"]="partial_censored"
    data=assemble(a["case_trades"],a["control_trades"],z["case_trades"],z["control_trades"])
    result=run(data)
    assert result["effects"]["case_delta"]["unknown_pairs"]==1
    assert result["effects"]["serial_delta"]["unknown_pairs"]==1
    assert data[0]["candidate"]["single_pending"][1]["portfolio_selected"] is False
    assert data[0]["serial_delta"][1]["difference"]==0
    data[0]["candidate"]["case_episodes"][0]["episode_net_return"]=0
    with pytest.raises(v.VerificationError):run(data)


@pytest.mark.parametrize("value",[float("nan"),float("inf"),True,"NaN","Infinity"])
def test_nonfinite_or_boolean_numbers_rejected(value):
    data=fixture();data[0]["candidate"]["case_trades"][0]["net_return"]=value
    with pytest.raises(v.VerificationError):run(data)


def test_standard_json_and_csv_safety(tmp_path):
    with pytest.raises(v.VerificationError):v.parse_json('{"x":1,"x":2}')
    with pytest.raises(v.VerificationError):v.parse_json('[NaN]')
    for identity in ("../raw.csv","/abs/raw.csv","data/prices.csv","x/../a"):
        with pytest.raises(v.VerificationError):v.safe_path(tmp_path,identity)
    for data in ("a,a\n1,2\n","a,b\n1\n","a\n1,2\n"):
        path=tmp_path/"bad.csv";path.write_text(data)
        with pytest.raises(v.VerificationError):v.read_csv(path)


def test_no_local_import_strategy_or_price_reader():
    import ast
    tree=ast.parse((ROOT/"scripts/verify_hourly_impulse_dual_partial_v16.py").read_text())
    modules=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):modules += [x.name for x in node.names]
        elif isinstance(node,ast.ImportFrom):modules.append(node.module)
    assert not any(x.startswith(("yoyo","pandas","numpy","scripts","verify_hourly")) for x in modules)


def context_rows(tables):
    native,fast=[],[]
    for population in ("case","control"):
        for arm in v.ARMS:
            for trade in tables[arm][population+"_trades"]:
                start=datetime.fromisoformat(trade["entry_time"])
                side=trade["direction"]
                row=dict(event_id=trade["event_id"],decision_time=trade["decision_time"],direction=side,
                    mg_entry_state="aligned",mg_entry_known=True,mg_entry_side=side,mg_entry_aligned=True,
                    mg_entry_native_minutes=15,mg_entry_reason="valid",mg_entry_bar_open=iso(start-timedelta(minutes=15)),
                    mg_entry_available_at=iso(start),mg_entry_ma=100.,mg_entry_hl2=100. if side==1 else 99.,
                    mg_entry_management_segment_id="native-2",mg_entry_raw_segment_id="raw-19",arm=arm,population=population)
                trade.update({k:x for k,x in row.items() if k.startswith("mg_entry_")})
                native.append(row)
                if arm=="candidate":
                    f=deepcopy(row);f.pop("arm");f.update(mg_entry_native_minutes=5,
                        mg_entry_bar_open=iso(start-timedelta(minutes=5)),mg_entry_management_segment_id="native-7")
                    fast.append(f)
    return native,fast


def test_fast_slow_freeze_has_all_own_rows_and_distinct_native_memory():
    tables,_=fixture();native,fast=context_rows(tables)
    counts=v.verify_contexts(native,fast,tables)
    assert sum(counts.values())==20 and len(fast)==10
    assert set(counts)=={(a,p,"aligned") for a in v.ARMS for p in ("case","control")}


@pytest.mark.parametrize("mutation",["drop_fast","duplicate_fast","wrong_clock","wrong_side","wrong_ma","wrong_segment","slow_arm_drift","unknown_as_aligned"])
def test_frozen_context_lineage_mutations_fail(mutation):
    tables,_=fixture();native,fast=context_rows(tables)
    if mutation=="drop_fast":fast.pop()
    elif mutation=="duplicate_fast":fast.append(deepcopy(fast[0]))
    elif mutation=="wrong_clock":fast[0]["mg_entry_available_at"]="2023-01-02T00:05:00Z"
    elif mutation=="wrong_side":fast[0]["mg_entry_side"]=-1
    elif mutation=="wrong_ma":fast[0]["mg_entry_ma"]=100.1
    elif mutation=="wrong_segment":fast[0]["mg_entry_raw_segment_id"]="other"
    elif mutation=="slow_arm_drift":native[4]["mg_entry_hl2"]=101.
    else:fast[0]["mg_entry_known"]=False
    with pytest.raises(v.VerificationError):v.verify_contexts(native,fast,tables)


def source_fixture(tmp_path, monkeypatch):
    experiment=tmp_path/"experiments"/"active"/v.EXPERIMENT_ID
    results=experiment/"results";results.mkdir(parents=True)
    def write(path,obj):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(obj));return path
    original=write(tmp_path/"experiments/active/old/results/saved.json",{"original":True})
    cfg=dict(experiment_id=v.EXPERIMENT_ID,policies=[v.BASE_POLICY,v.CANDIDATE_POLICY],
        parent_results="experiments/active/old/results",inputs={"saved.json":v.sha(original)},
        mother_results="experiments/active/old/results",mother_inputs={"saved.json":v.sha(original)},
        entry_context_results="experiments/active/old/results",entry_context_inputs={"saved.json":v.sha(original)},
        base_config="experiments/active/base/config.json")
    base=write(tmp_path/cfg["base_config"],dict(execution=dict(cost_fraction=.002,max_hours=72,stop_first=True),
        development_folds=[[f,a,z] for f,(a,z) in v.FOLDS.items()],source={"sha256":"a"*64}))
    cfg["base_config_sha256"]=v.sha(base)
    write(experiment/"config.json",cfg)
    identity=str(experiment.relative_to(tmp_path))
    source_paths=["yoyo/layers/l3_backtest/hourly_impulse.py","yoyo/evaluation/hourly_impulse_dual_partial_research.py",
        identity+"/config.json",identity+"/PROJECT_PLAN.md",cfg["base_config"]]
    contents={}
    for name in source_paths:
        path=tmp_path/name
        if not path.exists():path.parent.mkdir(parents=True,exist_ok=True);path.write_text("synthetic committed source")
        contents[name]=path.read_bytes()
    sources=[dict(path=name,sha256=hashlib.sha256(content).hexdigest()) for name,content in contents.items()]
    started=dict(sources=sources,builder_commit="b"*40,at="2026-09-06T08:00:00Z")
    write(results/"started.json",started)
    summary=dict(sources=sources,config_sha256=v.sha(experiment/"config.json"),inputs=cfg["inputs"],mother_inputs=cfg["mother_inputs"],
        entry_context_inputs=cfg["entry_context_inputs"],source=dict(sha256="a"*64,holdout_price_rows=0,phase_price_last_open="2024-12-31T23:55:00Z"),
        output_hashes={"started.json":v.sha(results/"started.json")})
    write(results/"summary.json",summary)
    def git(cmd,**kwargs):
        if "--format=%ct" in cmd:return SimpleNamespace(stdout="100\n")
        return SimpleNamespace(stdout=contents[cmd[-1].split(":",1)[1]])
    monkeypatch.setattr(v.subprocess,"run",git)
    return results,summary,contents,started


def test_source_receipts_hash_all_outputs_and_gitshow_original_not_current(tmp_path,monkeypatch):
    results,summary,contents,_=source_fixture(tmp_path,monkeypatch)
    # Auditor was not the strategy builder, and later source edits do not erase
    # the hash of the pinned source that actually built the saved experiment.
    (tmp_path/"yoyo/layers/l3_backtest/hourly_impulse.py").write_text("later code")
    result=v.verify_sources(tmp_path,results,summary)
    assert result["committed_sources_verified"]==5
    assert result["output_hashes_verified"]==1
    assert not any("verify_hourly" in row["path"] for row in result["source_pins"])


@pytest.mark.parametrize("mutation",["source_hash","source_missing","duplicate","unsafe","output_hash","extra_output","old_input","config","future","late_commit","git_unavailable"])
def test_source_receipt_corruption_cannot_be_silently_skipped(tmp_path,monkeypatch,mutation):
    results,summary,contents,started=source_fixture(tmp_path,monkeypatch)
    if mutation=="source_hash":contents["yoyo/layers/l3_backtest/hourly_impulse.py"]=b"changed"
    elif mutation in ("source_missing","duplicate","unsafe"):
        if mutation=="source_missing":summary["sources"].pop(0)
        elif mutation=="duplicate":summary["sources"].append(summary["sources"][0])
        else:summary["sources"].append(dict(path="data/prices.csv",sha256="a"*64))
        started["sources"]=summary["sources"]
        (results/"started.json").write_text(json.dumps(started))
        summary["output_hashes"]["started.json"]=v.sha(results/"started.json")
    elif mutation=="output_hash":summary["output_hashes"]["started.json"]="c"*64
    elif mutation=="extra_output":(results/"unlisted.txt").write_text("extra")
    elif mutation=="old_input":(tmp_path/"experiments/active/old/results/saved.json").write_text("changed")
    elif mutation=="config":(results.parent/"config.json").write_text("{}")
    elif mutation=="future":summary["source"]["phase_price_last_open"]="2025-01-01T00:00:00Z"
    elif mutation=="late_commit":monkeypatch.setattr(v.subprocess,"run",lambda cmd,**kwargs:SimpleNamespace(stdout="9999999999\n" if "--format=%ct" in cmd else contents[cmd[-1].split(":",1)[1]]))
    else:
        def error(*args,**kwargs):raise v.subprocess.CalledProcessError(1,args[0])
        monkeypatch.setattr(v.subprocess,"run",error)
    with pytest.raises((v.VerificationError,KeyError)):v.verify_sources(tmp_path,results,summary)


def test_cli_fixed_default_counts_and_no_overwrite(tmp_path,monkeypatch,capsys):
    called=[]
    def check(results,summary):
        called.append((results,summary));return dict(status="passed",counts={"cases":251},raw_replay=False)
    monkeypatch.setattr(v,"verify",check)
    out=tmp_path/"receipts/check.json"
    monkeypatch.setattr("sys.argv",["verify","--results",str(tmp_path/"results"),"--summary",str(tmp_path/"summary.json"),"--out",str(out)])
    assert v.main()==0 and len(called)==1 and json.loads(out.read_text())["counts"]["cases"]==251
    original=out.read_bytes()
    assert v.main()==1 and out.read_bytes()==original and len(called)==1
    assert '"status": "failed"' in capsys.readouterr().out


def lineage_fixture(tmp_path):
    tables,summary=fixture();native,fast=context_rows(tables)
    results=tmp_path/"experiments/active"/v.EXPERIMENT_ID/"results"
    parent="experiments/active/exp-btcusdtp-1h-native15-exit-preholdout-20260906-v15/results/candidate"
    context="experiments/active/synthetic-v5/results"
    mothers="experiments/active/synthetic-v4/results"
    def j(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
    def c(path,rows):
        path.parent.mkdir(parents=True,exist_ok=True)
        with (gzip.open if path.suffix==".gz" else open)(path,"wt",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=list(rows[0]) if rows else ["event_id"])
            writer.writeheader();writer.writerows(rows)
    j(results.parent/"config.json",dict(parent_results=parent,entry_context_results=context,mother_results=mothers))
    anchor={}
    for arm in v.ARMS:
        for table,file in v.TABLE_FILES.items():
            c(results/arm/file,tables[arm][table])
            if arm=="baseline":
                c(tmp_path/parent/file,tables[arm][table]);anchor[table]=dict(rows=len(tables[arm][table]),columns=len(tables[arm][table][0]))
    for delta in v.DELTAS:c(results/(delta+".csv"),tables[delta])
    for population in ("case","control"):
        originals=[{key:row[key] for key in ("event_id","decision_time","direction","initial_stop","signal_atr","signal_time","fold")}
            for row in tables["baseline"][population+"_trades"]]
        c(results/(population+"_context.csv.gz"),originals)
        c(tmp_path/context/("direct_k1_stop_"+population+"_context.csv.gz"),originals)
        c(tmp_path/mothers/(("original_mothers" if population=="case" else "control_mothers")+".csv.gz"),originals)
    assigned=[dict(event_id="case"+str(i),match_status="matched" if i<2 else "insufficient_candidates") for i in range(4)]
    c(results/"assignments.csv",assigned);c(tmp_path/mothers/"assignments.csv",assigned)
    c(results/"native_entry_context.csv.gz",native);c(results/"fast_entry_context.csv.gz",fast)
    counts=[dict(arm=a,population=p,mg_entry_state="aligned",n=4 if p=="case" else 6) for a in v.ARMS for p in ("case","control")]
    c(results/"native_initial_state_counts.csv",counts)
    j(results/"started.json",dict(at="2026-09-06T08:00:00Z"))
    j(results/"anchor_parity.json",anchor)
    frozen=dict(at="2026-09-06T08:01:00Z",before_outcome_reads=True,entry_gates=False,outcomes_hashed_or_read=False,
        rows=len(native),fast_rows=len(fast),counts=counts,
        context_sha256=v.sha(results/"native_entry_context.csv.gz"),fast_context_sha256=v.sha(results/"fast_entry_context.csv.gz"))
    j(results/"context_frozen.json",frozen)
    edges=[]
    for population in ("case","control"):
        for row in tables["candidate"][population+"_trades"]:
            for event in json.loads(row["partial_fast_events"]):
                x=dict(event_id=row["event_id"],population=population,**event)
                for field in ("previous_fast","current_fast","slow"):x[field]=json.dumps(x[field])
                edges.append(x)
    c(results/"partial_fast_edges.csv.gz",edges)
    summary["native_context"]=counts
    return results,v.load_tables(results),summary,frozen,c


def test_saved_anchor_context_freeze_and_flat_edge_export(tmp_path):
    results,tables,summary,_,_=lineage_fixture(tmp_path)
    result=v.verify_saved_lineage(tmp_path,results,tables,summary)
    assert result==dict(anchor_tables=6,native_context_rows=20,fast_context_rows=10,recorded_fast_edges=6,
        context_freeze_is_saved_receipt_not_runtime_trace=True)


@pytest.mark.parametrize("mutation",["anchor_value","anchor_missing","context_count","freeze_clock","freeze_post_outcome","freeze_sha","fake_assignment","edge_omit","edge_duplicate","edge_source"])
def test_lineage_and_freeze_drift_fails(tmp_path,mutation):
    results,tables,summary,frozen,c=lineage_fixture(tmp_path)
    if mutation=="anchor_value":tables["baseline"]["case_trades"][0]["ma"]="99"
    elif mutation=="anchor_missing":tables["baseline"]["case_trades"].pop()
    elif mutation=="context_count":frozen["fast_rows"]=9
    elif mutation=="freeze_clock":frozen["at"]="2026-09-06T07:59:00Z"
    elif mutation=="freeze_post_outcome":frozen["outcomes_hashed_or_read"]=True
    elif mutation=="freeze_sha":frozen["fast_context_sha256"]="c"*64
    elif mutation=="fake_assignment":
        rows=v.read_csv(results/"assignments.csv");rows[-1]["match_status"]="matched";c(results/"assignments.csv",rows)
    else:
        rows=v.read_csv(results/"partial_fast_edges.csv.gz")
        if mutation=="edge_omit":rows.pop()
        elif mutation=="edge_duplicate":rows.append(rows[0])
        else:
            source=json.loads(rows[0]["slow"]);source["ma"]=110.;rows[0]["slow"]=json.dumps(source)
        c(results/"partial_fast_edges.csv.gz",rows)
    (results/"context_frozen.json").write_text(json.dumps(frozen))
    with pytest.raises(v.VerificationError):v.verify_saved_lineage(tmp_path,results,tables,summary)

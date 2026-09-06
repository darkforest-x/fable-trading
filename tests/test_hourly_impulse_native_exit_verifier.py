"""V15 synthetic saved-row counterexamples; no market or historical reads."""
from copy import deepcopy
from datetime import datetime,timedelta
import importlib.util
from pathlib import Path

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

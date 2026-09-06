"""Pure synthetic support-graph tests; no archive, outcome or file loading."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_matching_support as audit
from yoyo.evaluation.hourly_impulse_k2_matching import assign_controls, build_matching_frame


END = pd.Timestamp("2024-02-01", tz="UTC")


def state_frame(times=None):
    times = pd.to_datetime(times if times is not None else ["2024-01-10 01:00", "2024-01-10 02:00", "2024-01-10 03:00",
        "2024-01-11 01:00", "2024-01-12 01:00"], utc=True)
    f = pd.DataFrame({"decision_time": times, "signal_time": times-pd.Timedelta(hours=1),
        "signal_atr": 1., "entry_open": 100., "open": 101., "high": 102., "low": 99., "close": 101.,
        "ma": 100., "ma_side": 1, "ma_slope_atr": .1, "source_segment_id": 0,
        "month": times.strftime("%Y-%m"), "utc_6h_bucket": times.hour//6, "vol_bucket": 0,
        "known_5m_colour": 1, "known_5m_available": times, "known_hourly_colour": 1,
        "unsigned_hourly_slope_sign": 1, "known_entry_open": True, "entry_source_continuous": True,
        "known_5m_valid": True, "known_hourly_valid": True, "actual_mother_decision_excluded": False})
    return refresh(f)


def refresh(frame):
    f=frame.copy()
    f["raw_strict_body_cross"] = ((f.open<f.ma)&(f.close>f.ma)) | ((f.open>f.ma)&(f.close<f.ma))
    times=set(f.loc[f.raw_strict_body_cross,"decision_time"])
    f["current_or_prior_cross_excluded"] = f.decision_time.isin(times|{t+pd.Timedelta(hours=1) for t in times})
    f["matching_support"] = f.vol_bucket.notna() & f.signal_atr.gt(0)
    for key in audit.SUPPORT_FLAGS:
        f["matching_support"] &= f[key]
    f["candidate_eligible"] = f.matching_support & ~f.current_or_prior_cross_excluded & ~f.actual_mother_decision_excluded
    return f


def mothers(count=1):
    return pd.DataFrame({"event_id":[f"m{i}" for i in range(count)],
        "decision_time":pd.date_range("2024-01-11 01:00",periods=count,freq="D",tz="UTC"),
        "direction":1,"initial_stop":98.,"signal_atr":1.,"fold":"F"})


def run(m=None,f=None,**kwargs):
    return audit.build_support_audit(mothers() if m is None else m,state_frame() if f is None else f,
                                     end_exclusive=END,**kwargs)


def test_original_outputs_identical_full_graph_and_all_mothers_retained():
    m,f=mothers(2),state_frame()
    m=m.iloc[::-1]
    before_m,before_f=m.copy(deep=True),f.copy(deep=True)
    result=run(m,f)
    controls,assignments,receipt=assign_controls(m,f,end_exclusive=END)
    pd.testing.assert_frame_equal(result["greedy_controls"],controls)
    pd.testing.assert_frame_equal(result["greedy_assignments"],assignments)
    assert result["greedy_diagnostics"]==receipt
    pd.testing.assert_frame_equal(m,before_m)
    pd.testing.assert_frame_equal(f,before_f)
    assert result["mother_audit"].event_id.tolist()==m.event_id.tolist()
    a=result["mother_audit"].set_index("event_id")
    assert a.loc["m0","preallocation_available"]==a.loc["m1","preallocation_available"]==3
    assert a.loc["m1","used_before_count"]==3 and a.loc["m1","available_before_greedy"]==0
    assert a.loc["m1","match_status"]=="insufficient_exact_controls"
    assert len(result["eligible_edges"])==6
    assert result["greedy_edges"].selected.sum()==3
    assert result["receipt"]["candidate_pool_count"]==3
    assert len(result["candidate_stages"])==len(f)
    assert result["key_supply"].raw_candidates.sum()==len(f)
    assert len(result["stage_counts"])==len(m)*len(audit.STAGES)
    for value in result["eligible_edges"].candidate_id:
        assert value==pd.Timestamp(value).isoformat() and value.endswith("+00:00")


@pytest.mark.parametrize("available",[0,1,2,3,4])
def test_true_zero_one_two_supply_not_conflated_with_missing_support(available):
    m=mothers()
    f=state_frame()
    candidates=f.index[f.decision_time.ne(m.decision_time.iloc[0])]
    f.loc[candidates[available:],"known_5m_valid"]=False
    result=run(m,refresh(f))
    row=result["mother_audit"].iloc[0]
    assert row.preallocation_available==row.available_before_greedy==available
    assert row.selected_count==(3 if available>=3 else 0)
    assert row.match_status==("matched" if available>=3 else "insufficient_exact_controls")
    assert len(result["eligible_edges"])==available


@pytest.mark.parametrize("field,status",[("vol_bucket","missing_causal_matching_support"),
    ("known_5m_valid","missing_causal_matching_support"),("known_hourly_valid","missing_causal_matching_support"),
    ("known_entry_open","missing_or_gapped_mother_open"),("entry_source_continuous","missing_or_gapped_mother_open")])
def test_each_missing_causal_support_flag_kept_with_unknown_availability(field,status):
    m,f=mothers(),state_frame()
    ix=f.index[f.decision_time.eq(m.decision_time.iloc[0])][0]
    f.loc[ix,field]=np.nan if field=="vol_bucket" else False
    result=run(m,refresh(f));row=result["mother_audit"].iloc[0]
    assert row.match_status==status and not row.mother_search_reached
    assert pd.isna(row.preallocation_available) and pd.isna(row.available_before_greedy)
    assert result["eligible_edges"].empty
    if field=="vol_bucket":assert row.mother_vol_bucket_missing and not row.mother_key_complete
    else:assert row["mother_"+field]==False


@pytest.mark.parametrize("field",audit.KEY_COLUMNS)
def test_missing_keys_never_match_each_other(field):
    f=state_frame();f[field]=np.nan
    result=run(f=refresh(f))
    assert result["eligible_edges"].empty
    assert not result["mother_audit"].mother_key_complete.any()
    assert not result["key_supply"].key_complete.any()
    assert result["mother_audit"].assigned_controls.sum()==0


@pytest.mark.parametrize("field,replacement",[("month","2024-02"),("utc_6h_bucket",2),("vol_bucket",2),
    ("known_5m_colour",-1),("known_hourly_colour",-1),("unsigned_hourly_slope_sign",-1)])
def test_exact_keys_cannot_be_relaxed_to_fill_a_triplet(field,replacement):
    m,f=mothers(),state_frame()
    f.loc[f.decision_time.ne(m.decision_time.iloc[0]),field]=replacement
    result=run(m,refresh(f))
    assert result["eligible_edges"].empty
    assert result["mother_audit"].available_before_greedy.iloc[0]==0


def test_current_and_previous_cross_and_defensive_actual_mother_exclusions_are_separate():
    m,f=mothers(),state_frame()
    f.loc[0,"open"]=99.
    result=run(m,refresh(f))
    rows=result["candidate_stages"]
    assert rows.current_or_prior_cross_excluded.tolist()==[True,True,False,False,False]
    assert rows.raw_strict_body_cross.tolist()==[True,False,False,False,False]
    own=rows.loc[rows.decision_time.eq(m.decision_time.iloc[0])].iloc[0]
    assert own.supplied_mother_excluded and not own.actual_mother_decision_excluded
    assert not own.pool_eligible
    assert result["mother_audit"].preallocation_available.iloc[0]==2
    # Future cross changes itself/+1h, never the previous candidate.
    f.loc[1,"open"]=99.
    repeated=run(m,refresh(f))["candidate_stages"]
    assert repeated.loc[0,"current_or_prior_cross_excluded"]==rows.loc[0,"current_or_prior_cross_excluded"]


def test_later_same_month_candidates_allowed_and_embargo_not_mother_plus_minus_72h():
    m=mothers()
    f=state_frame(["2024-01-01 01:00","2024-01-11 01:00","2024-01-20 01:00","2024-01-28 23:00","2024-01-29 00:00"])
    # Last two are other UTC buckets; retain one legal later candidate and one early.
    result=run(m,f,count=1)
    edges=result["eligible_edges"]
    assert pd.Timestamp("2024-01-20 01:00",tz="UTC") in set(edges.candidate_time)
    assert pd.Timestamp("2024-01-01 01:00",tz="UTC") in set(edges.candidate_time)
    assert edges.candidate_time.max()<END-pd.Timedelta(hours=72)
    stages=result["candidate_stages"]
    assert not stages.loc[stages.decision_time.eq(END-pd.Timedelta(hours=72)),"within_fold_embargo"].iloc[0]


def test_rows_after_fold_embargo_cannot_change_eligible_edges_or_greedy_assignment():
    first=run()
    future=state_frame(["2024-01-29 01:00","2024-02-01 01:00","2024-03-01 01:00"])
    f=pd.concat([state_frame(),future],ignore_index=True)
    f.loc[f.index[-3:],"open"]=99.
    result=run(f=refresh(f))
    for name in ("eligible_edges","greedy_controls","greedy_assignments"):
        pd.testing.assert_frame_equal(first[name],result[name])


def test_risk_is_mother_specific_and_insufficient_mother_reserves_nothing():
    m,f=mothers(2),state_frame()
    m.loc[0,"initial_stop"]=50.
    f.loc[2,"signal_atr"]=3.
    result=run(m,f)
    rows=result["mother_audit"].set_index("event_id")
    assert rows.loc["m0","preallocation_available"]==2
    assert rows.loc["m0","selected_count"]==0
    assert rows.loc["m1","preallocation_available"]==3
    assert rows.loc["m1","global_used_before_count"]==0
    assert rows.loc["m1","selected_count"]==3
    assert result["eligible_edges"].groupby("event_id").size().to_dict()=={"m0":2,"m1":3}


def test_opposite_directions_compete_for_one_global_real_time_capacity():
    m=mothers(2);m.loc[1,["direction","initial_stop"]]=[-1,102.]
    result=run(m)
    rows=result["mother_audit"].set_index("event_id")
    assert rows.loc["m1","used_before_count"]==3
    assert rows.loc["m1","selected_count"]==0
    assert rows.loc["m1","signed_hourly_slope_sign"]==-1
    assert result["greedy_controls"].decision_time.is_unique


@pytest.mark.parametrize("change,status",[("missing_time","missing_mother_hourly_decision"),
    ("null_time","missing_mother_hourly_decision"),("embargo","outside_fold_embargo"),
    ("negative_risk","invalid_mother_risk"),("wrong_atr","mother_atr_mismatch"),
    ("wrong_side","invalid_mother_risk")])
def test_invalid_mothers_are_not_dropped_or_assigned_zero_supply(change,status):
    m,f=mothers(),state_frame()
    if change=="missing_time":m.loc[0,"decision_time"]=pd.Timestamp("2024-01-25",tz="UTC")
    elif change=="null_time":m.loc[0,"decision_time"]=pd.NaT
    elif change=="embargo":
        m.loc[0,"decision_time"]=END-pd.Timedelta(hours=72)
        f=pd.concat([f,state_frame([m.decision_time.iloc[0]])],ignore_index=True)
    elif change=="negative_risk":m.loc[0,"initial_stop"]=101.
    elif change=="wrong_atr":m.loc[0,"signal_atr"]=2.
    else:m.loc[0,"direction"]=0
    result=run(m,f);row=result["mother_audit"].iloc[0]
    assert row.match_status==status and len(result["mother_audit"])==1
    assert pd.isna(row.available_before_greedy)
    assert result["eligible_edges"].empty


@pytest.mark.parametrize("seed",[1,7,20260906])
def test_seed_order_timezone_and_optional_outcomes_do_not_change_graph_or_original_algorithm(seed):
    m,f=mothers(2),state_frame()
    expected=run(m,f,seed=seed)
    m=m.iloc[::-1].copy();m["net_return"]=[np.inf,-999.];m["k2_success"]=[True,False]
    m.attrs["hidden_outcome"]="ignored"
    m["decision_time"]=m.decision_time.dt.tz_convert("Asia/Shanghai")
    f=f.iloc[::-1].copy();f["future_outcome"]="ignore"
    f.attrs["hidden_future_return"]=[999.]
    f["decision_time"]=f.decision_time.dt.tz_convert("America/New_York")
    actual=run(m,f,seed=seed)
    for name in ("greedy_controls","greedy_assignments","eligible_edges","greedy_edges"):
        pd.testing.assert_frame_equal(expected[name],actual[name])
    assert not {"net_return","k2_success"}.intersection(actual["mother_audit"])
    assert actual["candidate_stages"].attrs=={}


@pytest.mark.parametrize("change",["duplicate_candidate","duplicate_id","numeric_time","numeric_mother",
    "null_flag","contradictory_flag","missing_column","duplicate_column","mixed_fold"])
def test_malformed_contracts_fail_closed(change):
    m,f=mothers(2),state_frame()
    if change=="duplicate_candidate":f=pd.concat([f,f.iloc[:1]],ignore_index=True)
    elif change=="duplicate_id":m.loc[1,"event_id"]=m.event_id.iloc[0]
    elif change=="numeric_time":f["decision_time"]=range(len(f))
    elif change=="numeric_mother":m["decision_time"]=range(len(m))
    elif change=="null_flag":f["known_5m_valid"]=pd.NA
    elif change=="contradictory_flag":f.loc[0,"candidate_eligible"]=False
    elif change=="missing_column":f=f.drop(columns="raw_strict_body_cross")
    elif change=="duplicate_column":f=pd.concat([f,f[["ma"]]],axis=1)
    else:m.loc[1,"fold"]="another"
    with pytest.raises((ValueError,TypeError)):
        run(m,f)


def test_empty_mothers_and_empty_pool_have_explicit_schema_and_no_edges():
    result=run(m=mothers(0))
    assert result["mother_audit"].empty and result["eligible_edges"].empty
    assert result["eligible_edges"].columns.tolist()==audit.EDGE_COLUMNS
    assert result["greedy_edges"].columns.tolist()==audit.GREEDY_EDGE_COLUMNS
    assert result["receipt"]["all_mothers_retained"]
    result=run(f=state_frame().iloc[:0])
    assert result["mother_audit"].match_status.tolist()==["missing_mother_hourly_decision"]


def test_independent_available_and_selection_check_rejects_corrupted_allocator(monkeypatch):
    original=audit.assign_controls
    def corrupt(*args,**kwargs):
        controls,assignments,diagnostics=original(*args,**kwargs)
        assignments["available_controls"]+=1
        return controls,assignments,diagnostics
    monkeypatch.setattr(audit,"assign_controls",corrupt)
    with pytest.raises(AssertionError,match="available_controls"):
        run()


def test_real_matching_frame_builder_contract_and_warmup_on_synthetic_hours():
    times=pd.date_range("2024-01-01",periods=500,freq="h",tz="UTC")
    h=pd.DataFrame({"open_time":times,"open":101.,"high":102.,"low":97.,"close":101.,
        "atr":1.,"ma":100.,"ma_side":1,"ma_slope_atr":.1,"segment_id":0})
    rawtimes=pd.date_range(times[0],periods=501*12,freq="5min")
    raw=pd.DataFrame({"open_time":rawtimes,"open":100.,"segment_id":0})
    mg=raw[["open_time","segment_id"]].assign(ma_side=1)
    m=mothers(2);m["decision_time"]=[times[100]+pd.Timedelta(hours=1),times[250]+pd.Timedelta(hours=1)]
    f=build_matching_frame(raw,h,mg,m).assign(audit_fold="F")
    result=run(m,f)
    rows=result["mother_audit"].set_index("event_id")
    assert rows.loc["m0","mother_vol_bucket_missing"] and rows.loc["m0","match_status"]=="missing_causal_matching_support"
    assert rows.loc["m1","match_status"]=="matched"
    assert result["candidate_stages"].vol_bucket_missing.sum()==168
    assert result["candidate_stages"].audit_fold.eq("F").all()
    assert result["key_supply"].audit_fold.eq("F").all()
    assert result["key_supply"].vol_bucket_missing_count.sum()==168
    assert pd.isna(rows.loc["m0","mother_atr_tercile_low"])


@pytest.mark.parametrize("flag,count_column",[("known_entry_open","invalid_entry_open_count"),
    ("entry_source_continuous","gapped_entry_count"),("known_5m_valid","invalid_5m_count"),
    ("known_hourly_valid","invalid_hourly_count")])
def test_supply_table_retains_each_support_failure_without_claiming_disjoint_counts(flag,count_column):
    f=state_frame();f.loc[0,flag]=False
    result=run(f=refresh(f))
    assert result["key_supply"][count_column].sum()==1
    assert result["receipt"]["support_failure_counts_overlap"] is True
    assert result["mother_audit"].preallocation_available.iloc[0]==3


def test_selection_parity_checks_candidate_times_not_only_available_count(monkeypatch):
    original=audit.assign_controls
    def corrupt(*args,**kwargs):
        controls,assignments,diagnostics=original(*args,**kwargs)
        controls.loc[0,"decision_time"]+=pd.Timedelta(minutes=5)
        return controls,assignments,diagnostics
    monkeypatch.setattr(audit,"assign_controls",corrupt)
    with pytest.raises(AssertionError,match="selection"):
        run()

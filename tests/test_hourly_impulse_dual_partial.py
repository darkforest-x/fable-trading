"""Synthetic native5-event partial on native15 exits; no real data access."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


E = pd.Timestamp("2024-01-01T01:00:00Z")
FIVE = pd.Timedelta(minutes=5)
BASE = {"management_minutes":15,"exit_mode":"transition_colour","confirmations":1}
POLICY = {**BASE,"fast_partial_fraction":.5}


def raw_bars(n=900):
    return pd.DataFrame(dict(open_time=pd.date_range(E-3*FIVE,periods=n,freq="5min"),
        open=100.,high=101.,low=99.,close=100.,volume=1.,segment_id=19))


def management(minutes, sides, *, start=None):
    frame=pd.DataFrame(dict(open_time=pd.date_range(E-pd.Timedelta(minutes=minutes) if start is None else start,
        periods=len(sides),freq=str(minutes)+"min"),ma=np.where(np.asarray(sides)==1,99.,101.),ma_side=sides,ma_slope_atr=np.nan,
        low=99.,high=101.,close=100.,segment_id=7 if minutes==5 else 2))
    frame.attrs.update(ma_kind="SMA",ma_length=40,bar_minutes=minutes)
    return frame


def request(direction=1, *, phase=0):
    return pd.DataFrame([dict(event_id="dual",decision_time=E+pd.Timedelta(minutes=phase),direction=direction,
        initial_stop=90. if direction==1 else 110.,signal_atr=2.,source_feature=.3)])


def set_bar(raw, minutes, open_, *, stop=None, invalid=False):
    loc=raw.open_time.eq(E+pd.Timedelta(minutes=minutes))
    raw.loc[loc,["open","high","low","close"]]=[open_,max(open_,100.)+1,min(open_,100.)-1,open_]
    if stop is not None:
        raw.loc[loc,"low" if stop<100 else "high"]=stop
    if invalid:raw.loc[loc,["high","low","close"]]=np.nan


def fixture(direction=1):
    raw=raw_bars()
    set_bar(raw,5,100+direction*2)
    set_bar(raw,30,100+direction*4,invalid=True)
    slow=management(15,[direction,direction,-direction])
    fast=management(5,[direction,-direction,-direction,direction,-direction,-direction,-direction])
    return raw,slow,fast,request(direction)


def run(data, *, policy=None, cutoff=None):
    raw,slow,fast,entries=data
    return simulate_events(raw,slow,entries,POLICY if policy is None else policy,
        fast_management_featured=fast,end_exclusive=cutoff)


@pytest.mark.parametrize("direction",[-1,1])
def test_one_partial_weighted_return_and_original_full_exit(direction):
    data=fixture(direction);result=run(data).iloc[0]
    assert result.outcome=="transition_colour_exit" and result.exit_time==E+6*FIVE
    assert result.partial_exit_time==E+FIVE and result.partial_exit_price==100+direction*2
    assert result.partial_fraction==result.exit_remaining_fraction==.5
    assert result.realised_partial_gross_return==pytest.approx(.01)
    assert result.partial_fast_fill_count==1 and result.partial_fast_realised_net_return==pytest.approx(.009)
    assert result.gross_return==pytest.approx(.5*.02+.5*.04)
    assert result.net_return==pytest.approx(.028) and result.net_r==pytest.approx(.28)
    assert result.partial_fast_initial_state=="aligned" and result.partial_fast_initial_open_time==E-FIVE
    assert result.partial_fast_trigger_previous_open_time==E-FIVE
    assert result.partial_fast_trigger_open_time==E and result.partial_fast_trigger_available_at==E+FIVE
    assert result.partial_fast_slow_available_at==E and result.partial_fast_slow_open_time==E-3*FIVE
    assert result.partial_fast_slow_side==direction and result.partial_fast_slow_state=="aligned"
    assert result.partial_fast_status=="partial_closed" and not result.funding_modelled
    events=json.loads(result.partial_fast_events)
    assert [row["action"] for row in events]==["executed","already_partial"]
    assert result.partial_fast_flip_count==2 and events[0]["previous_fast"]["side"]==direction
    assert events[0]["current_fast"]["side"]==-direction
    assert events[0]["slow"]["management_segment_id"]=="2"
    assert events[0]["slow"]["raw_segment_id"]=="19"


@pytest.mark.parametrize("direction",[-1,1])
@pytest.mark.parametrize("move",[-1.,0.,.199,.2,.2001])
def test_strict20bp_no_floating_boundary_or_latched_later_price(direction,move):
    data=fixture(direction)
    set_bar(data[0],5,100+direction*move)
    data[2].loc[:,"ma_side"]=[direction,-direction,-direction,-direction,-direction,-direction,-direction]
    set_bar(data[0],10,100+direction*5)
    result=run(data).iloc[0]
    executed=move>.2
    assert result.partial_fraction==(.5 if executed else 0.)
    events=json.loads(result.partial_fast_events)
    assert len(events)==1
    assert events[0]["action"]==("executed" if executed else "insufficient_profit")
    assert events[0]["profit_qualified"] is executed


@pytest.mark.parametrize("direction",[-1,1])
def test_economic_failure_can_retry_only_on_another_true_edge(direction):
    data=fixture(direction);set_bar(data[0],5,100+direction*.1);set_bar(data[0],20,100+direction*3)
    result=run(data).iloc[0]
    assert result.partial_exit_time==E+4*FIVE
    assert [row["action"] for row in json.loads(result.partial_fast_events)]==["insufficient_profit","executed"]


@pytest.mark.parametrize("direction",[-1,1])
def test_initial_opposite_waits_for_observed_align_then_opposite(direction):
    data=fixture(direction)
    data[2].loc[:,"ma_side"]=[-direction,-direction,direction,-direction,-direction,-direction,-direction]
    set_bar(data[0],15,100+direction*2)
    result=run(data).iloc[0]
    assert result.partial_fast_initial_state=="opposite"
    assert result.partial_fast_first_armed_at==E+2*FIVE
    assert result.partial_exit_time==E+3*FIVE


@pytest.mark.parametrize("kind",["missing_seed","unknown_seed","missing","nonfinite","segment","zero_side"])
def test_fast_seed_reset_never_bridges_invalid_or_missing_colour(kind):
    data=fixture();fast=data[2]
    fast.loc[:,"ma_side"]=[1,1,-1,1,-1,-1,-1]
    if kind=="missing_seed":data=(data[0],data[1],fast.drop(index=0),data[3])
    elif kind=="unknown_seed":fast.loc[0,"ma_side"]=np.nan
    elif kind=="missing":data=(data[0],data[1],fast.drop(index=1),data[3])
    elif kind=="nonfinite":fast.loc[1,"ma"]=np.nan
    elif kind=="zero_side":fast.loc[1,"ma_side"]=0
    else:fast.loc[2:,"segment_id"]=77
    set_bar(data[0],10,102);set_bar(data[0],20,103)
    result=run(data).iloc[0]
    expected=E+2*FIVE if kind in ("missing_seed","unknown_seed") else E+4*FIVE
    assert result.partial_exit_time==expected
    if kind in ("missing_seed","unknown_seed"):
        assert result.partial_fast_initial_state=="unknown"
        assert result.partial_fast_first_armed_at==E+FIVE
    else:assert result.partial_fast_reset_count>=1


@pytest.mark.parametrize("kind",["opposite","missing","invalid","stale"])
def test_latest_slow_context_required_no_older_aligned_fallback(kind):
    data=fixture();slow=data[1]
    data[2].loc[:,"ma_side"]=[1,1,1,-1,1,-1,-1]
    set_bar(data[0],15,102);set_bar(data[0],25,103)
    # Keep initial slow opposite/unknown so bar ending15 cannot full-exit;
    # the event nevertheless cannot treat an old aligned value as latest.
    slow.loc[0,"ma_side"]=-1
    if kind=="opposite":slow.loc[1,"ma_side"]=-1
    elif kind=="missing":data=(data[0],slow.drop(index=1),data[2],data[3])
    elif kind=="invalid":slow.loc[1,"ma"]=np.nan
    else:slow.loc[1,"open_time"]+=FIVE
    result=run(data).iloc[0]
    assert result.partial_fraction==0
    actions=[row["action"] for row in json.loads(result.partial_fast_events)]
    assert actions and set(actions)==({"slow_not_aligned"} if kind=="opposite" else {"slow_unknown"})


@pytest.mark.parametrize("direction",[-1,1])
def test_native15_full_exit_wins_simultaneous_profitable_fast_edge(direction):
    data=fixture(direction)
    data[1].loc[1,"ma_side"]=-direction
    data[2].loc[:,"ma_side"]=[direction,direction,direction,-direction,-direction,-direction,-direction]
    set_bar(data[0],15,100+direction*4,invalid=True)
    result=run(data).iloc[0]
    assert result.exit_time==E+3*FIVE and result.outcome=="transition_colour_exit"
    assert result.partial_fraction==0 and result.partial_fast_events=="[]"


@pytest.mark.parametrize("direction",[-1,1])
def test_gap_stop_before_edge_and_current_intrabar_stop_only_remaining(direction):
    data=fixture(direction);stop=data[3].initial_stop.iloc[0]
    set_bar(data[0],5,stop-direction)
    result=run(data).iloc[0]
    assert result.outcome=="hard_stop_gap" and result.partial_fraction==0
    assert result.partial_fast_events=="[]"
    data=fixture(direction);set_bar(data[0],5,100+direction*2,stop=stop)
    result=run(data).iloc[0]
    assert result.partial_fraction==.5 and result.outcome=="hard_stop" and result.exit_time==E+2*FIVE
    assert result.gross_return==pytest.approx(.01-.05)
    assert result.net_return==pytest.approx(-.042)
    assert result.initial_stop==stop


def test_previous_bar_stop_cannot_be_retroactively_partially_realised():
    data=fixture();data[0].loc[data[0].open_time.eq(E),"low"]=90
    result=run(data).iloc[0]
    assert result.exit_time==E+FIVE and result.outcome=="hard_stop"
    assert result.partial_fraction==0 and result.partial_fast_events=="[]"


@pytest.mark.parametrize("kind",["missing","segment","segment_inf","open_nan","invalid_prior"])
def test_raw_source_failure_precedes_discretionary_partial(kind):
    data=fixture();raw=data[0]
    if kind=="missing":data=(raw.loc[~raw.open_time.eq(E+FIVE)],data[1],data[2],data[3])
    elif kind=="segment":raw.loc[raw.open_time.ge(E+FIVE),"segment_id"]=20
    elif kind=="segment_inf":raw["segment_id"]=raw.segment_id.astype(float);raw.loc[raw.open_time.ge(E+FIVE),"segment_id"]=np.inf
    elif kind=="open_nan":raw.loc[raw.open_time.eq(E+FIVE),"open"]=np.nan
    else:raw.loc[raw.open_time.eq(E),"close"]=np.nan
    result=run(data).iloc[0]
    assert result.outcome=="data_gap_censored" and result.partial_fraction==0 and not result.closed
    assert pd.isna(result.net_return)


def test_current_bad_hlc_cannot_undo_open_partial_but_whole_result_unknown():
    data=fixture();set_bar(data[0],5,102,invalid=True)
    result=run(data).iloc[0]
    assert result.partial_exit_time==E+FIVE and result.partial_fraction==.5
    assert result.realised_partial_gross_return==pytest.approx(.01)
    assert not result.closed and pd.isna(result.net_return) and pd.isna(result.net_r)
    assert result.partial_fast_status=="partial_censored"


def test_terminal_only_reads_open_and_future_suffix_does_not_change_result():
    data=fixture();expected=run(data)
    raw=data[0].copy();raw.loc[raw.open_time.gt(E+6*FIVE),["open","high","low","close"]]=-999.
    result=run((raw,*data[1:]))
    pd.testing.assert_frame_equal(expected,result)


def test_cutoff_preserves_realised_half_without_synthetic_full_fill():
    data=fixture();result=run(data,cutoff=E+pd.Timedelta(minutes=6)).iloc[0]
    assert result.partial_exit_time==E+FIVE and result.exit_time==E+FIVE
    assert result.partial_fraction==.5 and result.partial_fast_status=="partial_censored"
    assert result.outcome=="right_censored" and not result.closed and pd.isna(result.net_return)
    before=run(data,cutoff=E+FIVE).iloc[0]
    assert before.partial_fraction==0


def test_deadline_precedes_new_partial_and_keeps_prior_partial_if_already_realised():
    data=fixture()
    at5=run(data,policy={**POLICY,"max_minutes":5}).iloc[0]
    assert at5.outcome=="time_exit" and at5.partial_fraction==0 and at5.partial_fast_events=="[]"
    at10=run(data,policy={**POLICY,"max_minutes":10}).iloc[0]
    assert at10.outcome=="time_exit" and at10.partial_fraction==.5 and at10.exit_time==E+2*FIVE


def test_frozen20bp_trigger_unchanged_by30bp_cost_stress():
    data=fixture();normal=run(data).iloc[0];stress=run(data,policy={**POLICY,"cost_fraction":.003}).iloc[0]
    assert normal.partial_exit_time==stress.partial_exit_time
    assert normal.partial_fast_events==stress.partial_fast_events
    assert normal.gross_return==stress.gross_return
    assert normal.net_return-stress.net_return==pytest.approx(.001)


@pytest.mark.parametrize("override",[{"fast_partial_fraction":0},{"fast_partial_fraction":1},{"fast_partial_fraction":True},
    {"fast_partial_fraction":np.nan},{"fast_partial_fraction":"0.5"},{"management_minutes":5},{"management_minutes":60},
    {"exit_mode":"colour"},{"exit_mode":"partial_colour"},{"confirmations":2},{"confirmations":np.bool_(True)},
    {"decision_minutes":15},{"decision_minutes":5},{"launch_deadline_minutes":60,"launch_progress_r":.5},{"frozen_ma_exit":True}])
def test_unregistered_policy_combinations_rejected(override):
    with pytest.raises(ValueError):run(fixture(),policy={**POLICY,**override})


@pytest.mark.parametrize("field,value",[("bar_minutes",15),("bar_minutes",None),("ma_kind","EMA"),("ma_length",39)])
def test_fast_provenance_cannot_silently_relabel_bars(field,value):
    data=fixture();data[2].attrs[field]=value
    with pytest.raises(ValueError):run(data)


def test_slow_provenance_missing_fast_and_unrequested_fast_rejected():
    data=fixture();raw,slow,fast,entries=data
    with pytest.raises(ValueError):simulate_events(raw,slow,entries,POLICY)
    with pytest.raises(ValueError):run(data,policy=BASE)
    slow.attrs.clear()
    with pytest.raises(ValueError):run(data)


def test_invalid_risk_does_not_create_partial_or_initial_seed():
    data=fixture();data[3]["initial_stop"]=101
    result=run(data).iloc[0]
    assert result.outcome=="entry_invalid_risk" and result.partial_fast_status=="entry_not_validated"
    assert result.partial_fast_events=="[]" and result.partial_fast_initial_reason=="entry_not_validated"


def test_empty_opt_in_has_schema_and_absent_policy_no_new_columns():
    data=fixture();empty=data[3].iloc[:0]
    result=simulate_events(data[0],data[1],empty,POLICY,fast_management_featured=data[2])
    assert result.empty and "partial_fast_events" in result and "partial_fast_initial_side" in result
    baseline=simulate_events(data[0],data[1],empty,BASE)
    assert not any(column.startswith("partial_fast_") for column in baseline)


def test_no_trigger_baseline_all_old_fields_exactly_preserved():
    data=fixture();data[2]["ma_side"]=1
    baseline=simulate_events(data[0],data[1],data[3],BASE)
    candidate=run(data)
    pd.testing.assert_frame_equal(baseline,candidate[baseline.columns])
    assert candidate.partial_fast_events.iloc[0]=="[]"


@pytest.mark.parametrize("phase",[0,5,10])
def test_initial_observation_uses_exact_entry5m_seed_all_phases(phase):
    raw,slow,fast,_=fixture();data=(raw,slow,fast,request(phase=phase))
    result=run(data).iloc[0]
    assert result.partial_fast_initial_open_time==E+pd.Timedelta(minutes=phase)-FIVE
    assert result.partial_fast_initial_available_at==E+pd.Timedelta(minutes=phase)
    if result.partial_fraction:
        assert result.partial_exit_time>result.entry_time


@pytest.mark.parametrize("direction",[-1,1])
@pytest.mark.parametrize("exit_gross",[-.09,-.02,.001,.01,.10])
def test_terminal_path_and_weighted_identity_preserve_original_winners(direction,exit_gross):
    data=fixture(direction);set_bar(data[0],30,100*(1+direction*exit_gross),invalid=True)
    baseline=simulate_events(data[0],data[1],data[3],BASE).iloc[0]
    candidate=run(data).iloc[0]
    for field in ("entry_time","entry_price","initial_stop","risk_pct","risk_atr","exit_time","exit_price","outcome",
        "hold_minutes","max_favourable_r","max_adverse_r","transition_trigger_previous_open_time",
        "transition_trigger_open_time","transition_trigger_available_at","transition_initial_state","transition_reset_count"):
        assert candidate[field]==baseline[field],field
    assert candidate.net_return==pytest.approx(.5*baseline.net_return+.5*(.02-.002))
    if baseline.net_return>0:assert candidate.net_return>0
    if baseline.net_return<=0:assert candidate.net_return>baseline.net_return


def test_partial_then_missing_source_preserves_partial_but_not_known_whole_profit():
    data=fixture();raw=data[0].loc[~data[0].open_time.eq(E+2*FIVE)]
    result=run((raw,*data[1:])).iloc[0]
    assert result.outcome=="data_gap_censored" and result.partial_fast_fill_count==1
    assert result.partial_fast_realised_net_return==pytest.approx(.009)
    assert pd.isna(result.net_return) and not result.closed


@pytest.mark.parametrize("direction",[-1,1])
def test_partial_uses_only_prefix_source_and_current_open(direction):
    raw,slow,fast,entries=fixture(direction)
    expected=run((raw,slow,fast,entries)).iloc[0]
    untouched=[frame.copy(deep=True) for frame in (raw,slow,fast,entries)]
    raw.loc[raw.open_time.ge(E+FIVE),["high","low","close"]]=np.nan
    raw.loc[raw.open_time.gt(E+FIVE),"open"]=1e7
    fast.loc[fast.open_time.ge(E+FIVE),["ma","ma_side","high","low","close"]]=np.nan
    slow.loc[slow.open_time.ge(E),["ma","ma_side","high","low","close"]]=np.nan
    before=[frame.copy(deep=True) for frame in (raw,slow,fast,entries)]
    observed=run((raw,slow,fast,entries)).iloc[0]
    for field in ("partial_exit_time","partial_exit_price","partial_fraction","partial_fast_trigger_open_time",
        "partial_fast_trigger_available_at","partial_fast_slow_available_at","partial_fast_realised_net_return"):
        assert observed[field]==expected[field]
    assert json.loads(observed.partial_fast_events)[0]==json.loads(expected.partial_fast_events)[0]
    for original,current in zip(before,(raw,slow,fast,entries)):pd.testing.assert_frame_equal(original,current)
    # Running the untouched inputs also preserves every caller value/attribute.
    baseline_copies=[frame.copy(deep=True) for frame in untouched]
    run(untouched)
    for original,current in zip(baseline_copies,untouched):pd.testing.assert_frame_equal(original,current)


@pytest.mark.parametrize("direction",[-1,1])
def test_nearest_decimal_price_above_threshold_not_lost_to_tolerance(direction):
    data=fixture(direction)
    quote=np.nextafter(100+direction*.2, np.inf if direction==1 else -np.inf)
    set_bar(data[0],5,quote)
    result=run(data).iloc[0]
    assert result.partial_exit_time==E+FIVE
    assert json.loads(result.partial_fast_events)[0]["profit_qualified"] is True

"""Synthetic-only V15 seed/source freeze tests; no saved market/outcome reads."""
import numpy as np
import pandas as pd
import pytest

from yoyo.data.hourly_impulse import add_features, resample_complete
from yoyo.data.hourly_impulse_management_context import attach_management_context
from yoyo.data.hourly_impulse_native_exit_context import (
    NATIVE_CONTEXT_COLUMNS, attach_native_exit_context,
)


ENTRY = pd.Timestamp("2024-01-02T00:00:00Z")


def frames(minutes=15, phase=0, direction=1):
    raw = pd.DataFrame({"open_time": pd.date_range(ENTRY-pd.Timedelta(hours=12), periods=160, freq="5min"),
        "open": 100.2, "high": 101., "low": 99., "close": 99.8, "volume": 1.})
    five = resample_complete(raw,5)
    native = add_features(resample_complete(raw,minutes), "SMA",40)
    requests = pd.DataFrame({"event_id": ["own"], "decision_time": [ENTRY+pd.Timedelta(minutes=phase)],
        "signal_time": [ENTRY-pd.Timedelta(hours=1)], "direction": [direction],
        "signal_atr": [2.], "initial_stop": [90. if direction == 1 else 110.],
        "ma": [93.], "ltf_entry_state": ["old_5m_diagnostic"], "known_5m_colour": [-1]})
    return five,native,requests


@pytest.mark.parametrize("minutes,phase", [(5,0),(5,5),(5,10),(15,0),(15,5),(15,10)])
@pytest.mark.parametrize("direction", [1,-1])
def test_known_native_seed_exact_clock_ma_and_own_direction(minutes,phase,direction):
    raw,mg,requests = frames(minutes,phase,direction)
    row = attach_native_exit_context(raw,mg,requests,minutes).iloc[0]
    available = requests.decision_time.iloc[0].floor(f"{minutes}min")
    assert row.mg_entry_bar_open == available-pd.Timedelta(minutes=minutes)
    assert row.mg_entry_available_at == available <= row.decision_time
    assert row.mg_entry_known
    assert row.mg_entry_side == 1  # Native HL2==MA, despite an actual red body.
    assert row.mg_entry_state == ("aligned" if direction==1 else "opposite")
    assert row.mg_entry_ma == row.mg_entry_hl2 == 100.
    assert row.mg_entry_native_minutes == minutes
    assert row.ma == 93.  # Original signal-hour MA is not overwritten.


@pytest.mark.parametrize("minutes", [5,15])
def test_39_native_bars_unknown_40_known_without_slope_or_atr_gate(minutes):
    raw,_,requests = frames(minutes)
    raw = raw.loc[raw.open_time.ge(ENTRY-pd.Timedelta(minutes=minutes*40))].copy()
    raw[["open","high","low","close"]] = 100.
    mg = add_features(resample_complete(raw,minutes),"SMA",40)
    queries = pd.concat([requests.assign(event_id="39", decision_time=ENTRY-pd.Timedelta(minutes=minutes)),requests])
    result = attach_native_exit_context(raw,mg,queries,minutes)
    assert result.mg_entry_known.tolist() == [False,True]
    assert result.mg_entry_state.tolist() == ["unknown","aligned"]
    assert result.mg_entry_reason.tolist() == ["nonfinite_management","valid"]
    assert pd.isna(result.mg_entry_side.iloc[0])
    assert np.isnan(result.mg_entry_ma.iloc[0])
    assert result.mg_entry_ma.iloc[1] == 100.


def test_two_native_specs_and_controls_have_their_own_seed_not_copied():
    raw,five,requests = frames(5)
    _,fifteen,_ = frames(15)
    second = requests.assign(event_id="control",decision_time=ENTRY+pd.Timedelta(minutes=15),direction=-1)
    queries = pd.concat([requests,second],ignore_index=True)
    five.loc[five.open_time.eq(ENTRY-pd.Timedelta(minutes=5)),"ma_side"] = -1
    fifteen.loc[fifteen.open_time.eq(ENTRY),"ma_side"] = -1
    a = attach_native_exit_context(raw,five,queries,5)
    b = attach_native_exit_context(raw,fifteen,queries,15)
    assert a.mg_entry_state.tolist() == ["opposite","opposite"]
    assert b.mg_entry_state.tolist() == ["aligned","aligned"]
    pd.testing.assert_frame_equal(a[queries.columns],b[queries.columns])


@pytest.mark.parametrize("minutes,phase", [(5,0),(15,0),(15,5),(15,10)])
def test_current_unfinished_and_future_ohlc_mutation_and_prefix_invariance(minutes,phase):
    raw,mg,requests = frames(minutes,phase)
    expected = attach_native_exit_context(raw,mg,requests,minutes)
    entry = requests.decision_time.iloc[0]
    raw.loc[raw.open_time.ge(entry),["high","low","close"]] = np.nan
    raw.loc[raw.open_time.gt(entry),"open"] = np.inf
    mg.loc[mg.open_time.ge(entry.floor(f"{minutes}min")),["ma","ma_side","high","low","close"]] = np.nan
    pd.testing.assert_frame_equal(expected,attach_native_exit_context(raw,mg,requests,minutes))
    pd.testing.assert_frame_equal(expected,attach_native_exit_context(
        raw.loc[raw.open_time.le(entry)],
        mg.loc[(mg.open_time+pd.Timedelta(minutes=minutes)).le(entry)],requests,minutes))


@pytest.mark.parametrize("kind", ["missing","warmup","invalid_colour","unknown_segment"])
def test_latest_native_unknown_never_falls_back_but_retains_exact_diagnostics(kind):
    raw,mg,requests = frames()
    selected = mg.open_time.eq(ENTRY-pd.Timedelta(minutes=15))
    if kind=="missing":
        mg = mg.loc[~selected]
    elif kind=="warmup":
        mg.loc[selected,"ma"] = np.nan
    elif kind=="invalid_colour":
        mg.loc[selected,"ma_side"] = 0
    else:
        mg["segment_id"] = mg.segment_id.astype(float)
        mg.loc[selected,"segment_id"] = np.nan
    row = attach_native_exit_context(raw,mg,requests,15).iloc[0]
    assert not row.mg_entry_known and row.mg_entry_state=="unknown"
    assert pd.isna(row.mg_entry_side)
    if kind=="missing":
        assert pd.isna(row.mg_entry_bar_open) and pd.isna(row.mg_entry_ma)
    else:
        assert row.mg_entry_bar_open==ENTRY-pd.Timedelta(minutes=15)
        assert row.mg_entry_hl2==100.


@pytest.mark.parametrize("offset", [-15,-10,-5,0,5,10])
def test_every_underlying_source_time_to_phase_entry_is_required(offset):
    raw,mg,requests = frames(15,10)
    raw = raw.loc[raw.open_time.ne(ENTRY+pd.Timedelta(minutes=offset))]
    row = attach_native_exit_context(raw,mg,requests,15).iloc[0]
    assert not row.mg_entry_known and row.mg_entry_reason=="missing_source"


def test_raw_segment_change_unknown_opaque_native_counter_not_equal_to_raw():
    raw,mg,requests = frames()
    raw["segment_id"] = "raw-a"
    mg["segment_id"] = "native-other"
    row = attach_native_exit_context(raw,mg,requests,15).iloc[0]
    assert row.mg_entry_known
    assert row.mg_entry_raw_segment_id=="raw-a"
    assert row.mg_entry_management_segment_id=="native-other"
    raw.loc[raw.open_time.eq(ENTRY-pd.Timedelta(minutes=5)),"segment_id"] = "raw-b"
    row = attach_native_exit_context(raw,mg,requests,15).iloc[0]
    assert not row.mg_entry_known and row.mg_entry_reason=="source_segment_change"


def test_native_warmup_resets_after_a_missing_subbar():
    raw,_,requests = frames()
    raw = raw.loc[raw.open_time.ne(ENTRY-pd.Timedelta(minutes=40))]
    mg = add_features(resample_complete(raw,15),"SMA",40)
    row = attach_native_exit_context(raw,mg,requests,15).iloc[0]
    assert row.mg_entry_reason=="nonfinite_management"
    assert row.mg_entry_known==False


@pytest.mark.parametrize("minutes", [5,15])
@pytest.mark.parametrize("initial", [1,-1,0,np.nan])
def test_independent_context_exact_engine_initial_state_not_intuitive_colour(minutes,initial):
    # Synthetic L3 call terminates immediately after initialization. The pure
    # data helper itself never imports any layer or reads outcome ledgers.
    from yoyo.layers.l3_backtest.hourly_impulse import simulate_events
    raw,mg,requests = frames(minutes)
    mg.loc[mg.open_time.eq(ENTRY-pd.Timedelta(minutes=minutes)),"ma_side"] = initial
    context = attach_native_exit_context(raw,mg,requests,minutes).iloc[0]
    result = simulate_events(raw,mg,requests,
        {"management_minutes":minutes,"exit_mode":"transition_colour","confirmations":1},
        end_exclusive=ENTRY+pd.Timedelta(minutes=1)).iloc[0]
    assert context.mg_entry_state==result.transition_initial_state
    assert context.mg_entry_reason==result.transition_initial_reason
    if context.mg_entry_known:
        assert context.mg_entry_side==result.transition_initial_side
        assert context.mg_entry_bar_open==result.transition_initial_open_time


def test_shared_validator_all_original_columns_are_unchanged():
    raw,mg,requests = frames()
    queries = pd.concat([requests.assign(event_id="z"),requests.assign(event_id="a",direction=-1)])
    queries.index = pd.Index([8,8],name="owner")
    queries["decision_time"] = queries.decision_time.dt.tz_convert("Asia/Shanghai")
    queries.attrs["source"] = {"frozen":"V5"}
    original = queries.copy(deep=True)
    result = attach_native_exit_context(raw,mg,queries,15)
    base = attach_management_context(raw,mg,queries,15)
    pd.testing.assert_frame_equal(result[base.columns],base)
    pd.testing.assert_frame_equal(result[queries.columns],original)
    pd.testing.assert_frame_equal(queries,original)
    assert result.attrs==queries.attrs
    assert list(result.columns)==list(queries.columns)+NATIVE_CONTEXT_COLUMNS


def test_invalid_initial_risk_is_not_a_colour_filter_or_missing_request():
    raw,mg,requests = frames()
    requests["initial_stop"] = 101.
    result = attach_native_exit_context(raw,mg,requests,15)
    assert len(result)==1 and result.mg_entry_known.all()
    assert result.initial_stop.iloc[0]==101.


def test_other_unknown_request_does_not_coerce_known_segment_ids_or_seed():
    raw,mg,requests = frames()
    expected = attach_native_exit_context(raw,mg,requests,15)
    unknown = requests.assign(event_id="earlier_missing",decision_time=ENTRY-pd.Timedelta(days=1))
    batch = pd.concat([requests,unknown],ignore_index=True)
    actual = attach_native_exit_context(raw,mg,batch,15).iloc[[0]]
    pd.testing.assert_frame_equal(expected,actual)


@pytest.mark.parametrize("field,value,reason", [("open",np.nan,"invalid_source_open"),
    ("segment_id",np.nan,"source_segment_change")])
def test_current_open_and_current_source_segment_are_known_entry_requirements(field,value,reason):
    raw,mg,requests = frames()
    raw[field] = raw[field].astype(float)
    raw.loc[raw.open_time.eq(ENTRY),field] = value
    result = attach_native_exit_context(raw,mg,requests,15).iloc[0]
    assert not result.mg_entry_known and result.mg_entry_reason==reason


@pytest.mark.parametrize("which", ["requests","raw","management"])
def test_empty_inputs_preserve_schema_or_explicit_unknown(which):
    raw,mg,requests = frames()
    if which=="requests":
        requests=requests.iloc[:0]
    elif which=="raw":
        raw=raw.iloc[:0]
    else:
        mg=mg.iloc[:0]
    result = attach_native_exit_context(raw,mg,requests,15)
    assert list(result.columns)==list(requests.columns)+NATIVE_CONTEXT_COLUMNS
    assert len(result)==len(requests)
    if len(result):
        assert result.mg_entry_state.eq("unknown").all()
        assert not result.mg_entry_known.any()


@pytest.mark.parametrize("column", NATIVE_CONTEXT_COLUMNS)
def test_output_collisions_rejected_not_overwritten(column):
    raw,mg,requests = frames()
    requests[column] = "untouched"
    with pytest.raises(ValueError):
        attach_native_exit_context(raw,mg,requests,15)


@pytest.mark.parametrize("problem", ["duplicate_id","null_id","duplicate_source","numeric_source","bad_interval"])
def test_malformed_ids_source_or_specification_fail(problem):
    raw,mg,requests = frames()
    minutes = 15
    if problem=="duplicate_id":
        requests = pd.concat([requests,requests])
    elif problem=="null_id":
        requests["event_id"] = None
    elif problem=="duplicate_source":
        raw = pd.concat([raw.iloc[[0]],raw],ignore_index=True)
    elif problem=="numeric_source":
        raw["open_time"] = np.arange(len(raw))
    else:
        minutes = 10
    with pytest.raises(ValueError):
        attach_native_exit_context(raw,mg,requests,minutes)


@pytest.mark.parametrize("key,value", [("ma_kind","EMA"),("ma_length",20),("bar_minutes",5),
    ("ma_kind",None),("ma_length",None),("bar_minutes",None)])
def test_feature_specification_attrs_required_and_fixed(key,value):
    raw,mg,requests = frames()
    if value is None:
        del mg.attrs[key]
    else:
        mg.attrs[key]=value
    with pytest.raises(ValueError,match="SMA40"):
        attach_native_exit_context(raw,mg,requests,15)

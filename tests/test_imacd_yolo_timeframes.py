"""Synthetic period-transfer, aggregation and native15m parity tests."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import imacd_yolo_confirmation as old
from yoyo.evaluation import imacd_yolo_timeframes as new
from yoyo.evaluation.imacd_formation_research import aggregate


def fixture(minutes, n=400):
    times = pd.date_range("2026-05-04", periods=n, freq=f"{minutes}min", tz="UTC")
    c = 100+np.sin(np.arange(n)/5)
    bars = pd.DataFrame(dict(open=c, high=c+1, low=c-1, close=c+.1,
                             volume=np.ones(n)), index=times)
    p = 40
    event = dict(event_id=f"TEST_{minutes}", symbol="TEST", timeframe_min=minutes,
        signal_i=p, side=1, setup_start_i=28, setup_bars=12,
        signal_open_at=times[p], signal_available_at=times[p+1],
        signal_close=bars.close.iloc[p], complete_followup=True)
    f = pd.DataFrame(dict(md=np.ones(n)), index=times)
    box = dict(symbol="TEST", timeframe_min=minutes, direction="long", confidence=.6,
        window_start_i=25, window_end_i=42, window_len=18,
        core_start_i=36, core_end_i=39, core_length_bars=4, confirmation_bars=3,
        structural_pass=True, detection_id="box", available_at=times[43])
    return bars, pd.DataFrame([event]), pd.DataFrame([box]), f


@pytest.mark.parametrize("minutes", [15,60,240])
def test_confirmation_clock_and_entry_use_actual_period(minutes):
    bars, events, proposals, f = fixture(minutes)
    d = new.decisions_for(events, proposals, bars, f, minutes)
    row = d.iloc[0]
    assert row.delay_bars == 2
    assert row.confirmation_available_at-row.signal_available_at == pd.Timedelta(minutes=2*minutes)
    assert row.confirmed_next_open == bars.open.iloc[43]
    assert new.validate_selection(d, proposals, minutes)["passed"]


def test_same15m_decisions_equal_frozen_original_except_identity_namespace():
    bars, events, proposals, f = fixture(15)
    a = old.decisions_for(events, proposals, bars, f)
    b = new.decisions_for(events, proposals, bars, f, 15)
    pd.testing.assert_frame_equal(a.drop(columns="core_identity"), b.drop(columns="core_identity"))


@pytest.mark.parametrize("minutes", [60,240])
def test_md_cancellation_and_trace_clock(minutes):
    bars, events, proposals, f = fixture(minutes)
    f.iloc[42,0] = 0
    d = new.decisions_for(events, proposals, bars, f, minutes)
    assert d.iloc[0].status == "invalidated"
    trace = new.trace_for(events, bars, f)
    assert trace.bar_i.tolist() == list(range(40,51))
    assert trace.loc[trace.bar_i.eq(42),"md"].iloc[0] == 0
    assert trace.iloc[-1].bar_open_at == bars.index[50]


def test_mixed_timeframe_ledgers_cannot_be_used_as_one_period():
    bars, events, proposals, f = fixture(60)
    proposals.loc[0,"timeframe_min"] = 240
    with pytest.raises(ValueError, match="mixed timeframe"):
        new.decisions_for(events, proposals, bars, f, 60)
    pool = new.eligible_pool(SimpleNamespace(**events.iloc[0].to_dict()), proposals, f.md.to_numpy())
    assert pool.empty


@pytest.mark.parametrize("minutes", [60,240])
def test_wait_exactly_nine_local_bars_and_next_endpoint_rejected(minutes):
    bars, events, boxes, f = fixture(minutes)
    boxes.loc[0,["window_start_i","window_end_i","core_start_i","core_end_i",
                 "core_length_bars","confirmation_bars","available_at"]] = [32,49,37,40,4,9,bars.index[50]]
    assert new.decisions_for(events, boxes, bars, f, minutes).iloc[0].delay_bars == 9
    boxes.loc[0,["window_start_i","window_end_i","core_start_i","core_end_i","available_at"]] = [33,50,38,41,bars.index[51]]
    assert new.decisions_for(events, boxes, bars, f, minutes).iloc[0].status == "expired"


def test_full_history_aggregation_drops_only_partial_edge_groups():
    times = pd.date_range("2022-01-03 14:45", periods=40, freq="15min", tz="UTC")
    c = 100+np.arange(40)
    raw = pd.DataFrame(dict(open=c, high=c+1, low=c-1, close=c+.5, volume=1), index=times)
    result = aggregate(raw,240)
    assert result.index.tolist() == [pd.Timestamp("2022-01-03 16:00",tz="UTC"),
                                     pd.Timestamp("2022-01-03 20:00",tz="UTC")]
    assert result.volume.tolist() == [16,16]
    assert result.open.iloc[0] == raw.open.iloc[5]
    assert result.close.iloc[-1] == raw.close.iloc[36]
    with pytest.raises(ValueError,match="gap"):
        aggregate(raw.drop(raw.index[10]),240)


@pytest.mark.parametrize("minutes", [60,240])
def test_candle_close_boundary_not_open_filters_candidates(minutes):
    times = pd.date_range(new.END-pd.Timedelta(minutes=40*minutes),periods=50,
                          freq=f"{minutes}min",tz="UTC")
    bars = pd.DataFrame(dict(open=100.,high=101.,low=99.,close=100.,volume=1.),index=times)
    f = pd.DataFrame(dict(release_side=0,focus_start_i=-1,near_zero_bars=0),index=times)
    f.iloc[38] = [1,26,12]  # close strictly before end
    f.iloc[39] = [1,27,12]  # close equals END: reject
    c = new.candidates(bars,f,"TEST",minutes)
    assert c.signal_i.tolist() == [38]
    assert c.event_id.iloc[0].startswith(f"TEST_{minutes}_")


@pytest.mark.parametrize("minutes", [60,240])
def test_rendering_has_no_future_pixels_after_timeframe_transfer(minutes):
    from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
    bars, _, _, _ = fixture(minutes)
    raw = bars.reset_index(names="open_time")
    image = new.prepare_window(add_candidate_features(raw),350,19)[0]
    changed = raw.copy()
    changed.loc[351:, ["open","high","low","close"]] *= 100
    other = new.prepare_window(add_candidate_features(changed),350,19)[0]
    prefix = new.prepare_window(add_candidate_features(raw.iloc[:351].copy()),350,19)[0]
    assert np.array_equal(image,other) and np.array_equal(image,prefix)


def test_empty_candidate_group_has_declared_schema():
    bars, events, boxes, f = fixture(60)
    d = new.decisions_for(events.iloc[:0], boxes.iloc[:0], bars, f,60)
    assert d.empty and {"status","timeframe_min","complete_followup"}.issubset(d.columns)


def test_fake_inference_has_period_clock_and_identity():
    class Array:
        def __init__(self,a): self.a=np.asarray(a)
        def cpu(self): return self
        def numpy(self): return self.a
    class Boxes:
        xywhn=Array([[.6,.5,.18,.5]]);cls=Array([0]);conf=Array([.6])
        def __len__(self): return 1
    class Model:
        def predict(self,**kw): return [SimpleNamespace(boxes=Boxes()) for _ in kw['source']]
    frames=[]
    for minutes in (60,240):
        bars,events,_,_=fixture(minutes)
        events.loc[0,'signal_i']=350
        q,stats=new.infer(bars,events,'TEST',Model(),'cpu',minutes)
        assert q.available_at.max()==bars.index[360]
        assert stats['windows_scored']==20 and q.timeframe_min.eq(minutes).all()
        frames.append(q)
    assert pd.concat(frames).detection_id.nunique()==40

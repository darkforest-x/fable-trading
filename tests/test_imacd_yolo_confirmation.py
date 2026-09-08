"""Synthetic timing, geometry, prefix and price-clock tests; no market data."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.imacd_yolo_confirmation import (
    MAX_WAIT, decisions_for, eligible_pool, prepare_window,
)


def fixture():
    n, p = 80, 40
    times = pd.date_range("2020-01-01", periods=n, freq="15min", tz="UTC")
    opens = 100+np.arange(n)*.1
    bars = pd.DataFrame(dict(open=opens, high=opens+1, low=opens-1,
                             close=opens+.2, volume=10), index=times)
    event = dict(event_id="test", symbol="TEST", signal_i=p, side=1,
                 setup_start_i=p-12, setup_bars=12, signal_open_at=times[p],
                 signal_available_at=times[p+1], signal_close=bars.close.iloc[p],
                 complete_followup=True)
    f = pd.DataFrame(dict(md=np.ones(n)), index=times)
    return bars, event, f


def box(e=42, a=36, b=39, direction="long", conf=.5, identity="x", w=18):
    return dict(symbol="TEST", structural_pass=True, core_start_i=a,
                core_end_i=b, window_end_i=e, confidence=conf, window_len=w,
                core_length_bars=b-a+1, confirmation_bars=e-b,
                direction=direction, detection_id=identity,
                available_at=pd.Timestamp("2020-01-01", tz="UTC")+
                pd.Timedelta(minutes=15*(e+1)))


def decide(boxes, f=None):
    bars, event, features = fixture()
    return decisions_for(pd.DataFrame([event]), pd.DataFrame(boxes), bars,
                         features if f is None else f).iloc[0]


def test_core_can_precede_arrow_and_entry_is_later_next_open():
    r = decide([box()])
    assert r.status == "confirmed" and r.delay_bars == 2
    assert r.baseline_next_open == 104.1 and r.confirmed_next_open == 104.3
    assert r.overlap_bars == 4 and r.core_end_from_arrow_bars == -1
    assert r.displacement_bp == pytest.approx((104.3/104.1-1)*10000)


def test_earliest_confirmation_wins_not_highest_confidence():
    r = decide([box(e=43, conf=.99, identity="later"), box(conf=.26)])
    assert r.model_detection_id == "x"


def test_opposite_box_does_not_prevent_later_same_direction():
    r = decide([box(e=41, direction="short", identity="opposite"), box()])
    assert r.delay_bars == 2
    assert r.can_long and r.can_short


@pytest.mark.parametrize("value", [0, -1, float("nan")])
def test_invalidation_on_confirmation_candle_wins(value):
    _, _, f = fixture()
    f.iloc[42, 0] = value
    assert decide([box()], f).status == "invalidated"


def test_recovery_cannot_revive_invalidated_candidate():
    _, _, f = fixture()
    f.iloc[41, 0] = 0
    assert decide([box()], f).status == "invalidated"


@pytest.mark.parametrize("a,b,e", [(37,39,42), (34,39,42), (36,39,40), (36,39,49)])
def test_illegal_core_or_post_rejected(a, b, e):
    assert decide([box(a=a, b=b, e=e)]).status == "expired"


def test_wait_limit_inclusive_but_next_endpoint_excluded():
    assert decide([box(a=37, b=40, e=49)]).status == "confirmed"
    assert decide([box(a=38, b=41, e=50)]).status == "expired"


def test_actual_bar_overlap_not_boundary_touch():
    assert decide([box(a=40, b=43, e=45)]).overlap_bars == 1
    assert decide([box(a=41, b=44, e=46)]).status == "expired"


def test_tie_break_deterministic_and_once_per_candidate():
    r = decide([box(identity="z", w=19), box(identity="a", w=18)])
    assert r.model_detection_id == "a"


def test_future_md_changes_do_not_rewrite_earliest_confirmation():
    _, _, f = fixture()
    f.iloc[43:, 0] = -999
    assert decide([box()], f).status == "confirmed"


def test_incomplete_followup_is_visible_censoring():
    bars, event, f = fixture()
    event["complete_followup"] = False
    r = decisions_for(pd.DataFrame([event]), pd.DataFrame([box()]), bars, f).iloc[0]
    assert r.status == "censored_end" and r.confirmed_next_open is None


def test_no_detection_expires_and_cross_symbol_rejected():
    assert decide([]).status == "expired"
    q = box()
    q["symbol"] = "OTHER"
    assert decide([q]).status == "expired"


def test_prefix_render_has_no_future_pixel_access():
    from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
    n = 400
    times = pd.date_range("2020-01-01", periods=n, freq="15min", tz="UTC")
    c = 100+np.sin(np.arange(n)/5)
    raw = pd.DataFrame(dict(open_time=times, open=c, high=c+1,
                            low=c-1, close=c+.1, volume=np.ones(n)))
    enriched = add_candidate_features(raw)
    original = prepare_window(enriched, 350, 19)[0]
    changed = raw.copy()
    changed.loc[351:, ["open", "high", "low", "close"]] *= 100
    perturbed = prepare_window(add_candidate_features(changed), 350, 19)[0]
    prefix = prepare_window(add_candidate_features(raw.iloc[:351].copy()), 350, 19)[0]
    assert np.array_equal(original, perturbed)
    assert np.array_equal(original, prefix)


def test_pool_direction_is_not_used_in_original_md_validity():
    _, event, f = fixture()
    q = eligible_pool(SimpleNamespace(**event), pd.DataFrame([box(direction="short")]),
                      f.md.to_numpy())
    assert len(q) == 1  # The null permutes association, not md invalidation.


def test_inference_adapter_with_synthetic_prediction_batches():
    from yoyo.evaluation.imacd_yolo_confirmation import infer
    n = 400
    times = pd.date_range("2020-01-01", periods=n, freq="15min", tz="UTC")
    c = 100+np.sin(np.arange(n)/5)
    raw = pd.DataFrame(dict(open=c, high=c+1, low=c-1, close=c+.1,
                            volume=np.ones(n)), index=times)

    class Array:
        def __init__(self, a): self.a = np.asarray(a)
        def cpu(self): return self
        def numpy(self): return self.a

    class Boxes:
        xywhn = Array([[.60, .5, .18, .5]])
        cls = Array([0])
        conf = Array([.6])
        def __len__(self): return 1

    class Model:
        def predict(self, **kw):
            assert kw["imgsz"] == 1280 and kw["conf"] == .25
            assert kw["rect"] and not kw["half"] and kw["max_det"] == 300
            return [SimpleNamespace(boxes=Boxes()) for _ in kw["source"]]

    events = pd.DataFrame([dict(signal_i=350, complete_followup=True)])
    proposals, stats = infer(raw, events, "TEST", Model(), "cpu")
    assert len(proposals) == 20 and stats["windows_scored"] == 20
    assert proposals.detection_id.nunique() == 20
    assert proposals.window_end_i.min() == 350
    assert proposals.window_end_i.max() == 350+MAX_WAIT
    assert proposals.available_at.max() == times[360]

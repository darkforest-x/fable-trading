"""Synthetic tests of frozen formation memory and complete-bar clocks."""
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from yoyo.evaluation.mainstream_features import enrich, closed_positions, add_context, candidate_events


def test_closed_context_does_not_see_partial_four_hour_and_units_match():
    source = pd.date_range("2025-01-01", periods=3, freq="4h", tz="UTC")
    target = pd.date_range("2025-01-01", periods=9, freq="h", tz="UTC")
    expected = [-1, -1, -1, 0, 0, 0, 0, 1, 1]
    assert closed_positions(source, 240, target, 60).tolist() == expected
    assert closed_positions(source.as_unit("ms"), 240, target, 60).tolist() == expected
    assert closed_positions(source, 240, target.as_unit("us"), 60).tolist() == expected
    assert (closed_positions(source[:0], 240, target, 60) == -1).all()


def test_enriched_features_prefix_and_future_invariance():
    x = np.arange(720)
    c = 100 + np.sin(x/8)*.2
    bars = pd.DataFrame(dict(open=c, high=c+1, low=c-1, close=c, volume=10+x%19),
                        index=pd.date_range("2024-01-01", periods=len(x), freq="h", tz="UTC"))
    bars.iloc[580, :4] = [100, 141, 99, 140]
    full = enrich(bars)
    for n in (352, 580, 581, 640):
        assert_frame_equal(full.iloc[:n], enrich(bars.iloc[:n]), check_exact=True)
    bars.iloc[600:, :4] *= 7
    assert_frame_equal(full.iloc[:600], enrich(bars).iloc[:600], check_exact=True)
    assert full.momentum_memory.iloc[580]


def fixture(n=570):
    return pd.DataFrame(dict(release_side=0, md=1., close=100., ready=True,
        context_ready=True, higher_permission=False, release_zone_low=90., relative_volume=3.,
        dense_recent=True, above_all6=True, momentum_memory=True, momentum10=95.,
        lower_active=True, lower_age_minutes=30.),
        index=pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC"))


def test_wait_is_actual_later_decision_and_memory_does_not_require_current_strength():
    f = fixture()
    f.iloc[550, f.columns.get_loc("release_side")] = 1
    f.iloc[550, f.columns.get_loc("momentum10")] = 55
    f.iloc[555:, f.columns.get_loc("higher_permission")] = True
    rows = candidate_events(f, 540, 569, 60)
    assert dict(arm="sequence_wait", anchor_i=550, decision_i=555) in rows
    assert dict(arm="sequence", anchor_i=550, decision_i=550) in rows
    assert not any(r["arm"] in ("momentum_now", "strict_same_time") for r in rows)
    assert candidate_events(f, 551, 569, 60) == []
    before = candidate_events(f, 540, 554, 60)
    assert not any(r["arm"].endswith("wait") for r in before)


@pytest.mark.parametrize("column,value", [("md", 0), ("close", 89), ("release_side", -1)])
def test_wait_cancelled_by_original_failure_or_new_release(column, value):
    f = fixture()
    f.iloc[550, f.columns.get_loc("release_side")] = 1
    f.iloc[553, f.columns.get_loc(column)] = value
    f.iloc[555:, f.columns.get_loc("higher_permission")] = True
    rows = candidate_events(f, 540, 569, 60)
    assert not any(r["arm"].endswith("wait") for r in rows)


def test_missing_context_diagnostics_do_not_borrow_future():
    base = fixture(8)
    lower = pd.DataFrame(dict(ready=True, active_long=True, active_start_i=0, active_inside_count=2),
        index=pd.date_range("2025-01-01", periods=32, freq="15min", tz="UTC"))
    higher = pd.DataFrame(dict(ready=True, md=3., sb=2.),
        index=pd.date_range("2025-01-01", periods=2, freq="4h", tz="UTC"))
    f = add_context(base, lower, higher, 60)
    assert f.higher_md.iloc[:3].isna().all()
    assert not f.higher_permission.iloc[:3].any()
    assert f.higher_source_close.iloc[3] == pd.Timestamp("2025-01-01T04:00Z")


def test_release_candle_below_original_box_is_not_active_and_cannot_start_wait():
    bars = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=10.),
        index=pd.date_range("2024-01-01", periods=600, freq="h", tz="UTC"))
    bars.iloc[580, :4] = [100, 400, 90, 90]
    f = enrich(bars)
    assert f.release_side.iloc[580] == 1
    assert not f.active_long.iloc[580]
    synthetic = fixture()
    synthetic.iloc[550, synthetic.columns.get_loc("release_side")] = 1
    synthetic.iloc[550, synthetic.columns.get_loc("close")] = 89
    synthetic.iloc[551:, synthetic.columns.get_loc("higher_permission")] = True
    assert not any(r["arm"].endswith("wait") for r in candidate_events(synthetic, 540, 569, 60))

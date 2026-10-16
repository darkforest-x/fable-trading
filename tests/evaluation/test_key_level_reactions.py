"""Causal formation, touch ordering, gap rejection and response arithmetic."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import key_level_reactions as study


def candles(ts, price=100):
    price = float(price)
    return pd.DataFrame(dict(ts=ts, open=price, high=price + 1,
                             low=price - 1, close=price, volume=10.))


def test_complete_bucket_only_and_missing_not_interpolated():
    raw = candles(np.arange(30) * study.MINUTE)
    result, missing = study.aggregate_15m(raw.drop(index=3))
    assert result.index.tolist() == [study.STEP]
    assert missing == 1
    assert result.volume.iloc[0] == 150


def test_profile_volume_scale_invariance_and_analytic_uniform_area():
    frame = pd.DataFrame([dict(open=100, high=110, low=90, close=100, volume=800)])
    result = study.volume_profile(frame)
    assert result == pytest.approx(dict(poc=90.125, val=90, vah=104))
    frame.volume *= .01
    assert study.volume_profile(frame) == pytest.approx(result)


def test_day_boundaries_handle_both_dst_transitions():
    for day, hours in [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)]:
        assert study.midnight(day + timedelta(days=1), "America/New_York") - study.midnight(day, "America/New_York") == hours * 3600000


def test_levels_activate_after_full_sessions_and_are_future_invariant():
    start = study.midnight(date(2026, 10, 5), "America/New_York")
    raw = candles(np.arange(start, start + 8 * 86400000, study.MINUTE))
    bars, _ = study.aggregate_15m(raw)
    levels, _ = study.make_levels(bars, "America/New_York")
    monday = levels.loc[levels.level_id.eq("monday_high|2026-10-05")].iloc[0]
    assert monday.active_ms == start + 86400000
    assert monday.price == 101
    assert monday.expires_ms == start + 7 * 86400000
    changed = bars.copy()
    changed.loc[changed.index >= monday.active_ms, "high"] = 10000
    after, _ = study.make_levels(changed, "America/New_York")
    assert after.set_index("level_id").loc[monday.level_id, "price"] == monday.price
    prior = levels.loc[levels.level_id.eq("prior_week_high|2026-10-05")].iloc[0]
    assert prior.active_ms == start + 7 * 86400000
    partial, _ = study.make_levels(bars.iloc[:90], "America/New_York")
    assert partial.empty


def level(price=99, active=60000, family="monday_low"):
    return pd.DataFrame([dict(level_id=family + "|test", family=family, price=price,
        origin="test", active_ms=active, expires_ms=600000, first_day_end_ms=180000)])


def test_first_contact_direction_uses_previous_close_not_contact_close():
    raw = candles(np.arange(10) * study.MINUTE)
    raw.loc[1, "close"] = 98
    found, _ = study.first_contacts(raw, level(), 0, 600000, "UTC")
    assert len(found) == 1
    assert found.side.iloc[0] == 1
    assert found.touch_index.iloc[0] == 1
    assert found.entry_index.iloc[0] == 2
    short, _ = study.first_contacts(raw, level(101, family="monday_high"), 0, 600000, "UTC")
    assert short.side.iloc[0] == -1


def test_missing_before_touch_invalidates_untouched_claim():
    raw = candles(np.arange(10) * study.MINUTE)
    raw.loc[4, "low"] = 95
    raw = raw.drop(index=2).reset_index(drop=True)
    events, status = study.first_contacts(raw, level(95), 0, 600000, "UTC")
    assert events.empty
    assert status.status.iloc[0] == "missing_before_touch"


def test_poc_aliases_are_one_event_and_old_untouched_poc_is_npoc():
    raw = candles(np.arange(10) * study.MINUTE)
    raw.loc[4, "low"] = 95
    events, _ = study.first_contacts(raw, level(95, family="poc"), 0, 600000, "UTC")
    assert len(events) == 1
    assert events.category.iloc[0] == "npoc"
    raw.loc[1, "low"] = 95
    events, _ = study.first_contacts(raw, level(95, family="poc"), 0, 600000, "UTC")
    assert events.category.iloc[0] == "yesterday_poc"


def test_response_excludes_contact_minute_extremes_and_checks_gaps():
    raw = candles(np.arange(10) * study.MINUTE)
    raw.loc[0, ["high", "low"]] = [1000, 1]
    raw.loc[3, "close"] = 100.5
    paths = study.forward_paths(raw, [3])[3]
    gross, mfe, mae = study.response(paths, 1, 1, 100)
    assert (gross, mfe, mae) == pytest.approx((50, 100, 100))
    short = study.response(paths, 1, -1, 100)
    assert short == pytest.approx((-50, 100, 100))
    broken = study.forward_paths(raw.drop(index=2).reset_index(drop=True), [3])[3]
    assert not broken["valid"][1]


def test_volatility_features_do_not_use_current_or_future_candles():
    ts = np.arange(96 * 12) * study.STEP
    bars = candles(ts).set_index("ts")
    bars["close"] = 100 * np.exp(np.sin(np.arange(len(bars)) / 11) * .01)
    cutoff = 96 * 10
    query = np.array([ts[cutoff], ts[cutoff] + study.MINUTE])
    expected = study.causal_features(bars, query)
    bars.loc[ts[cutoff]:, "close"] *= 100
    actual = study.causal_features(bars, query)
    for left, right in zip(expected, actual):
        np.testing.assert_equal(left, right)


def test_cluster_test_uses_weeks_and_no_model_probability():
    frame = pd.DataFrame(dict(week=["a", "a", "b", "b"], excess_bp=[1., 1., 1., 1.]))
    stats = study.cluster_stats(frame, 42, 999)
    assert stats["matched_weeks"] == 2
    assert stats["p"] > .4
    assert stats["ci_low"] == stats["ci_high"] == 1

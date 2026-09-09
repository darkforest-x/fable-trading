"""Observation time, episode invalidation and no retrospective confirmation."""
import pandas as pd
import pytest

from yoyo.evaluation.super_trend_candidate import active_lower_episode, attach_model_history, evidence_at, replay


def fixtures():
    times = pd.date_range("2026-08-31", periods=8, freq="1h", tz="UTC")
    one = pd.DataFrame({"release_side": [0, 1, 0, 0, 0, 0, 0, 0], "close": 10.0,
        "md": 1.0, "sb": .5, "release_zone_low": 9., "dense_recent": True,
        "relative_volume_median20": 3., "relative_volume_mean20": 2.,
        "momentum10": [100., 59., 60., 60., 60., 60., 60., 60.],
        "close_above_all6": True, "focus_start_i": 0, "near_zero_bars": 1}, index=times)
    low_times = pd.date_range("2026-08-30T20:00Z", periods=48, freq="15min")
    low = pd.DataFrame({"release_side": 0, "close": 10., "md": 1.,
                       "release_zone_low": 9., "release_zone_high": 9.5,
                       "momentum10": 100., "relative_volume_median20": 5.}, index=low_times)
    low.loc[pd.Timestamp("2026-08-31T00:00Z"), "release_side"] = 1
    high_times = pd.date_range("2026-08-30T20:00Z", periods=3, freq="4h")
    high = pd.DataFrame({"md": [.2, 1., 1.], "sb": [.3, .4, .4], "close": 12.,
                         "golden_cross": [False, True, False], "close_above_all6": True}, index=high_times)
    return one, low, high


def test_later_high_timeframe_cross_never_changes_start_snapshot():
    one, low, high = fixtures()
    now = evidence_at(one, low, high, one.index[1])
    assert now["higher_bullish_now"] is False
    assert now["strict_research_gate"] is False
    assert now["momentum_strong_observed_during_setup"] == [one.index[1]]
    changed = high.copy()
    changed.loc[changed.index >= pd.Timestamp("2026-08-31T00:00Z"), "md"] = 999
    assert evidence_at(one, low, changed, one.index[1]) == now
    timeline = replay(one, low, high, one.index[0])
    upgrade = next(x for x in timeline if x["type"] == "4h_cross_upgrade")
    assert upgrade["observed_at"] == "2026-08-31T04:00:00+00:00"
    assert upgrade["hours_after_start"] == 2


def test_lower_episode_is_not_expired_by_clock_but_is_invalidated_by_zero():
    _, low, _ = fixtures()
    now = pd.Timestamp("2026-08-31T02:00Z")
    assert active_lower_episode(low, now)["age_minutes"] == 105
    low.loc[pd.Timestamp("2026-08-31T01:15Z"), "md"] = 0
    assert active_lower_episode(low, now)["state"] == "none"


def test_prefix_replay_preserves_every_existing_observation():
    one, low, high = fixtures()
    full = replay(one, low, high, one.index[0])
    part = replay(one.iloc[:3], low.iloc[:28], high.iloc[:1], one.index[0])
    assert part == [x for x in full if pd.Timestamp(x["observed_at"]) <= pd.Timestamp("2026-08-31T03:00Z")]


def test_absolute_feature_row_numbers_do_not_drop_setup_after_slicing():
    one, low, high = fixtures()
    one["focus_start_i"] = 9621
    result = evidence_at(one, low, high, one.index[1])
    assert result["momentum_strong_observed_during_setup"] == [one.index[1]]
    with pytest.raises(ValueError, match="formation history"):
        evidence_at(one.iloc[1:], low, high, one.index[1])


def test_model_evidence_uses_observation_not_core_left_edge():
    one, low, high = fixtures()
    events = replay(one, low, high, one.index[0])
    proposal = {"side": "long", "structural_pass": True, "core_start_bj": "2026-08-31T00:00Z",
                "core_end_bj": "2026-08-31T00:12Z", "confidence": .7}
    future = [{"timeframe": "3m", "available_at_bj": "2026-08-31T02:03Z", "proposals": [proposal]}]
    original = attach_model_history(events, future, one)
    assert original[0]["prior_model_structure_groups"] == []
    past = [{**future[0], "available_at_bj": "2026-08-31T00:21Z"}]
    known = attach_model_history(events, past + future, one)
    assert len(known[0]["prior_model_structure_groups"]) == 1
    assert known[0]["prior_model_structure_groups"][0]["last_observed_at"] == "2026-08-31T00:21:00+00:00"

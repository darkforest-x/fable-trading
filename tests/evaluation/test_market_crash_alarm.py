"""Causal features and label clocks for the frozen warning comparison."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation.market_crash_alarm import (
    OUT, alarms, eligible_events, event_flags, group_events, make_scores,
)


def config():
    return json.loads((OUT / 'config.json').read_text())


def test_future_prices_cannot_change_prior_scores_or_alarm_times():
    cfg = config()
    rng = np.random.default_rng(42)
    close = np.exp(np.cumsum(rng.normal(0, .001, (1800, 6)), axis=0)) * 100
    scores, vol, valid = make_scores(close, cfg)
    changed = close.copy()
    changed[1700:] *= np.linspace(.01, 3, 100)[:, None]
    after, after_vol, after_valid = make_scores(changed, cfg)
    np.testing.assert_allclose(vol[:1700], after_vol[:1700], equal_nan=True)
    np.testing.assert_array_equal(valid[:1700], after_valid[:1700])
    for method in scores:
        np.testing.assert_allclose(scores[method][:1700], after[method][:1700], equal_nan=True)
        before_alarms = alarms(scores[method], 1, valid, 15)
        after_alarms = alarms(after[method], 1, after_valid, 15)
        np.testing.assert_array_equal(before_alarms[before_alarms < 1700], after_alarms[after_alarms < 1700])


def test_current_return_does_not_enter_its_volatility_denominator():
    cfg = config()
    close = np.exp(np.arange(1600)[:, None] * .001) * np.ones((1, 6))
    _, vol, _ = make_scores(close, cfg)
    changed = close.copy()
    changed[1500] *= .5
    _, new_vol, _ = make_scores(changed, cfg)
    assert vol[1500] == new_vol[1500]
    assert new_vol[1501] > vol[1501]


def test_event_uses_current_low_not_old_extreme_inside_a_rolling_window():
    cfg = config()
    close = np.full((100, 6), 100.)
    low = close.copy()
    low[10] = 95
    flag, median, valid = event_flags(close, low, cfg)
    assert np.flatnonzero(flag).tolist() == [10]
    assert valid[10]
    events = group_events(flag, median, close, np.arange(100) * 60000, cfg)
    assert events.position.tolist() == [10]
    assert events.first_open.iloc[0] == pd.Timestamp('1970-01-01T00:10:00Z')
    assert events.known_at.iloc[0] == pd.Timestamp('1970-01-01T00:11:00Z')


def test_event_membership_cannot_leak_across_period_end():
    events = pd.DataFrame({'position': [10, 20, 21]})
    mask = np.zeros(30, bool)
    mask[:20] = True  # Decision rows 0..19; event opens through minute20.
    selected, censored = eligible_events(events, mask, mask)
    assert selected.position.tolist() == [10, 20]
    assert censored == 0
    gap = mask.copy()
    gap[5] = False
    selected, censored = eligible_events(events, mask, gap)
    assert selected.position.tolist() == [20]
    assert censored == 1


def test_missing_bar_blocks_event_and_feature_windows_without_compression():
    cfg = config()
    close = np.full((1700, 6), 100.)
    low = close.copy()
    close[1500, 2] = np.nan
    low[1501] = 90
    flag, _, valid = event_flags(close, low, cfg)
    assert not valid[1501]
    assert not flag[1501]
    _, _, feature_valid = make_scores(close, cfg)
    assert not feature_valid[1501:].any()

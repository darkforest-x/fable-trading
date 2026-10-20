"""Stop-bar ambiguity, side symmetry and right-edge contracts for recovery labels."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
from yoyo.evaluation.pepe_range_recovery import counts, excursions, observe


def record(**kwargs):
    args = dict(entry_i=0, exit_i=2, side=1, entry=100., risk=1., reason=1,
                end_i=5, pair_id=0, population="signal", segment=0)
    args.update(kwargs)
    return SimpleNamespace(**args)


def candles():
    return np.array([[100,200,50,100], [100,103,99.5,102], [102,110,98,99],
                     [99,99.5,98.5,99], [99,101,98.8,100.5], [100.5,105,100,104]], dtype=float)


def test_holding_excludes_entry_bar_and_ambiguous_stop_bar():
    assert excursions(candles(), record()) == (3., 10.)


def test_non_stop_close_exit_includes_exit_bar():
    assert excursions(candles(), record(reason=3)) == (10., 10.)


def test_recovery_excludes_stop_bar_and_freezes_original_risk():
    x = observe(candles(), record(), 15, [0,1,3,5,10])
    assert x["complete"] and x["post_max_r"] == 5
    assert not x["touch_10r"]  # 10R was only in the ambiguous stop bar.
    assert x["first_touch_0r_minutes"] == 10
    assert x["touch_1r"] and x["close_1r"]  # Last close is +4R.
    assert x["adverse_bound_before_entry_reclaim_r"] == 2


def test_censored_horizon_does_not_consume_following_segment():
    x = observe(candles(), record(end_i=4), 15, [0,1])
    assert not x["complete"] and "touch_0r" not in x


def test_touch_is_distinct_from_close_reclaim():
    bars = candles()
    bars[4] = [99, 100, 98.8, 99.9]
    bars[5] = [99.9, 99.95, 99, 99.8]
    x = observe(bars, record(), 15, [0,1])
    assert x["touch_0r"] and not x["close_0r"]


def test_long_short_mirror_has_identical_r_labels():
    bars = candles()
    mirror = 200-bars
    mirror[:, [1,2]] = mirror[:, [2,1]]
    a = observe(bars, record(), 15, [0,1,3,5,10])
    b = observe(mirror, record(side=-1), 15, [0,1,3,5,10])
    assert a == b
    assert excursions(bars, record()) == excursions(mirror, record(side=-1))


def test_decimal_tick_touch_is_retained():
    bars = candles()*1e-9
    r = record(entry=100e-9, risk=1e-9)
    assert observe(bars, r, 15, [5])["touch_5r"]


def test_missing_censored_labels_are_not_recoveries():
    assert counts(pd.DataFrame({"touch": [True, False, np.nan]}), ["touch"]) == {"touch": 1}

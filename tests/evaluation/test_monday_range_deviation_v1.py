"""Unit checks for the Monday-range deviation replay (signal timing and the partial-exit path)."""
import numpy as np
import pytest

from yoyo.evaluation import monday_range_deviation_v1 as m


def test_signal_long_arms_on_break_and_fires_on_close_back_inside():
    lo = np.array([101, 99, 98, 99.5, 100.5])
    hi = lo + 1
    cl = np.array([101.5, 99.5, 98.5, 100.2, 101.0])
    sig, stop = m.signal(lo, hi, cl, level=100.0, depth=0.0, side=1)
    assert sig == 3 and stop == 98.0  # first close > 100 after the break; lowest low since arming


def test_signal_depth_requires_the_extension():
    lo = np.array([99.5, 99.2, 100.5])
    cl = np.array([99.8, 100.4, 101.0])
    assert m.signal(lo, lo + 1, cl, level=100.0, depth=1.0, side=1) is None
    assert m.signal(lo, lo + 1, cl, level=100.0, depth=0.0, side=1) == (1, 99.2)


def test_signal_short_mirrors():
    hi = np.array([99, 101, 102, 100.5])
    cl = np.array([98.5, 100.5, 101.0, 99.5])
    assert m.signal(hi - 1, hi, cl, level=100.0, depth=0.0, side=-1) == (3, 102.0)


def path(rows):
    a = np.array(rows, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]  # high, low, open, close


def test_stop_first_in_the_same_minute():
    h, l, o, c = path([[112, 94, 100, 100]])
    r, how = m.exit_r(h, l, o, c, 100, 95, 110, 120, 1, "primary")
    assert how == "stop" and r == pytest.approx(-1.0)


def test_tp1_then_tp2_and_variants():
    h, l, o, c = path([[105, 99, 100, 104], [111, 103, 104, 110], [121, 109, 110, 120]])
    r, how = m.exit_r(h, l, o, c, 100, 95, 110, 120, 1, "primary")
    assert how == "tp2" and r == pytest.approx(0.8 * 2 + 0.2 * 4)
    r, how = m.exit_r(h, l, o, c, 100, 95, 110, 120, 1, "tp1_only")
    assert how == "tp1" and r == pytest.approx(2.0)


def test_rest_stop_original_or_break_even():
    h, l, o, c = path([[111, 99, 100, 110], [110, 94, 108, 94]])
    r, how = m.exit_r(h, l, o, c, 100, 95, 110, 120, 1, "primary")
    assert how == "tp1_stop" and r == pytest.approx(0.8 * 2 - 0.2)
    r, how = m.exit_r(h, l, o, c, 100, 95, 110, 120, 1, "be_after_tp1")
    assert how == "tp1_stop" and r == pytest.approx(0.8 * 2)


def test_gap_through_stop_fills_at_open_and_time_exit():
    h, l, o, c = path([[101, 99, 100, 100], [93, 90, 92, 91]])
    r, how = m.exit_r(h, l, o, c, 100, 95, 110, 120, 1, "primary")
    assert how == "stop" and r == pytest.approx(-8 / 5)
    h, l, o, c = path([[101, 99, 100, 100], [102, 100, 100, 102]])
    r, how = m.exit_r(h, l, o, c, 100, 95, 110, 120, 1, "primary")
    assert how == "time" and r == pytest.approx(0.4)

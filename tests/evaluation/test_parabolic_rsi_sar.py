"""Published SAR semantics: na warmup, flip direction, value tested, causality."""
import numpy as np
import pytest

from yoyo.evaluation.parabolic_rsi_sar import diamonds, pine_sar, within


def series(n=200, seed=7):
    rng = np.random.default_rng(seed)
    return np.r_[np.full(14, np.nan), np.clip(50 + np.cumsum(rng.normal(0, 4, n)), 1, 99)]


def test_warmup_stays_undefined_instead_of_seeding_a_substitute():
    src = series()
    sar, below = pine_sar(src)
    # The published init branch runs while bar_index <= len+2 and reads src[1];
    # replacing na with a number there would start the recursion from a price
    # the chart never had.
    assert np.isnan(sar[:14]).all() and np.isfinite(sar[16:]).all()
    assert not below[:14].any()


def test_is_below_is_the_bullish_state_and_trails_the_source():
    rising = np.arange(40, dtype=float) + 10
    sar, below = pine_sar(rising, init_bars=3)
    assert below[5:].all()
    # A bullish SAR sits under the source, clamped by the two prior lows.
    assert (sar[5:] <= rising[5:] - 1 + 1e-9).all()
    falling = 90 - np.arange(40, dtype=float)
    sar_dn, below_dn = pine_sar(falling, init_bars=3)
    assert not below_dn[5:].any() and (sar_dn[5:] >= falling[5:] + 1 - 1e-9).all()


def test_strong_diamond_tests_the_sar_value_not_the_rsi_value():
    sar = np.array([np.nan, 25., 80., 28.])
    below = np.array([False, True, False, True])
    got = diamonds(sar, below, lower=30., upper=70.)
    assert got["flip_up"].tolist() == [False, True, False, True]
    assert got["strong_up"].tolist() == [False, True, False, True]
    # A flip whose SAR is at 80 is a down flip in the overbought zone.
    assert got["strong_dn"].tolist() == [False, False, True, False]
    # Nudging the SAR just outside the threshold removes the strong event while
    # the plain flip survives, which is the difference the report relies on.
    quiet = diamonds(np.array([np.nan, 31., 69., 28.]), below, lower=30., upper=70.)
    assert not quiet["strong_up"][1] and quiet["flip_up"][1]
    assert not quiet["strong_dn"][2] and quiet["flip_dn"][2]


def test_sar_and_window_are_prefix_causal():
    src = series()
    changed = src.copy()
    changed[150:] += 25
    sar_a, below_a = pine_sar(src)
    sar_b, below_b = pine_sar(changed)
    np.testing.assert_allclose(sar_a[:150], sar_b[:150], equal_nan=True)
    np.testing.assert_array_equal(below_a[:150], below_b[:150])
    event = np.zeros(10, dtype=bool)
    event[4] = True
    np.testing.assert_array_equal(within(event, 1), event)
    assert within(event, 3).tolist() == [False]*4 + [True]*3 + [False]*3


def test_inputs_are_validated():
    with pytest.raises(ValueError):
        pine_sar(np.zeros((2, 2)))
    with pytest.raises(ValueError):
        pine_sar(np.zeros(5), start=0.3, maximum=0.2)
    with pytest.raises(ValueError):
        diamonds(np.zeros(3), np.zeros(4, dtype=bool))
    with pytest.raises(ValueError):
        within(np.zeros(3, dtype=bool), 0)

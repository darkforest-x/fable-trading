"""Arithmetic, gate semantics, immutable exits and prefix causality."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.ma_stoch_rsi_filter import add_filter, zone_admission
from yoyo.evaluation.spike_v6_bb_squeeze import _rsi_wilder
from yoyo.evaluation.ma_stoch_exit_v2 import prepare, simulate
from test_ma_stoch_exit_v2 import fixture


def test_wilder_seed_and_next_recursive_value():
    # Deltas +1,-2,+3 => gain 4/3, loss 2/3, RSI 66 2/3.
    # Next -1 => gain 8/9, loss 7/9, RSI 53 1/3.
    r = _rsi_wilder(pd.Series([100., 101., 99., 102., 101.]), 3)
    assert r.iloc[:3].isna().all()
    np.testing.assert_allclose(r.iloc[3:], [200/3, 160/3], atol=1e-12)


def test_one_sided_and_flat_wilder_inputs():
    assert _rsi_wilder(pd.Series(np.arange(20.)), 14).iloc[14:].eq(100).all()
    assert _rsi_wilder(pd.Series(np.arange(20.)[::-1]), 14).iloc[14:].eq(0).all()
    assert _rsi_wilder(pd.Series(np.ones(20)), 14).isna().all()


def test_filter_is_same_bar_intersection_not_memory_or_new_signal():
    a = [1, 1, 1, -1, -1, -1, 0, 1, -1]
    r = [29, 30, 31, 71, 70, 69, 15, np.nan, np.nan]
    assert zone_admission(a, r).tolist() == [1, 0, 0, -1, 0, 0, 0, 0, 0]
    with pytest.raises(ValueError): zone_admission([1], [29, 30])


def test_filter_and_ma_stoch_features_are_prefix_causal():
    idx = pd.date_range('2025-12-20', periods=600, freq='5min', tz='UTC')
    p = 100 + np.sin(np.arange(600)/10)
    f = pd.DataFrame(dict(open=p, high=p+1, low=p-1, close=p+.1, volume=1), index=idx)
    original = prepare(f); saved = original['admission'].copy(); a = add_filter(original)
    changed = f.copy(); changed.iloc[500:, :4] += 50; b = add_filter(prepare(changed))
    for name in ('rsi', 'admission', 'arrow', 'atr', 'direction'):
        np.testing.assert_allclose(a[name][:500], b[name][:500], equal_nan=True)
    np.testing.assert_array_equal(original['admission'], saved)
    assert a['arrow'] is original['arrow'] and a['atr'] is original['atr']
    assert np.isin(a['admission'], [-1, 0, 1]).all()


def test_gate_decides_at_signal_close_and_leaves_open_trade_exits_alone():
    c, s, t = fixture(); c['arrow'][133] = -1; c['high'][135] = 113
    expected = simulate(c, s, t, 'wide_tp2_no_arrow')
    rs = np.full(len(c['index']), 50.); rs[130] = 29.
    filtered = dict(c, admission=zone_admission(c['admission'], rs))
    actual = simulate(filtered, s, t, 'wide_tp2_no_arrow')
    pd.testing.assert_frame_equal(expected['trades'], actual['trades'])
    assert actual['trades'].iloc[0].exit_reason == 'target'
    assert actual['trades'].iloc[0].entry_i == 131
    rs[130] = 31.; rs[131] = 29.
    blocked = simulate(dict(c, admission=zone_admission(c['admission'], rs)), s, t, 'wide_tp2_no_arrow')
    assert blocked['trades'].empty and blocked['open_positions'].empty

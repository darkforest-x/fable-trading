"""Causality and unit invariants for the offline BB slope study."""
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_v128_bb_slope as study


def frame(n=1000):
    rng = np.random.default_rng(7)
    c = 100 + np.cumsum(rng.normal(0, .1, n))
    return pd.DataFrame({'open': c, 'high': c+.2, 'low': c-.2, 'close': c,
                         'volume': 100., 'atr': .5}, index=pd.date_range('2024-01-01', periods=n, freq='15min', tz='UTC'))


def test_slope_matches_linear_regression_and_scaling():
    x = np.arange(40.)
    np.testing.assert_allclose(study.ols_slope(800 + .37*x, 12)[11:], .37, atol=1e-12)
    f = frame()
    a = study.slope_features(f, 15)
    b = study.slope_features(f.assign(close=f.close*100, atr=f.atr*100), 15)
    np.testing.assert_allclose(a['pre12_half'], b['pre12_half'], atol=1e-10, equal_nan=True)


def test_pre_signal_feature_does_not_see_signal_or_future():
    f = frame(); a = study.slope_features(f, 15)
    altered = f.copy(); altered.loc[altered.index[800]:, 'close'] *= 1.7
    b = study.slope_features(altered, 15)
    for name in ('pre12_basis', 'pre12_half', 'pre3_basis', 'pre24_half', 'episode12_half'):
        np.testing.assert_allclose(a[name][:801], b[name][:801], equal_nan=True)
    assert a['now12_basis'][800] != b['now12_basis'][800]


def test_direction_and_decomposition_are_exact():
    a = study.slope_features(frame(), 15)
    long = study.at_event(a, 900, 1); short = study.at_event(a, 900, -1)
    assert long['pre12_inward'] == short['pre12_inward']
    assert long['pre12_trend'] == -short['pre12_trend']
    assert long['pre12_opp'] == long['pre12_trend'] + long['pre12_inward']
    np.testing.assert_allclose(long['pre12_opp'], study.ols_slope(a['lower'], 12)[899]/.5, atol=1e-12)
    np.testing.assert_allclose(short['pre12_opp'], -study.ols_slope(a['upper'], 12)[899]/.5, atol=1e-12)


def test_gaps_restart_bb_and_endpoint_is_strictly_prior():
    f = frame().drop(frame().index[800])
    a = study.slope_features(f, 15)
    assert np.isnan(a['basis'][800:999]).all()
    valid = a['episode_endpoint'] >= 0
    assert (a['episode_endpoint'][valid] < np.flatnonzero(valid)).all()
    assert (np.flatnonzero(valid) - a['episode_endpoint'][valid] <= 10).all()


def test_timeframe_routes_keep_inherited_gates(monkeypatch):
    calls = []
    monkeypatch.setattr(study.parent, 'pine_facts', lambda *a: calls.append('15'))
    monkeypatch.setattr(study.low, 'pine_facts', lambda *a: calls.append('5'))
    monkeypatch.setattr(study.parent, '_generic_facts', lambda *a: calls.append('other'))
    for m in study.HTF:
        study.facts_for(None, None, 'ETH', .01, m)
    assert calls == ['other','other','5','15','other','other','other']

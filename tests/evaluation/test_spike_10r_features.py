from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_10r_features import FEATURE_COLUMNS, candidate_features


def _prepared(rows: int = 140):
    index = pd.date_range("2024-01-01", periods=rows, freq="h", tz="UTC")
    close = np.linspace(100.0, 240.0, rows)
    frame = pd.DataFrame({
        "open": close - .5, "high": close + 1, "low": close - 1, "close": close,
        "volume": np.linspace(100, 300, rows), "tr": np.full(rows, 2.), "atr": np.full(rows, 2.),
        "rv": np.linspace(1, 3, rows), "pastWidth": np.full(rows, 1.5),
        "ropeHigh": close - .25, "ropeLow": close - .75,
        "s20": close - .4, "e20": close - .3, "s60": close - .8, "e60": close - .7,
        "s120": close - 1.2, "e120": close - 1.1,
    }, index=index)
    return SimpleNamespace(frame=frame, gap=np.zeros(rows, dtype=bool),
                           context=SimpleNamespace(minutes=60), spec=SimpleNamespace(tick=.01))


def _candidate(i: int = 120):
    return pd.DataFrame({"event_key": [f"stream:{i}:1"], "local_i": [i], "signal_i": [1000 + i],
                         "side": [1], "reference_risk_fraction": [999.]})


def test_features_are_causal_and_keep_exactly_twenty_declared_fields():
    prepared = _prepared(); before = candidate_features(prepared, _candidate())
    changed = _prepared()
    changed.frame.loc[changed.frame.index[121:], ["open", "high", "low", "close", "volume", "atr"]] *= 100
    changed.frame.loc[changed.frame.index[:10], ["open", "high", "low", "close", "volume", "atr"]] *= .01
    after = candidate_features(changed, _candidate())
    assert list(before.columns[-20:]) == list(FEATURE_COLUMNS)
    assert before.columns.tolist().count("reference_risk_fraction") == 1
    pd.testing.assert_frame_equal(before, after)


def test_internal_gap_makes_lookback_unknown_but_edge_and_outside_gaps_do_not():
    edge = _prepared(); edge.gap[100] = True  # left edge of the candidate's prior-20 window
    edge_values = candidate_features(edge, _candidate()).iloc[0]
    assert np.isfinite(edge_values.breakout20_atr)
    internal = _prepared(); internal.gap[110] = True
    internal_values = candidate_features(internal, _candidate()).iloc[0]
    assert np.isnan(internal_values.breakout20_atr)
    assert np.isnan(internal_values.momentum20_atr)
    assert np.isfinite(internal_values.body_fraction)
    outside = _prepared(); outside.gap[5] = True
    outside_values = candidate_features(outside, _candidate()).iloc[0]
    assert np.isfinite(outside_values.breakout20_atr)


def test_missing_optional_volume_is_unknown_never_zero():
    prepared = _prepared(); prepared.frame = prepared.frame.drop(columns="volume")
    row = candidate_features(prepared, _candidate()).iloc[0]
    assert np.isnan(row.volume_trend20)
    assert np.isfinite(row.volume_ratio)


def test_reference_risk_checks_all_four_internal_edges_of_five_bars():
    prepared=_prepared()
    prepared.gap[117]=True
    row=candidate_features(prepared,_candidate(120)).iloc[0]
    assert np.isnan(row.reference_risk_fraction)


def test_volatility_ratio_uses_prior_twenty_over_prior_hundred_returns():
    prepared=_prepared(180)
    increments=np.r_[np.full(120,.001),np.linspace(-.025,.025,60)]
    prepared.frame['close']=100*np.exp(np.cumsum(increments))
    i=160
    actual=candidate_features(prepared,_candidate(i)).iloc[0].volatility_ratio20_100
    historical=np.diff(np.log(prepared.frame.close.iloc[i-101:i].to_numpy()))
    assert actual==pytest.approx(np.std(historical[-20:])/np.std(historical))
    prepared.frame.loc[prepared.frame.index[i:],'close']*=100
    assert candidate_features(prepared,_candidate(i)).iloc[0].volatility_ratio20_100==pytest.approx(actual)

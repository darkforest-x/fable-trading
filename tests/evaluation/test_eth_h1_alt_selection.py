import numpy as np
import pandas as pd

from yoyo.evaluation.eth_h1_alt_selection import hourly, features, outcomes, choose


def frame(n=1100):
    rng = np.random.default_rng(5)
    c = 100 * np.exp(rng.normal(0, .01, n).cumsum())
    return pd.DataFrame(dict(open=c-.1, high=c+1, low=c-1, close=c,
                             volume=rng.uniform(1, 10, n)),
                        index=pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC"))


def test_exact_sixteen_hours_excludes_signal_extremes():
    f = frame(25)
    f.iloc[0, f.columns.get_loc("high")] = 10000
    r = outcomes(f, 16, .002).iloc[0]
    assert np.isclose(r.net_bp, (f.close.iloc[16]/f.open.iloc[1]-1-.002)*1e4)
    assert np.isclose(r.mfe_bp, (f.high.iloc[1:17].max()/f.open.iloc[1]-1)*1e4)
    assert np.isnan(outcomes(f, 16, .002).net_bp.iloc[-1])


def test_complete_hour_and_gap_rejection():
    raw = frame(8).reset_index(drop=True)
    raw["ts"] = np.arange(8)*900000
    assert len(hourly(raw, 15)) == 2
    assert len(hourly(raw.drop(index=2), 15)) == 1
    f = frame(30)
    f.iloc[8] = np.nan
    assert np.isnan(outcomes(f, 16, .002).net_bp.iloc[0])


def test_feature_prefix_invariance():
    f = frame()
    pd.testing.assert_frame_equal(features(f, f).iloc[:900], features(f.iloc[:900], f.iloc[:900]))


def test_selection_never_takes_outcome_or_fills_missing_score():
    s = pd.Series({"A": 1., "B": 1., "C": np.nan, "D": 3.})
    e = pd.Series({"A": True, "B": True, "C": True, "D": False})
    assert choose(s, e, 3) == ["A", "B"]

"""Causal invariants for the gold research adapter; no market outcomes."""
import numpy as np
import pandas as pd
from yoyo.evaluation.xauusd_systems import aggregate_gold, features, attach_higher, candidate, CANDIDATES


def bars(n=650):
    rng = np.random.default_rng(81)
    close = 100 + np.cumsum(rng.normal(0, .3, n))
    b = pd.DataFrame({'open': close-.05, 'high': close+.2, 'low': close-.2, 'close': close},
                     index=pd.date_range('2018-01-01', periods=n, freq='h', tz='UTC'))
    b['time_close'] = b.index + pd.Timedelta(hours=1)
    return b


def test_gold_daily_preserves_closed_market_without_fake_bars():
    index = pd.date_range('2024-01-02 05:00', '2024-01-03 04:59', freq='min', tz='UTC')
    index = index[~((index.hour == 22))]
    b = pd.DataFrame({'open': 100., 'high': 101., 'low': 99., 'close': 100.}, index=index)
    g = aggregate_gold(b, 1440)
    assert len(g) == 1
    assert g.observed_minutes.iloc[0] == 1380
    assert g.time_close.iloc[0] == pd.Timestamp('2024-01-03 05:00Z')


def test_features_and_every_candidate_prefix_invariant():
    b = bars()
    a, z = features(b.iloc[:500]), features(b)
    pd.testing.assert_frame_equal(a, z.iloc[:500])
    for f in (a,z):
        f['h_known'] = True
        f['h_md'], f['h_sh'] = .2, .1
    for name in CANDIDATES:
        ea = candidate(b.iloc[:500], a, name)
        ez = candidate(b,z,name)
        for x,y in zip(ea,ez):
            np.testing.assert_array_equal(x,y[:500])


def test_high_source_closes_by_local_open_not_future_close():
    hb = bars(360)
    hf = features(hb)
    b = hb.iloc[342:345].copy()
    f = attach_higher(b, features(b), hb, hf)
    assert (f.h_source_close <= b.index).all()
    assert f.h_md.iloc[0] == hf.md.iloc[341]
    # The current high candle is not yet available, even at this bar's close.
    assert f.h_source_close.iloc[0] == hb.time_close.iloc[341]

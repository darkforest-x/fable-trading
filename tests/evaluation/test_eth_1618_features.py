"""Causal feature clocks and independent training changes, without model fits."""
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_1618_features import feature_series, event_features
from yoyo.evaluation.eth_1618_feature_models import training_rows, training_weights


def frames():
    ix = pd.date_range('2024-01-01', periods=2300, freq='5min', tz='UTC')
    rng = np.random.default_rng(61)
    result = []
    for scale in [2000, 40000]:
        c = scale*np.exp(np.cumsum(rng.normal(0,.001,len(ix))))
        o = np.r_[c[0], c[:-1]]
        result.append(pd.DataFrame(dict(open=o,high=np.maximum(o,c)*1.001,
            low=np.minimum(o,c)*.999,close=c,volume=rng.uniform(10,100,len(ix))), index=ix))
    return result


def test_future_and_touch_mutations_cannot_change_pre_touch_features():
    eth, btc = frames(); i = 2201
    events = pd.DataFrame(dict(submission_i=[i,i], submission_time=[str(eth.index[i])]*2,
                               side=[-1,1], entry=[eth.close.iloc[i]]*2))
    original = event_features(events, eth, btc)
    a, b = eth.copy(), btc.copy()
    a.iloc[i+1:] *= 10; b.iloc[i+1:] *= .1
    pd.testing.assert_frame_equal(original, event_features(events, a, b))
    pd.testing.assert_frame_equal(original, event_features(events, eth.iloc[:i+1], btc.iloc[:i+1]))
    for col in ['g_approach_efficiency_1h','g_approach_acceleration_15m','g_candle_volume_imbalance_1h',
                'g_btc_return_4h','g_eth_residual_4h']:
        assert abs(original[col].iloc[0]+original[col].iloc[1]) < 1e-12


def test_training_maturity_and_decay_preserve_within_week_equal_weights():
    d = pd.DataFrame(dict(symbol=['ETH']*5,event_id=list('abcde'),week=['2024-01-01','2025-01-06','2025-01-06','2025-06-23','2025-06-30'],
         week_start=pd.to_datetime(['2024-01-01','2025-01-06','2025-01-06','2025-06-23','2025-06-30'],utc=True),
         expiry_dt=pd.to_datetime(['2024-01-08','2025-01-13','2025-01-13','2025-06-30','2025-07-07'],utc=True),complete=[True]*5))
    start = pd.Timestamp('2025-07-01',tz='America/New_York')
    train = training_rows(d,start,'original_model')
    assert train.event_id.tolist() == list('abcd')
    assert training_rows(d,start,'window_52w').event_id.tolist() == list('bcd')
    base = training_weights(train,start,'original_model')
    total = pd.Series(base,index=train.index).groupby(train.week).sum()
    np.testing.assert_allclose(total,total.iloc[0])
    w = training_weights(train,start,'decay_26w')
    assert w[1] == w[2] and w[3] > w[1]+w[2] > w[0]
    assert abs(w.mean()-1)<1e-12

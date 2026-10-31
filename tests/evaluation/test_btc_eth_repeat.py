"""Decision clocks, NY DST, episode merging and fixed-stop geometry."""
import numpy as np
import pandas as pd
from yoyo.evaluation.btc_eth_repeat_data import base_features, bounds, candidates, rounded


def frame(days=14):
    index=pd.date_range('2024-01-01T00:05Z',periods=days*288,freq='5min')
    rng=np.random.default_rng(9)
    close=100+np.cumsum(rng.normal(0,.01,len(index)))
    return pd.DataFrame(dict(open=close,high=close+.1,low=close-.1,close=close,
                             volume=rng.uniform(1,10,len(index))),index=index)


def test_features_ignore_future_and_unfinished_resamples():
    f=frame()
    all_features=base_features(f)
    for i in [403,1193,2555,2999]:
        prefix=base_features(f.iloc[:i+1])
        pd.testing.assert_series_equal(prefix.iloc[-1],all_features.iloc[i])
    changed=f.copy()
    changed.iloc[1200:,:]=changed.iloc[1200:,:]*2
    pd.testing.assert_frame_equal(base_features(changed).iloc[:1200],all_features.iloc[:1200])


def test_week_expiry_tracks_new_york_dst():
    start,formed,end=bounds('2024-03-04')
    assert (formed-start).total_seconds()==86400
    assert (end-start).total_seconds()==167*3600
    start,_,end=bounds('2024-10-28')
    assert (end-start).total_seconds()==169*3600


def test_rounding_keeps_stop_outside_and_target_favorable():
    for side in (-1,1):
        entry=rounded(81684.7718,.1,side==-1)
        stop=rounded(entry*(1-side*.007),.1,side==-1)
        assert side*(entry-stop)>=entry*.007-1e-8
        assert abs(stop/.1-round(stop/.1))<1e-7


def test_adjacent_touch_bars_merge_then_rearm():
    f=frame(3)
    f[['open','close']]=100.
    f['high'],f['low']=101.,99.
    _,formed,_=bounds('2024-01-01')
    opens=f.index-pd.Timedelta(minutes=5)
    # Monday width 2 => upper 102.236. Adjacent 15m touches form one episode.
    for stamp in [formed,formed+pd.Timedelta(minutes=15),formed+pd.Timedelta(minutes=45)]:
        f.loc[opens==stamp,'high']=103.
    events,orders=candidates('ETH',f)
    upper=events[events.side.eq(-1)]
    assert list(upper.ordinal)==[1,2]
    assert list(pd.to_datetime(upper.touch_open,utc=True))==[formed,formed+pd.Timedelta(minutes=45)]
    assert (upper.submission_i==upper.touch_i-1).all()
    assert (orders.loc[orders.kind.eq('signal'),'stop']>102.236).all()

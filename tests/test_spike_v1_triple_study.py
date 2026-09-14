"""Causal event folding and matching invariants, no historical data."""
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v1_triple_study import dedup_events, control_events


def test_dedup_uses_earliest_entry_before_filter_and_never_outcome():
    common = dict(base_asset='BTC', entry_time='2024-01-01T04:00:00Z')
    rows = [dict(common, event_id='a', venue='okx', timeframe_min=60, net_r=100),
            dict(common, event_id='b', venue='binance', timeframe_min=240, net_r=-1),
            dict(common, event_id='c', venue='binance', timeframe_min=15, net_r=200)]
    out = dedup_events(pd.DataFrame(rows))
    assert list(out.loc[out.dedup_keep, 'event_id']) == ['b']
    rows[0]['entry_time'] = '2024-01-01T03:00:00Z'
    assert list(dedup_events(pd.DataFrame(rows)).query('dedup_keep').event_id) == ['a']


def test_controls_stay_same_entry_day_bucket_and_are_deterministic():
    index = pd.date_range('2024-01-01', periods=100, freq='1h', tz='UTC')
    bars = pd.DataFrame(dict(open=100., high=102., low=99., close=101., atr=1., rv=5., recentLow=99., ready=True), index=index)
    events = pd.DataFrame([dict(event_id='native', signal_bar_open=index[30], timeframe_min=60,
                                base_asset='BTC', venue='binance', symbol='BTCUSDT', dedup_keep=True)])
    a, audit = control_events(bars, events, .01)
    b, _ = control_events(bars, events, .01)
    pd.testing.assert_frame_equal(a, b)
    assert len(a) == 20 and a.event_id.nunique() == 20
    assert set(a.entry_day) == {'2024-01-02'}
    assert index[30] not in set(a.signal_bar_open)
    assert np.all(a.initial_stop < a.signal_close)
    assert audit.selected_controls.iloc[0] == 20


def test_controls_do_not_cross_day_to_fill_shortfall():
    index = pd.date_range('2024-01-01', periods=12, freq='4h', tz='UTC')
    bars = pd.DataFrame(dict(open=100., high=102., low=99., close=101., atr=1., rv=5., recentLow=99., ready=True), index=index)
    events = pd.DataFrame([dict(event_id='native', signal_bar_open=index[2], timeframe_min=240,
                                base_asset='BTC', venue='binance', symbol='BTCUSDT', dedup_keep=True)])
    a, _ = control_events(bars, events, .01)
    assert len(a) == 4
    assert set(a.entry_day) == {'2024-01-01'}

"""Causal V13 confirmation and next-open lifecycle checks, with explicit OHLC."""
import numpy as np
import pandas as pd
import pytest

from yoyo.monitor.v130_signals import replay, from_parent, STEP


def fixture(extra=()):
    rows = [(9.5, 10., 9., 9.5), (10., 11., 10., 10.5),
            (10.5, 10.8, 9.9, 10.2), (10.3, 11.5, 10.1, 11.2), *extra]
    index = pd.date_range('2026-09-25', periods=len(rows), freq='15min', tz='UTC')
    built = pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'], index=index)
    built['atr'] = .2
    built.attrs['data_gap'] = np.zeros(len(rows), bool)
    anchor = dict(bar_close_ms=int(index[0].value // 1_000_000) + STEP, side='long',
                  initial_stop=8., source_sha256='parent', base_asset='ETH')
    return built, np.zeros(len(rows), int), [anchor]


def test_confirm_at_final_close_does_not_wait_for_next_bar():
    built, side, anchors = fixture()
    events, state = replay(built, side, anchors, tick=.1)
    assert len(events) == 1
    e = events[0]
    assert e['wait_bars'] == 3
    assert e['bar_close_ms'] == int(built.index[-1].value // 1_000_000) + STEP
    assert e['anchor_close_ms'] < e['breakout_close_ms'] < e['retest_close_ms'] < e['bar_close_ms']
    assert e['performance']['reason'] == 'awaiting_next_closed_bar'
    assert not e['is_trade'] and e['executable_entry_time'] is None
    assert state['confirmations'] == 1


def test_confirmation_identity_survives_future_stop_and_changes_only_projection():
    built, side, anchors = fixture([(11.2, 11.4, 7.9, 9.)])
    events, _ = replay(built, side, anchors, tick=.1)
    prefix = built.iloc[:4].copy(); prefix.attrs['data_gap'] = np.zeros(4, bool)
    earlier, _ = replay(prefix, side[:4], anchors, tick=.1)
    assert {k:v for k,v in events[0].items() if k != 'performance'} == {k:v for k,v in earlier[0].items() if k != 'performance'}
    p = events[0]['performance']
    assert p['status'] == 'loss' and p['exit_reason'] == 'initial_stop'
    assert p['entry_price'] == 11.2 and p['exit_price'] == 8.
    assert p['exit_r'] == pytest.approx(-1 - .002 * 11.2 / 3.2)


def test_original_next_open_invalid_cancels_before_breakout():
    built, side, anchors = fixture()
    built.loc[built.index[1], ['open', 'low']] = [7.9, 7.9]
    assert replay(built, side, anchors, tick=.1)[0] == []


def test_delayed_invalid_open_does_not_remove_confirmation():
    built, side, anchors = fixture([(7.9, 9., 7.8, 8.5)])
    events, _ = replay(built, side, anchors, tick=.1)
    assert len(events) == 1
    assert events[0]['performance']['reason'] == 'delayed_risk_invalid'


def test_trail_is_active_on_following_bar_only():
    built, side, anchors = fixture([(11.2, 18.2, 10.5, 18.), (18., 18.1, 16.9, 17.5)])
    built['atr'] = .25
    events, _ = replay(built, side, anchors, tick=.1)
    p = events[0]['performance']
    assert p['exit_reason'] == 'trailing_stop'
    assert p['exit_price'] == pytest.approx(17.)
    assert p['bars_held'] == 1


def test_raw_opposite_exits_next_open_not_signal_close():
    built, side, anchors = fixture([(11.2, 12., 10., 11.8), (10.5, 11., 10., 10.8)])
    side[4] = -1
    events, _ = replay(built, side, anchors, tick=.1)
    p = events[0]['performance']
    assert p['exit_reason'] == 'raw_opposite_next_open' and p['exit_price'] == 10.5


def test_simultaneous_confirmations_choose_oldest_once():
    built, side, anchors = fixture()
    events, state = replay(built, side, anchors * 2, tick=.1)
    assert len(events) == 1 and state['occupied_skips'] == 1


@pytest.mark.parametrize('reason', ['opposite', 'stop', 'lost_level', 'gap'])
def test_canceled_sequence_cannot_notify(reason):
    built, side, anchors = fixture()
    if reason == 'opposite': side[2] = -1
    if reason == 'stop': built.loc[built.index[2], 'low'] = 7.9
    if reason == 'lost_level': built.loc[built.index[2], 'close'] = 10.
    if reason == 'gap': built.attrs['data_gap'][2] = True
    assert replay(built, side, anchors, tick=.1)[0] == []


def test_short_mirror_has_same_timing_and_entry_stop():
    built, side, anchors = fixture([(11.2, 11.4, 7.9, 9.)])
    old = built.copy()
    built['open'], built['close'] = 30-old.open, 30-old.close
    built['high'], built['low'] = 30-old.low, 30-old.high
    anchors[0].update(side='short', initial_stop=22.)
    events, _ = replay(built, side, anchors, tick=.1)
    assert len(events) == 1 and events[0]['side'] == 'short'
    assert events[0]['performance']['exit_price'] == 22.


@pytest.mark.parametrize('tf', ['5m', '2H', '1Dutc'])
def test_unmonitored_periods_rejected(tf):
    with pytest.raises(ValueError, match='15m/30m/1H/4H'):
        from_parent({}, {}, tick=.1, timeframe=tf)


def hourly(extra=()):
    """The same OHLC path on 1H opens, anchored one 1H bar after its open."""
    built, side, anchors = fixture(extra)
    built.index = pd.date_range('2026-09-25', periods=len(built), freq='1h', tz='UTC')
    anchors[0]['bar_close_ms'] = int(built.index[0].value // 1_000_000) + 3_600_000
    return built, side, anchors


def test_1h_uses_2atr_trail_and_other_periods_keep_4atr():
    """V13.1: after the 18 close (2R armed) a 1H trail is 18-2*.25=17.5, 15m stays 17."""
    rows = [(11.2, 18.2, 10.5, 18.), (18., 18.1, 16.9, 17.5)]
    built, side, anchors = hourly(rows)
    built['atr'] = .25
    events, state = replay(built, side, anchors, tick=.1, timeframe='1H')
    p = events[0]['performance']
    assert events[0]['timeframe'] == '1H' and events[0]['timeframe_min'] == 60
    assert events[0]['trail_atr'] == 2. and p['trail_atr'] == 2.
    assert p['exit_reason'] == 'trailing_stop' and p['exit_price'] == pytest.approx(17.5)
    assert state['timeframe'] == '1H'
    built, side, anchors = fixture(rows)
    built['atr'] = .25
    p = replay(built, side, anchors, tick=.1)[0][0]['performance']
    assert p['trail_atr'] == 4. and p['exit_price'] == pytest.approx(17.)


def test_period_must_match_the_bar_clock():
    built, side, anchors = hourly()
    built.index = built.index + pd.Timedelta(minutes=15)
    with pytest.raises(ValueError, match='ordered 1H opens'):
        replay(built, side, anchors, tick=.1, timeframe='1H')

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.chartart_bbrsi import features, replay
from yoyo.evaluation.chartart_bbrsi_v12 import buy_and_hold, compounded, exposure, replay_long_only

MINUTES = 15
START = pd.Timestamp('2026-01-01', tz='UTC')


def bars(n=4000, seed=7):
    """Random walk, so the close actually crosses both bands often enough."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, .004, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(dict(open=open_, high=np.maximum(open_, close) * 1.001,
                             low=np.minimum(open_, close) * .999, close=close, volume=1.),
                        index=pd.date_range(START, periods=n, freq=f'{MINUTES}min', tz='UTC'))


def window(frame):
    return str(frame.index[0]), str(frame.index[-1] + pd.Timedelta(minutes=MINUTES))


def marked(entries, flats, close=None):
    """Explicit signal frame; prices rise by one per bar unless overridden."""
    n = len(entries)
    price = np.arange(n, dtype=float) + 100 if close is None else np.asarray(close, float)
    frame = pd.DataFrame(dict(open=price, high=price + .5, low=price - .5, close=price, volume=1.,
                              long_signal=entries, short_signal=flats),
                         index=pd.date_range(START, periods=n, freq=f'{MINUTES}min', tz='UTC'))
    return frame


def test_long_only_never_opens_a_short():
    frame = features(bars())
    trades = replay_long_only(frame, *window(frame), MINUTES)
    assert len(trades) and set(trades.side) == {1}


def test_long_legs_are_identical_to_v11_which_only_adds_shorts():
    """v1.2 is v1.1 minus the short legs: the long state machine is unchanged."""
    frame = features(bars())
    columns = ['entry_i', 'exit_i', 'signal_i', 'entry_price', 'exit_price', 'net_return']
    v12 = replay_long_only(frame, *window(frame), MINUTES)
    v11 = replay(frame, *window(frame), MINUTES)
    assert (v11.side == -1).any()
    closed = lambda t: t.loc[(t.side == 1) & ~t.censored, columns].reset_index(drop=True)
    pd.testing.assert_frame_equal(closed(v12), closed(v11))


def test_exit_needs_the_flat_signal_not_only_a_price_move():
    frame = marked([True] + [False] * 8, [False] * 9)
    trades = replay_long_only(frame, *window(frame), MINUTES)
    assert len(trades) == 1 and bool(trades.censored.iloc[0])
    assert trades.exit_reason.iloc[0] == 'boundary_mark'
    assert trades.exit_price.iloc[0] == frame.close.iloc[-1]


def test_fills_are_next_open_and_ignore_later_bars():
    frame = marked([False, True, False, False, False, False], [False, False, False, True, False, False])
    trades = replay_long_only(frame, *window(frame), MINUTES)
    assert len(trades) == 1
    assert trades.entry_i.iloc[0] == 2 and trades.exit_i.iloc[0] == 4
    assert trades.entry_price.iloc[0] == frame.open.iloc[2] and trades.exit_price.iloc[0] == frame.open.iloc[4]
    later = frame.copy()
    later.iloc[5, later.columns.get_loc('close')] *= 3
    pd.testing.assert_frame_equal(trades, replay_long_only(later, *window(later), MINUTES))


def test_same_direction_signals_do_not_pyramid():
    frame = marked([False, True, True, True, False, False], [False, False, False, False, True, False])
    trades = replay_long_only(frame, *window(frame), MINUTES)
    assert len(trades) == 1 and trades.entry_i.iloc[0] == 2


def test_flat_signal_while_flat_does_nothing():
    frame = marked([False, False, False, True, False, False], [False, True, False, False, False, False])
    trades = replay_long_only(frame, *window(frame), MINUTES)
    assert len(trades) == 1 and trades.entry_i.iloc[0] == 4 and bool(trades.censored.iloc[0])


def test_cost_turns_a_small_gross_win_into_a_net_loss():
    frame = marked([False, True, False, False], [False, False, True, False], close=[100, 100, 100, 100.1])
    frame.iloc[3, frame.columns.get_loc('open')] = 100.1
    trades = replay_long_only(frame, *window(frame), MINUTES)
    assert trades.gross_return.iloc[0] > 0 > trades.net_return.iloc[0]
    assert trades.gross_return.iloc[0] - trades.net_return.iloc[0] == pytest.approx(.002)


def test_buy_and_hold_and_exposure_arithmetic():
    frame = marked([False, True, False, False, False, False], [False, False, False, True, False, False])
    hold = buy_and_hold(frame, *window(frame), MINUTES)
    assert hold['bars'] == 6 and hold['entry_price'] == frame.open.iloc[0]
    assert hold['gross_return'] == pytest.approx(frame.close.iloc[-1] / frame.open.iloc[0] - 1)
    assert hold['net_return'] == pytest.approx(hold['gross_return'] - .002)
    trades = replay_long_only(frame, *window(frame), MINUTES)
    assert exposure(trades, frame, *window(frame), MINUTES) == dict(window_bars=6, held_bars=2, time_in_market=2 / 6)
    assert compounded(trades) == pytest.approx(trades.net_return.iloc[0])


def test_entries_before_the_window_are_not_carried_in():
    frame = features(bars())
    start = str(frame.index[2000])
    trades = replay_long_only(frame, start, window(frame)[1], MINUTES)
    assert (pd.to_datetime(trades.entry_time, utc=True) >= pd.Timestamp(start)).all()


def test_incomplete_final_bar_is_refused():
    frame = marked([True] + [False] * 5, [False] * 6)
    unaligned = str(frame.index[-1] + pd.Timedelta(minutes=7))
    with pytest.raises(ValueError):
        replay_long_only(frame, str(frame.index[0]), unaligned, MINUTES)

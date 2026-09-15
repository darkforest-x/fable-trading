import numpy as np
import pandas as pd

from yoyo.evaluation.chartart_bbrsi import features, replay, rma, signal_masks


def bars(n=500):
    close = 100 + np.sin(np.arange(n)/8) * 10 + np.arange(n)/100
    return pd.DataFrame(dict(open=close, high=close+1, low=close-1, close=close, volume=1.),
                        index=pd.date_range('2026-01-01', periods=n, freq='3min', tz='UTC'))


def test_wilder_seed():
    result = rma([np.nan, 1, 2, 3, 4], 3)
    assert np.isnan(result[:3]).all()
    np.testing.assert_allclose(result[3:], [2, 8/3])


def test_feature_prefix_invariance_and_bands():
    frame = bars()
    result = features(frame)
    changed = frame.copy()
    changed.iloc[300:, changed.columns.get_loc('close')] *= 2
    pd.testing.assert_frame_equal(result.iloc[:300], features(changed).iloc[:300])
    assert np.isnan(result.basis.iloc[198])
    assert abs(result.upper.iloc[199] - (frame.close.iloc[:200].mean()+2*np.std(frame.close.iloc[:200]))) < 1e-10


def test_both_crosses_required():
    result = features(bars(5000))
    for i in np.flatnonzero(result.long_signal):
        assert result.rsi.iloc[i] > 50 >= result.rsi.iloc[i-1]
        assert result.close.iloc[i] > result.lower.iloc[i]
        assert result.close.iloc[i-1] <= result.lower.iloc[i-1]
    for i in np.flatnonzero(result.short_signal):
        assert result.rsi.iloc[i] < 50 <= result.rsi.iloc[i-1]
        assert result.close.iloc[i] < result.upper.iloc[i]
        assert result.close.iloc[i-1] >= result.upper.iloc[i-1]


def test_crossing_same_bar_not_persistent_rsi_state():
    frame = pd.DataFrame(dict(close=[89,91,89,91,111,109], lower=90, upper=110,
                              rsi=[49,51,51,52,51,49]))
    long, short = signal_masks(frame)
    assert long.tolist() == [False,True,False,False,False,False]
    assert short.tolist() == [False,False,False,False,False,True]


def test_next_open_same_direction_ignored_opposite_reverses():
    frame = bars(10)
    frame['long_signal'], frame['short_signal'] = False, False
    frame.loc[frame.index[[1, 2]], 'long_signal'] = True
    frame.loc[frame.index[5], 'short_signal'] = True
    result = replay(frame, frame.index[0], frame.index[-1]+pd.Timedelta(minutes=3), 3)
    assert result.entry_i.tolist() == [2, 6]
    assert result.exit_i.tolist() == [6, 9]
    assert result.censored.tolist() == [False, True]
    assert result.entry_price.iloc[0] == frame.open.iloc[2]
    assert result.exit_price.iloc[0] == frame.open.iloc[6]
    assert result.exit_price.iloc[1] == frame.close.iloc[9]


def test_no_future_entry_or_exit_wick():
    frame = bars(10)
    frame['long_signal'], frame['short_signal'] = False, False
    frame.loc[frame.index[1], 'long_signal'] = True
    frame.loc[frame.index[5], 'short_signal'] = True
    end = frame.index[-1]+pd.Timedelta(minutes=3)
    before = replay(frame, frame.index[0], end, 3)
    frame.loc[frame.index[6], 'high'] = 1e9
    after = replay(frame, frame.index[0], end, 3)
    assert before.mfe_return.iloc[0] == after.mfe_return.iloc[0]

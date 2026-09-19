"""Count and timestamp contracts before the owner's exit policy is chosen."""
import numpy as np
import pandas as pd

from yoyo.evaluation.btc_rsi_fifth import color_counts, prepare_signals


def test_visible_fifth_is_sixth_state_bar_after_flip():
    state, dots = color_counts(np.arange(9.) + 40, np.array([False, False] + [True] * 7))
    assert state.tolist() == [1, 2, 1, 2, 3, 4, 5, 6, 7]
    assert dots.tolist() == [1, 2, 0, 1, 2, 3, 4, 5, 6]


def test_warmup_and_short_color_run_do_not_carry_dot_count():
    state, dots = color_counts(np.array([np.nan, 40, 41, 42, 43, 44, 45.]),
                              np.array([False, True, True, False, True, True, True]))
    assert state.tolist() == [0, 1, 2, 1, 1, 2, 3]
    assert dots.tolist() == [0, 0, 1, 0, 0, 1, 2]


def test_four_hour_boundary_is_utc_and_partial_group_never_used():
    ix = pd.date_range("2025-01-01T21:00Z", periods=96, freq="5min")
    vals = np.arange(96.) + 100
    frame = pd.DataFrame(dict(open=vals, high=vals + 1, low=vals - 1, close=vals, volume=1.), index=ix)
    bars = prepare_signals(frame, 4)
    assert bars.index.tolist() == [pd.Timestamp("2025-01-02T04:00Z")]
    assert bars.iloc[0].open == 136
    assert bars.iloc[0].close == 183
    assert bars.iloc[0].volume == 48

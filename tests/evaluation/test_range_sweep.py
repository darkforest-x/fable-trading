"""Causal sweep boundaries and pre-pair rematching, using explicit OHLC."""
import numpy as np
import pandas as pd

from yoyo.evaluation.monday_range_sweep_reference import sweep_mask, select_sweep_matches
from yoyo.evaluation.monday_range_v2_31_reference import select_matches


def fixture():
    q = pd.DataFrame(dict(open=[99.]*30, high=[99.5]*30, low=[98.]*30,
                          close=[99.]*30),
                     index=pd.date_range('2026-10-20 14:00', periods=30, freq='15min', tz='UTC'))
    q.loc[q.index[19], 'high'] = 102.
    q.loc[q.index[22], 'high'] = 101.
    q.loc[q.index[23], 'high'] = 103.
    a = np.zeros(len(q), int); a[22] = -1
    grid = np.tile([90., 100.], (len(q), 1))
    weeks = np.array(['week']*len(q), object)
    return q, a, grid, ['MondayLow', 'MondayHigh'], weeks


def test_history_excludes_touch_and_requires_twenty():
    q, *_ = fixture()
    m = sweep_mask(q, -1)
    assert not m[:20].any()
    assert not m[22] and m[23]
    q.loc[q.index[23], 'high'] = 102.
    assert not sweep_mask(q, -1)[23]


def test_later_valid_touch_is_not_lost_by_post_filtering():
    args = fixture()
    old = select_matches(*args, start='2026-01-01T00:00Z')
    new = select_sweep_matches(*args, start='2026-01-01T00:00Z')
    assert len(old) == len(new) == 1
    assert old[0]['touch_i'] == old[0]['entry_i'] == 22
    assert new[0]['touch_i'] == new[0]['entry_i'] == 23
    assert not sweep_mask(args[0], -1)[old[0]['touch_i']]


def test_mirror_and_no_lookahead():
    q, a, grid, names, weeks = fixture()
    mirrored = q.copy()
    for col in ['open', 'close']:
        mirrored[col] = 200-q[col]
    mirrored.high = 200-q.low; mirrored.low = 200-q.high
    newgrid = 200-grid[:, ::-1]
    short = select_sweep_matches(q, a, grid, names, weeks, start='2026-01-01T00:00Z')
    long = select_sweep_matches(mirrored, -a, newgrid, names, weeks, start='2026-01-01T00:00Z')
    assert len(short) == len(long) == 1
    assert short[0]['entry_i'] == long[0]['entry_i'] == 23
    for end in range(20, 31):
        prefix = select_sweep_matches(q.iloc[:end], a[:end], grid[:end], names, weeks[:end], start='2026-01-01T00:00Z')
        assert prefix == [r for r in short if r['entry_i'] < end]


def test_inputs_are_not_modified():
    q, a, grid, names, weeks = fixture()
    original_q = q.copy(); original_a = a.copy(); original_grid = grid.copy()
    select_sweep_matches(q, a, grid, names, weeks)
    pd.testing.assert_frame_equal(q, original_q)
    np.testing.assert_array_equal(a, original_a)
    np.testing.assert_array_equal(grid, original_grid)

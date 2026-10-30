"""Ensure range-return labels cannot use prices preceding the first touch."""
import pandas as pd
import pytest

from yoyo.evaluation.btc_1618_return_count import same_bar_bounds


@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('case,expected', [
    ('before_only', (False, False)),
    ('later_return', (True, True)),
    ('unknown_order', (False, True)),
    ('close_proves', (True, True)),
])
def test_touch_precedes_return(mirror, case, expected):
    index = pd.date_range('2026-10-28T00:00:00Z', periods=15, freq='min')
    minute = pd.DataFrame([[112, 114, 111, 112]]*15, index=index,
                          columns=['open', 'high', 'low', 'close'])
    minute.iloc[0] = [100, 105, 95, 104]
    minute.iloc[1] = [104, 115, 103, 112]
    if case == 'later_return':
        minute.iloc[5] = [112, 113, 99, 101]
    elif case == 'unknown_order':
        minute.iloc[1] = [104, 115, 99, 112]
    elif case == 'close_proves':
        minute.iloc[-1] = [112, 113, 99, 100]
    level, edge, side = 110, 100, 'upper'
    if mirror:
        minute = pd.DataFrame(dict(open=200-minute.open, high=200-minute.low,
                                   low=200-minute.high, close=200-minute.close), index=index)
        level, edge, side = 90, 100, 'lower'
    bars = minute.resample('15min').agg(dict(open='first', high='max', low='min', close='last'))
    assert same_bar_bounds(bars, minute, 0, side, level, edge) == expected

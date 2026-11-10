from types import SimpleNamespace

import pandas as pd

from yoyo.evaluation.month_first_week_alt_entries import HOUR, bounds, prepare_symbol


def test_confirms_only_at_closed_hours_and_cancels_at_the_target():
    start, ready, end = bounds('2024-03')
    index = pd.date_range(start, end + 8 * 24 * HOUR, inclusive='left', freq='h')
    frame = pd.DataFrame(dict(open=94., high=95., low=94., close=94.), index=index)
    frame.loc[frame.index < ready] = [105., 110., 100., 105.]
    # Lower outer level 93.82, near edge 100. First hour closes outside.
    frame.loc[ready] = [94., 95., 91., 92.]
    frame.loc[ready + HOUR] = [92., 95., 91.5, 94.]
    spec = SimpleNamespace(symbol='FIXTURE', cohort='test')

    def actual_times(prices):
        _, events, candidates = prepare_symbol(spec, prices)
        assert len(events)==1 and events[0]['touch']==ready
        return {row['entry_rule']:row['entry_time'] for row in candidates if not row['is_control']}

    clock = actual_times(frame)
    assert clock['touch_1h']==clock['touch_close_outside']==ready+HOUR
    assert 'touch_close_inside' not in clock
    assert clock['reclaim_1h']==ready+2*HOUR
    assert clock['reclaim_two_1h']==ready+3*HOUR
    assert clock['reclaim_4h'].hour % 4==0 and clock['reclaim_1d'].hour==0

    frame.loc[ready+HOUR, 'high']=101.
    cancelled=actual_times(frame)
    assert cancelled=={'touch_1h':ready+HOUR, 'touch_close_outside':ready+HOUR}

"""V2.23 checks: two-sided touch windows and irreversible closed-bar admission."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.monday_range_v2_23_reference import select_matches


def case(touches=(6,)):
    q = pd.DataFrame([[99., 99.8, 98., 99.5]] * 9,
                     columns=['open', 'high', 'low', 'close'],
                     index=pd.date_range('2026-10-20T21:30+08:00', periods=9, freq='15min'))
    for j in touches:
        q.iloc[j, q.columns.get_loc('high')] = 101.
    a = np.array([0, 0, 0, 0, 0, -1, 0, 0, 0])
    p = np.tile([100., 110., 111.3], (len(q), 1))
    return q, a, p, ['0:up', '0:down', '1.13:up'], np.array(['2026-10-19'] * len(q))


@pytest.mark.parametrize('touch,entry', [(3, 5), (4, 5), (5, 5), (6, 6), (7, 7)])
def test_earliest_close_with_arrow_and_touch_enters(touch, entry):
    r = select_matches(*case((touch,)))[0]
    assert (r['signal_i'], r['touch_i'], r['entry_i']) == (5, touch, entry)
    assert r['entry_time'] == case()[0].index[entry].isoformat()
    assert r['entry'] == 99.5
    assert r['stop'] == 101.2


@pytest.mark.parametrize('touches', [(2,), (8,)])
def test_touches_outside_two_bar_window_do_not_enter(touches):
    assert select_matches(*case(touches)) == []


def test_later_body_cannot_cancel_or_delay_first_admission():
    q, a, p, n, w = case((6, 7))
    first = select_matches(q.iloc[:7], a[:7], p[:7], n, w[:7])
    q.iloc[7] = [99., 102., 98., 101.]
    assert select_matches(q, a, p, n, w) == first
    assert first[0]['entry_i'] == 6


def test_body_between_arrow_and_second_touch_invalidates_same_key():
    q, a, p, n, w = case((7,))
    q.iloc[6] = [99., 101., 98., 100.]
    assert select_matches(q, a, p, n, w) == []


def test_prior_body_boundary_equality_invalidates_same_key():
    q, a, p, n, w = case()
    q.iloc[0] = [99., 101., 98., 100.]
    assert select_matches(q, a, p, n, w) == []


def test_one_bar_expiry_does_not_accept_second_bar():
    assert select_matches(*case((7,)), wait_bars=1) == []


@pytest.mark.parametrize('touches,chosen', [((3,4,5),5), ((3,4),4)])
def test_at_arrow_close_nearest_eligible_touch_wins(touches,chosen):
    r = select_matches(*case(touches))[0]
    assert (r['touch_i'],r['entry_i']) == (chosen,5)


def test_earlier_touch_uses_its_own_level_snapshot():
    q,a,p,n,w = case((3,))
    p[4:,0] = 102.
    r = select_matches(q,a,p,n,w)[0]
    assert r['touch_i'] == 3 and r['level'] == 100.


def test_body_between_earlier_touch_and_arrow_invalidates_pair():
    q,a,p,n,w = case((3,))
    q.iloc[4] = [99.,101.,98.,100.]
    assert select_matches(q,a,p,n,w) == []


def test_earlier_touch_cannot_cross_week_boundary():
    q,a,p,n,w = case((3,))
    w[3] = '2026-10-12'
    assert select_matches(q,a,p,n,w) == []


@pytest.mark.parametrize('touches', [(3,), (4,), (5,), (6,), (7,), (4, 5), (6, 7), (8,)])
def test_all_prefixes_preserve_admissions(touches):
    q, a, p, n, w = case(touches)
    full = select_matches(q, a, p, n, w)
    for end in range(1, len(q) + 1):
        assert select_matches(q.iloc[:end], a[:end], p[:end], n, w[:end]) == [r for r in full if r['entry_i'] < end]


def test_btc_oct20_uses_first_following_fib_touch_and_its_close():
    values = [[81378,81500,81358.6,81380.9], [81380.8,81743.2,81273,81613.1],
              [81613.2,82149,81605.1,82124.3], [82124.3,83218.5,82066.5,83202.7],
              [83202.7,83333,82801.4,82949.9], [82949.9,83298.2,82860,83086.4],
              [83086.4,83436.9,82950.2,83231.6], [83231.6,83273.6,82152.5,82382.3]]
    q, a, p, n, w = case()
    q = pd.DataFrame(values, columns=q.columns, index=q.index[:8])
    p = np.tile([81166.,83149.8,83407.694], (len(q),1))
    r = select_matches(q,a[:8],p,n,w[:8])[0]
    assert (r['touch_i'],r['entry_i'],r['key']) == (6,6,'1.13:up')
    assert (r['entry'],r['stop']) == (83231.6,83603.4)
    assert r['entry_bar_time'] == '2026-10-20T22:45:00+08:00'
    assert r['entry_time'] == '2026-10-20T23:00:00+08:00'


def test_eth_aug27_second_candle_label_is_not_its_close_timestamp():
    # Actual OKX candles labeled 15:30..17:15 Shanghai; last closes 17:30.
    values = [[2489.65,2493.04,2489.34,2490.63], [2490.63,2498.10,2490.31,2495.92],
              [2495.93,2528,2495.93,2518.89], [2518.90,2537.48,2511.24,2533.28],
              [2533.27,2545.88,2525.66,2535.26], [2535.25,2539.38,2527.69,2529.91],
              [2529.92,2535.88,2524.23,2534.14], [2534.13,2566.26,2529,2553.33]]
    q = pd.DataFrame(values, columns=['open','high','low','close'],
                     index=pd.date_range('2026-08-27T15:45+08:00',periods=8,freq='15min'))
    a = np.array([0,0,0,0,0,-1,0,0])
    # Isolate the selected key; the other two prices cannot match any touch.
    p = np.tile([2400.,2600.,2561.9319],(8,1))
    r = select_matches(q,a,p,['0:up','0:down','1.27:up'],np.array(['2026-08-24']*8),
                       start='2026-08-21T00:00+08:00',tick=.01)[0]
    assert (r['signal_i'],r['touch_i'],r['entry_i']) == (5,7,7)
    assert r['entry_bar_time'] == '2026-08-27T17:15:00+08:00'
    assert r['entry_time'] == '2026-08-27T17:30:00+08:00'
    assert (r['entry'],r['stop']) == (2553.33,2571.37)

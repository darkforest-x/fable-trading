"""Behavioral guards for actual-clock reporting and temporal selection."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_v128_entry_clock import clock, cohort, bucket, prepare_controls, pair_rows, choose, describe


def frame(times):
    return pd.DataFrame({'entry_time': times, 'exit_time': times, 'net_return': .01,
                         'net_r': 1., 'gross_return': .012, 'symbol': 'X', 'status': 'closed'})


def test_clock_midnight_and_right_open_boundary():
    d = clock(frame(['2026-08-01T15:59Z', '2026-08-01T16:00Z', '2026-08-01T20:00Z']), 'Asia/Shanghai')
    assert d.hour.tolist() == [23, 0, 4]
    assert bucket(d, '4h').tolist() == [20, 0, 4]
    assert d.weekday.tolist() == [5, 6, 6]


def test_retest_uses_actual_entry_not_anchor():
    d = frame(['2026-08-01T16:00Z']); d['signal_close'] = '2026-08-01T12:00Z'
    assert clock(d, 'Asia/Shanghai').hour.iloc[0] == 0


def test_earlier_requires_mature_exit_and_censor_not_loss():
    d = frame(['2026-08-22T23:00Z']*3 + ['2026-08-23T00:00Z'])
    d['exit_time'] = ['2026-08-22T23:30Z', '2026-08-23T01:00Z', None, '2026-08-23T01:00Z']
    d.loc[2, ['status', 'net_return', 'net_r']] = ['censored_boundary', np.nan, np.nan]
    d = clock(d, 'Asia/Shanghai'); split = pd.Timestamp('2026-08-23T00:00Z')
    assert list(cohort(d, 'earlier', split).index) == [0]
    assert list(cohort(d, 'later', split).index) == [3]
    assert list(cohort(d, 'cross', split).index) == [1, 2]
    r = describe(d, {'bootstrap': 10, 'statistics_seed': 1})
    assert r['closed'] == 3 and r['censored'] == 1 and r['win_rate'] == 1


def test_clock_random_pair_and_sunday_anchor_exclusion():
    targets = clock(frame(['2026-08-01T16:00Z']*3), 'Asia/Shanghai')
    targets['trade_key'] = ['a', 'b', 'c']; targets['policy'] = 'retest'
    rows = []
    for key, anchor, entry in [('a', '2026-08-01T12:00Z', '2026-08-01T16:00Z'),
                               ('b', '2026-08-01T12:00Z', '2026-08-01T17:00Z'),
                               ('c', '2026-08-02T00:00Z', '2026-08-02T16:00Z')]:
        for policy, when in [('baseline', anchor), ('retest', entry)]:
            rows.append(dict(trade_key=key, control_pool=policy, matched=True, control_status='closed',
                             control_signal_close=when, control_exit_time='2026-08-03T00:00Z',
                             control_net_return=.01, control_net_r=1.))
    c = prepare_controls(pd.DataFrame(rows), 'Asia/Shanghai')
    c = c[c.policy == 'retest']
    assert set(c.trade_key) == {'a', 'b'}
    assert pair_rows(targets, c, '1h', 'all', pd.Timestamp('2026-08-23T00:00Z')).trade_key.tolist() == ['a']
    assert set(pair_rows(targets, c, '4h', 'all', pd.Timestamp('2026-08-23T00:00Z')).trade_key) == {'a', 'b'}


def test_earlier_selection_ignores_later_outcomes_and_full_guards():
    d = pd.DataFrame({'bucket': [0, 4], 'closed': [16, 15], 'weeks': [3, 3],
                      'win_rate': [.4, .5], 'earlier_closed': [16, 15], 'later_closed': [1, 100],
                      'later_win_rate': [.99, .01]})
    cfg = dict(early_rank_min_closed=15, early_rank_min_weeks=3, rank_min_closed=30,
               rank_min_weeks=6, rank_min_each_half=10)
    assert choose(d, 'win_rate', cfg, earlier=True).bucket == 4
    assert choose(d, 'win_rate', cfg) is None
    d['later_win_rate'] = [0, 1]
    assert choose(d, 'win_rate', cfg, earlier=True).bucket == 4


def test_duplicate_random_anchor_fails():
    c = pd.DataFrame({'trade_key': ['a', 'a'], 'control_pool': 'baseline', 'control_signal_close': '2026-08-01T00:00Z'})
    with pytest.raises(ValueError, match='duplicate random anchor'):
        prepare_controls(c, 'Asia/Shanghai')

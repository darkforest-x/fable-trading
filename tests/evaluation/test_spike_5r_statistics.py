"""Label boundaries, censor handling and identity retention for V5 metrics."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_5r_statistics as t


def sample():
    times = pd.date_range('2025-10-01', periods=6, freq='MS', tz='UTC')
    return pd.DataFrame(dict(event_key=list('abcdef'), entry_time=times,
        valid_entry=[True, True, True, True, True, False],
        censored=[False, False, False, False, True, False],
        net_r=[5., 5.0001, -1., 11., 100., 100.],
        net_return=[.01, .02, -.03, .04, 1., 1.],
        gross_return=[.012, .022, -.028, .042, 1.002, 1.002], asset='A'))


def test_strict_net_boundary_and_unknown_denominators():
    c = sample()
    row = t.metrics(c, c)
    assert (row['closed'], row['gt5'], row['precision']) == (4, 2, .5)
    assert (row['censored'], row['invalid'], row['confirmed_gt5_per_candidate']) == (1, 1, 2/6)
    assert row['win_rate'] == .75
    assert row['mean_net_bp'] == pytest.approx(100.)
    assert row['mean_gross_bp'] == pytest.approx(120.)
    # Changing gross returns does not turn net-R == 5 into a tail winner.
    c['gross_return'] = 999.
    assert t.metrics(c, c)['gt5'] == 2


def test_paired_null_uses_same_strict_target_and_never_redraws():
    c = sample()
    controls = pd.DataFrame(dict(event_key=c.event_key, target_net_r=c.net_r,
        matched=True, control_censored=[False, False, False, True, False, False],
        control_net_r=[5.001, 5., -1., 100., 100., 100.],
        control_net_return=0., control_entry_time=c.entry_time, control_exit_time=c.entry_time))
    row = t.control_statistics(c, controls, 'full')
    assert (row['matched_pairs'], row['unmatched_closed']) == (3, 1)
    assert (row['random_gt5'], row['paired_gt5']) == (1, 1)
    assert row['random_precision'] == 1/3
    assert row['paired_excess_net_bp'] == pytest.approx(0., abs=1e-12)
    controls.loc[0, 'target_net_r'] = 6.
    with pytest.raises(ValueError, match='target drift'):
        t.control_statistics(c, controls, 'full')


def test_bootstrap_and_retention_separate_new_winners():
    c = sample()
    assert t.rate_interval(c, c) == dict(precision_delta=0., precision_ci_low=0., precision_ci_high=0.)
    assert np.isnan(t.rate_interval(c, c.iloc[:0])['precision_delta'])
    selected = c.iloc[[1, 3]].copy()
    selected.loc[selected.index[1], 'event_key'] = 'new'
    row = t.serial_retention(selected, c)
    assert row == dict(retained_gt5=1, lost_gt5=1, gained_gt5=1, recall=.5, gt5_count_ratio=1.)

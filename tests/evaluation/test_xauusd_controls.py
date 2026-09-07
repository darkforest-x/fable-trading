"""Synthetic checks for outcome-blind gold event controls; no market replay."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.xauusd_controls import matched_controls, holm, _inference


def fixture(n=30, actual_indices=(1, 9, 17), sides=None):
    index = pd.date_range('2024-01-02', periods=n, freq='h', tz='UTC')
    bars = pd.DataFrame({'time_close': index + pd.Timedelta(hours=1)}, index=index)
    minute_index = pd.date_range(index[0], bars.time_close.iloc[-1], freq='min')
    price = 100 + np.arange(len(minute_index)) * .001
    minutes = pd.DataFrame({'open': price, 'close': price + .0002}, index=minute_index)
    f = pd.DataFrame({'ready': True, 'volbin': 2, 'md': np.arange(n) + 1., 'atr': 1.}, index=index)
    e = np.zeros(n, dtype=int)
    sides = np.ones(len(actual_indices), dtype=int) if sides is None else np.asarray(sides)
    e[list(actual_indices)] = sides
    xl, xs = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    for i in actual_indices:
        if i + 2 < n:
            xl[i+2] = xs[i+2] = True
    gross = np.arange(len(actual_indices)) * 40. + 10
    ledger = pd.DataFrame({'trade_id': np.arange(len(actual_indices)),
                           'decision_index': actual_indices, 'side': sides,
                           'gross_bp': gross, 'net_bp': gross-20})
    return minutes, bars, f, e, xl, xs, ledger, index[0], bars.time_close.iloc[-1]


def test_pair_identity_does_not_use_future_outcomes():
    args = list(fixture())
    a = matched_controls(*args)
    changed = args[0].copy()
    after = changed.index >= args[1].time_close.iloc[1]
    changed.loc[after, ['open', 'close']] += np.linspace(0, 20, int(after.sum()))[:, None]
    args[0] = changed
    b = matched_controls(*args)
    identity = ['actual_trade_id', 'control_decision_index', 'side', 'month', 'volbin']
    pd.testing.assert_frame_equal(a['pairs'][identity], b['pairs'][identity])
    assert not np.allclose(a['pairs'].control_net_bp, b['pairs'].control_net_bp)
    assert a['summary']['matched_n'] == 3
    assert a['summary']['paired_p'] == a['summary']['p']
    assert not a['pairs'].duplicated(['control_decision_index', 'side']).any()


def test_controls_match_decision_month_volatility_and_direction():
    args = list(fixture(n=60, actual_indices=(1, 31), sides=(1, -1)))
    old = args[1].index
    index = old[:30].append(old[30:] + pd.Timedelta(days=31))
    args[1] = args[1].set_axis(index)
    args[1]['time_close'] = index + pd.Timedelta(hours=1)
    args[2] = args[2].set_axis(index)
    args[2]['volbin'] = np.arange(60) % 2 + 1
    args[2].loc[index[3], 'ready'] = False
    args[3][5] = -1  # Even opposite-side entry requests are excluded.
    minute_index = pd.date_range(index[0], args[1].time_close.iloc[-1], freq='min')
    args[0] = pd.DataFrame({'open': 100., 'close': 100.1}, index=minute_index)
    args[-1] = args[1].time_close.iloc[-1]
    result = matched_controls(*args)
    assert result['summary']['matched_n'] == 2
    for row in result['pairs'].itertuples():
        j = row.control_decision_index
        assert row.month == args[1].time_close.iloc[j].strftime('%Y-%m')
        assert row.volbin == args[2].volbin.iloc[j]
        assert row.side == int(args[6].set_index('trade_id').loc[row.actual_trade_id, 'side'])
        assert bool(args[2].ready.iloc[j]) and args[3][j] == 0


def gap_fixture():
    args = list(fixture(n=11, actual_indices=(0,)))
    day = pd.Timestamp('2024-01-02', tz='UTC')
    times = [day + pd.Timedelta(hours=h, minutes=m) for h, m in
             [(1, 0), (1, 1), (5, 0), (5, 1), (9, 0), (9, 1), (10, 0)]]
    args[0] = pd.DataFrame({'open': [100., 101., 120., 122., 150., 154., 999.],
                            'close': [100.5, 101.5, 121., 123., 151., 155., 999.]},
                           index=pd.DatetimeIndex(times))
    args[2]['ready'] = False
    args[2].iloc[[0, 1, 9, 10], args[2].columns.get_loc('ready')] = True
    args[4][:] = False
    args[4][[1, 5]] = True  # Exit at control's own decision must be ignored.
    args[5][:] = False
    args[-1] = day + pd.Timedelta(hours=10)
    return args


def test_exit_uses_first_later_decision_then_actual_next_open_across_gap():
    args = gap_fixture()
    row = matched_controls(*args, n_controls=1)['pairs'].iloc[0]
    assert row.control_decision_index == 1
    assert row.control_entry_time == pd.Timestamp('2024-01-02 05:00Z')
    assert row.control_exit_decision_index == 5
    assert row.control_exit_time == pd.Timestamp('2024-01-02 09:00Z')
    assert row.control_entry_price == 120
    assert row.control_exit_price == 150
    assert row.control_gross_bp == pytest.approx(2500)
    assert row.control_net_bp == pytest.approx(2480)
    assert row.control_entry_index == 2 and row.control_exit_index == 4


def test_boundary_does_not_read_incomplete_minute_or_after_end():
    args = gap_fixture()
    args[-1] = pd.Timestamp('2024-01-02 09:00:30Z')
    row = matched_controls(*args, n_controls=1)['pairs'].iloc[0]
    # The 09:00 minute opens before end but does not close inside the window.
    assert row.control_exit_kind == 'boundary'
    assert row.control_exit_price == 123
    assert row.control_exit_time == pd.Timestamp('2024-01-02 05:02Z')
    assert row.control_net_bp == pytest.approx(230)
    altered = args[0].copy()
    altered.loc[altered.index >= pd.Timestamp('2024-01-02 09:00Z'), :] = np.nan
    args[0] = altered
    again = matched_controls(*args, n_controls=1)['pairs'].iloc[0]
    assert again.control_exit_price == row.control_exit_price


def test_full_three_controls_or_none_and_side_specific_nonreuse():
    args = list(fixture(n=7, actual_indices=(0, 1), sides=(1, 1)))
    args[2]['ready'] = False
    args[2].iloc[:5, args[2].columns.get_loc('ready')] = True
    result = matched_controls(*args)
    assert result['summary']['matched_n'] == 1
    assert result['summary']['unmatched_n'] == 1
    assert len(result['pairs']) == 3
    args[3][1] = -1
    args[6].loc[1, 'side'] = -1
    opposite = matched_controls(*args)
    assert opposite['summary']['matched_n'] == 2
    assert len(opposite['pairs']) == 6
    assert opposite['pairs'].control_decision_index.nunique() == 3
    assert not opposite['pairs'].duplicated(['control_decision_index', 'side']).any()


def test_fixed_score_decile_and_auc_do_not_rank_realized_profit():
    args = list(fixture(n=60, actual_indices=tuple(range(1, 51, 5))))
    args[6]['net_bp'] = [100.] * 9 + [-100.]
    args[6]['gross_bp'] = args[6].net_bp + 20
    summary = matched_controls(*args)['summary']
    assert summary['score_auc'] == 0
    assert summary['score_top_decile_n'] == 1
    assert summary['top_decile_net_bp'] == -100
    assert summary['top_decile_gross_bp'] == -80
    assert summary['score_top_decile_win_pct'] == 0


def test_missing_pairs_single_profit_class_and_empty_ledger_are_explicit():
    args = list(fixture(n=5, actual_indices=(0,)))
    args[2]['ready'] = False
    args[2].iloc[0, args[2].columns.get_loc('ready')] = True
    summary = matched_controls(*args)['summary']
    assert summary['matched_n'] == 0
    assert summary['excess_bp'] is None and summary['paired_p'] is None
    assert summary['score_auc'] is None
    assert summary['score_auc_status'] == 'single_profit_class'
    args[6] = args[6].iloc[:0]
    result = matched_controls(*args)
    assert result['summary']['actual_n'] == 0 and result['pairs'].empty
    assert result['summary']['score_auc'] is None


def test_monthly_inference_weights_month_means_not_individual_trades():
    cases = pd.DataFrame({'month': ['2024-01'] * 10 + ['2024-02'],
                          'excess_bp': [10.] * 10 + [20.]})
    stats = _inference(cases, 20260908)
    assert stats['months'] == 2 and stats['p'] == .25
    assert stats['monthly_mean_excess_bp'] == 15
    assert stats['ci_low'] == 10 and stats['ci_high'] == 20
    one = _inference(cases.iloc[:10], 20260908)
    assert one['p'] == .5 and one['ci_low'] is None
    assert one['inference_status'] == 'one_month_no_bootstrap_ci'
    assert stats == _inference(cases, 20260908)


def test_bankruptcy_scope_mismatch_disables_inference_not_pair_records():
    args = list(fixture())
    args[6]['exit_reason'] = ['bankruptcy', 'signal', 'signal']
    summary = matched_controls(*args)['summary']
    assert summary['matched_n'] == 3
    assert summary['actual_bankruptcy_n'] == 1
    assert summary['paired_p'] is None and summary['p'] is None
    assert summary['inference_status'].startswith('actual_bankruptcy')


def test_holm_uses_all_planned_hypotheses_including_missing_tests():
    p = np.r_[.0001, .02, np.repeat(np.nan, 187)]
    adjusted = holm(p)
    np.testing.assert_allclose(adjusted[:2], [.0189, 1])
    assert np.isnan(adjusted[2:]).all()
    np.testing.assert_allclose(holm([.04, .01, .03]), [.06, .03, .06])
    assert holm([]).size == 0
    with pytest.raises(ValueError):
        holm([1.1])


def test_out_of_fold_actual_trade_or_misaligned_features_fail():
    args = list(fixture())
    args[-1] = args[1].time_close.iloc[1]
    with pytest.raises(ValueError, match='eligible'):
        matched_controls(*args)
    args = list(fixture())
    args[2] = args[2].iloc[::-1]
    with pytest.raises(ValueError, match='index'):
        matched_controls(*args)

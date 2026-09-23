"""Protect win denominators and established-versus-ambiguous MFE accounting."""
import hashlib
import json

import numpy as np
import pandas as pd
from yoyo.evaluation.spike_v128_recent_report import build, digest, enrich, metrics, block_inference


def sample():
    return pd.DataFrame({'trade_key':['a','b','c'], 'signal_close':['2026-08-01']*3,
        'entry_time':['2026-08-01']*3, 'exit_time':['2026-08-02']*3,
        'net_return':[-.012,-.001,.018], 'gross_return':[-.01,.001,.02],
        'net_r':[-1.2,-.1,1.8], 'gross_r':[-1.,.1,2.], 'initial_risk_frac':[.01]*3,
        'mfe_known_r':[1.,.1,3.], 'mfe_upper_r':[2.,.1,3.],
        'close_peak_r':[.8,.1,2.1], 'exit_reason':['initial_stop','opposite_v6_next_open','trailing_stop']})


def test_fee_covering_profit_and_stop_bar_ambiguity_stay_separate():
    result=metrics(enrich(sample(),pd.Timestamp('2026-08-23',tz='UTC')))
    assert result['closed']==3 and result['wins']==1 and result['losses']==2
    assert result['fee_only_losses']==1
    assert result['float_to_stop_loss_1r']==1
    assert result['float_to_stop_loss_2r']==0
    assert result['ambiguous_stop_profit_2r']==1
    assert result['net_float_to_any_loss']==1
    assert np.isclose(result['mean_gross_bp']-result['mean_net_bp'],20)


def test_week_sign_flip_uses_exact_small_sample_resolution():
    result=block_inference([1.,2.],['week1','week2'],bootstrap=20)
    assert result['p_one_sided']==.25
    assert result['minimum_p_resolution']==.25
    assert result['mean_excess_bp']==1.5


def _coverage_summary(symbol, minutes, expected, actual, ready, *, window_gaps=0):
    return {'symbol': symbol, 'minutes': minutes, 'window_bars_expected': expected,
            'window_bars_actual': actual, 'window_gap_count': window_gaps,
            'chart_gaps': window_gaps, 'partial_chart_buckets': int(window_gaps > 0),
            'partial_higher_buckets': 0, 'valid_ready_window_bars': ready,
            'first': None if not actual else '2026-07-23T00:00:00+00:00',
            'last': None if not actual else '2026-09-22T23:45:00+00:00'}


def _write_stream(root, symbol, minutes, run_identity, summary, *, statuses=None, trades=None, controls=None):
    folder = root / 'streams' / f'{symbol}_{minutes}m'
    folder.mkdir(parents=True)
    tables = {'statuses': statuses if statuses is not None else pd.DataFrame(),
              'trades': trades if trades is not None else pd.DataFrame(),
              'controls': controls if controls is not None else pd.DataFrame()}
    for name in ('decisions', 'frames', 'hints'):
        tables[name] = pd.DataFrame()
    for name, table in tables.items():
        table.to_csv(folder / f'{name}.csv.gz', index=False, compression='gzip')
    receipt = {'status': 'complete', 'symbol': symbol, 'minutes': minutes,
               'run_identity': run_identity, 'summary': summary,
               'files': {f'{name}.csv.gz': digest(folder / f'{name}.csv.gz') for name in tables}}
    (folder / 'receipt.json').write_text(json.dumps(receipt))
    return digest(folder / 'receipt.json')


def _coverage_fixture(tmp_path):
    root = tmp_path / 'run'
    (root / 'streams').mkdir(parents=True)
    identity = {'config': {'split': '2026-08-23T00:00:00Z', 'stat_seed': 923129, 'bootstrap': 20}}
    (root / 'identity.json').write_text(json.dumps(identity))
    run_identity = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    statuses = pd.DataFrame({'timeframe_min': [15, 15, 15], 'arm': ['v9_both', 'v9_both', 'joint'],
                             'status': ['closed', 'skipped_in_position', 'censored_boundary']})
    trades = pd.DataFrame({'trade_key': ['closed', 'censored'], 'symbol': ['PART', 'PART'],
        'timeframe_min': [15, 15], 'arm': ['v9_both', 'joint'], 'side': [1, 1],
        'signal_close': ['2026-08-01T00:00:00Z'] * 2, 'entry_time': ['2026-08-01T00:00:00Z'] * 2,
        'exit_time': ['2026-08-02T00:00:00Z'] * 2, 'net_return': [.01, np.nan], 'gross_return': [.012, np.nan],
        'net_r': [1., np.nan], 'gross_r': [1.2, np.nan], 'initial_risk_frac': [.01, .01],
        'mfe_known_r': [1.2, np.nan], 'mfe_upper_r': [1.2, np.nan], 'close_peak_r': [1.2, np.nan],
        'exit_reason': ['trailing_stop', 'boundary_mark'], 'censored': [False, True]})
    controls = pd.DataFrame({'trade_key': ['closed'], 'matched': [True], 'control_net_return': [0.0],
                             'control_exit_time': ['2026-08-02T00:00:00Z']})
    receipts = {
        'COMPLETE_60m': _write_stream(root, 'COMPLETE', 60, run_identity, _coverage_summary('COMPLETE', 60, 10, 10, 10)),
        'EMPTY_15m': _write_stream(root, 'EMPTY', 15, run_identity, _coverage_summary('EMPTY', 15, 40, 0, 0)),
        'PART_15m': _write_stream(root, 'PART', 15, run_identity, _coverage_summary('PART', 15, 40, 38, 37, window_gaps=2), statuses=statuses, trades=trades, controls=controls),
    }
    (root / 'manifest.json').write_text(json.dumps({'complete': True, 'run_identity': run_identity,
        'stream_keys': sorted(receipts), 'receipts': receipts}))
    return root


def test_build_exposes_receipt_coverage_and_observed_arm_denominators(tmp_path):
    root = _coverage_fixture(tmp_path)
    receipt = build(root, tmp_path / 'report')
    coverage = pd.read_csv(tmp_path / 'report' / 'coverage.csv').set_index('symbol')
    assert coverage.loc['COMPLETE', 'coverage_status'] == 'complete'
    assert coverage.loc['EMPTY', 'coverage_status'] == 'no_recent_data'
    assert coverage.loc['PART', 'coverage_status'] == 'partial'
    assert coverage.loc['PART', ['window_bars_expected', 'window_bars_actual', 'window_gap_count', 'valid_ready_window_bars']].tolist() == [40, 38, 2, 37]
    counts = pd.read_csv(tmp_path / 'report' / 'arm_counts.csv').set_index('arm')
    assert counts.loc['v9_both', ['candidates', 'taken', 'closed', 'censored']].tolist() == [2, 1, 1, 0]
    assert counts.loc['joint', ['candidates', 'taken', 'closed', 'censored']].tolist() == [1, 1, 0, 1]
    assert receipt['coverage_summary']['complete_streams'] == 1
    assert receipt['coverage_summary']['partial_streams'] == 1
    assert receipt['coverage_summary']['no_recent_data_streams'] == 1
    assert receipt['report_builder_sha256'] == digest('yoyo/evaluation/spike_v128_recent_report.py')
    assert receipt['report_invoked_at']
    comparisons = pd.read_csv(tmp_path / 'report' / 'random_comparison.csv')
    assert {'summary', 'symbols'} <= set(comparisons.view)
    assert comparisons.loc[comparisons.view.eq('symbols'), 'symbol'].tolist() == ['PART']

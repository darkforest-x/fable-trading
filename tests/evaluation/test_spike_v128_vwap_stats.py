"""Synthetic integrity and aggregation checks for the SPIKE VWAP/TWAP study."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation import spike_v128_vwap_stats as stats


def test_early_cross_split_is_counted_but_not_mature_closed():
    frame = pd.DataFrame({
        'signal_close': pd.to_datetime(['2024-12-20T00:00Z', '2024-12-30T00:00Z', '2025-01-05T00:00Z'], utc=True),
        'exit_time': pd.to_datetime(['2024-12-21T00:00Z', '2025-01-02T00:00Z', '2025-01-06T00:00Z'], utc=True),
        'status': ['closed', 'closed', 'closed'], 'censored': [False, False, False],
        'trade_key': ['mature', 'cross', 'later'], 'net_r': [1., 2., 3.],
    })
    split = pd.Timestamp('2025-01-01T00:00Z')
    assert stats._mature_closed(frame, 'earlier', split).trade_key.tolist() == ['mature']
    assert stats._mature_closed(frame, 'all', split).trade_key.tolist() == ['mature', 'later']
    assert stats._crosscut(frame, split).trade_key.tolist() == ['cross']


def test_retention_reports_same_key_counts_for_large_winners():
    base = pd.DataFrame({'trade_key': ['a', 'b', 'c'], 'net_r': [6., 4., 11.]})
    gate = pd.DataFrame({'trade_key': ['b', 'c', 'd'], 'net_r': [4., 12., 11.]})
    row = stats._retention(base, gate)
    assert (row['samekey_retained'], row['samekey_lost'], row['samekey_new']) == (2, 1, 1)
    assert (row['baseline_ge5r'], row['samekey_retained_ge5r'], row['samekey_lost_ge5r'], row['samekey_new_ge5r']) == (2, 1, 1, 1)
    assert (row['baseline_ge10r'], row['samekey_retained_ge10r'], row['samekey_new_ge10r']) == (1, 1, 1)


def test_same_gate_control_rejects_without_redrawing():
    target = pd.DataFrame({
        'trade_key': ['a', 'b', 'c'], 'signal_close': pd.to_datetime(
            ['2025-01-06T00:00Z', '2025-01-13T00:00Z', '2025-01-20T00:00Z'], utc=True),
        'net_return': [.01, .02, .03],
    })
    controls = pd.DataFrame({
        'trade_key': ['a', 'b', 'c'], 'matched': [True, True, True], 'control_pool': ['baseline'] * 3,
        'control_exit_time': pd.to_datetime(['2025-01-07T00:00Z', '2025-01-14T00:00Z', '2025-01-21T00:00Z'], utc=True),
        'control_net_return': [0., 0., 0.], 'control_net_r': [0., 0., 0.],
        'control_censored': [False, False, False], 'control_vwap_near': [True, False, True],
    })
    cfg = {'statistics_seed': 123, 'bootstrap': 50}
    row = stats._control_row(target, controls, period='later', split=pd.Timestamp('2025-01-01T00:00Z'),
                             policy='vwap_near', feature='vwap', mode='same_gate', cfg=cfg)
    assert row['target_closed'] == row['draw_assigned'] == row['matched_draws'] == 3
    assert row['control_gate_pass_draws'] == 2
    assert row['control_gate_rejected'] == 1
    assert row['paired_closed'] == 2
    assert row['matched'] == 2


def _write_csv(path: Path, frame: pd.DataFrame) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0})
    return stats.digest(path)


def _make_synthetic_run(root: Path) -> tuple[Path, dict]:
    cfg = json.loads(stats.CONFIG.read_text())
    run = root / 'run'
    (run / 'streams').mkdir(parents=True)
    (run / 'features').mkdir()
    thresholds = {str(minutes): {
        'vwap': {'q50': 1., 'q10': .5, 'n': 100},
        'twap': {'q50': 1., 'q10': .5, 'n': 100},
    } for minutes in cfg['timeframes']}
    (run / 'thresholds.json').write_text(json.dumps(thresholds, sort_keys=True))
    identity = {
        'config': cfg, 'config_sha256': stats.digest(stats.CONFIG),
        'code': {'yoyo/evaluation/spike_v128_vwap_study.py': stats.digest('yoyo/evaluation/spike_v128_vwap_study.py'),
                 'yoyo/evaluation/spike_v128_vwap_stats.py': stats.digest('yoyo/evaluation/spike_v128_vwap_stats.py')},
        'symbols': ['BTCUSDT'], 'timeframes': [15, 60], 'inputs': {'BTCUSDT': 'input-hash'}, 'subset': True,
    }
    rid = stats.fingerprint(identity)
    receipts = {}
    signal_times = pd.to_datetime([
        '2024-12-20T00:00Z', '2024-12-30T00:00Z', '2025-01-06T00:00Z',
        '2025-01-13T00:00Z', '2025-01-20T00:00Z', '2025-02-03T00:00Z',
    ], utc=True)
    exits = pd.to_datetime([
        '2024-12-28T00:00Z', '2025-01-02T00:00Z', '2025-01-07T00:00Z',
        '2025-01-14T00:00Z', '2025-01-21T00:00Z', '2025-02-04T00:00Z',
    ], utc=True)
    vwap_dist = [.5, .5, 2., .5, 2., .5]
    twap_dist = [.5, 2., .5, 2., .5, 2.]
    net = [.01, -.01, .02, -.02, .03, .015]
    selected = {'baseline': [0, 1, 2, 3], 'vwap_near': [0, 1, 3, 5], 'twap_near': [0, 2, 4]}
    for minutes in cfg['timeframes']:
        key = f'BTCUSDT_{minutes}m'
        candidates, control_rows = [], []
        for i, signal in enumerate(signal_times):
            trade_key = f'{minutes}:{i}'
            candidates.append({
                'trade_key': trade_key, 'arm': 'v9_both', 'symbol': 'BTCUSDT', 'timeframe_min': minutes,
                'signal_i': i, 'signal_close': signal, 'side': -1, 'status': 'closed',
                'entry_time': signal + pd.Timedelta(minutes=minutes), 'exit_time': exits[i],
                'net_return': net[i], 'gross_return': net[i] + .002,
                'net_r': net[i] * 100, 'gross_r': net[i] * 100 + .2, 'censored': False,
                'vwap_distance': vwap_dist[i], 'twap_distance': twap_dist[i],
                'vwap_near': abs(vwap_dist[i]) <= 1., 'twap_near': abs(twap_dist[i]) <= 1.,
            })
            ctrl_vwap = [.5, .5, 2., .5, .5, .5][i]
            ctrl_twap = [.5, 2., .5, 2., .5, 2.][i]
            control_rows.append({
                'trade_key': trade_key, 'arm': 'v9_both', 'symbol': 'BTCUSDT', 'timeframe_min': minutes,
                'side': -1, 'matched': True, 'control_pool': 'baseline',
                'control_exit_time': exits[i] + pd.Timedelta(hours=1),
                'control_net_return': net[i] - .005, 'control_net_r': net[i] * 100 - .5,
                'control_censored': False, 'control_vwap_distance': ctrl_vwap,
                'control_twap_distance': ctrl_twap, 'control_vwap_near': ctrl_vwap <= 1.,
                'control_twap_near': ctrl_twap <= 1.,
            })
        candidate_frame = pd.DataFrame(candidates)
        control_frame = pd.DataFrame(control_rows)
        trade_parts, status_parts = [], []
        for policy, indices in selected.items():
            chosen = set(indices)
            for i, row in enumerate(candidates):
                status = row['status'] if i in chosen else ('skipped_in_position' if policy == 'baseline' else 'filtered_distance')
                status_parts.append({'trade_key': row['trade_key'], 'arm': row['arm'], 'symbol': row['symbol'],
                                     'timeframe_min': minutes, 'signal_close': row['signal_close'],
                                     'policy': policy, 'status': status})
                if i in chosen:
                    trade_parts.append(row | {'policy': policy,
                                              'evaluation_fold': 'earlier' if row['signal_close'] < pd.Timestamp('2025-01-01T00:00Z') else 'later'})
        tables = {
            'candidate_outcomes': candidate_frame,
            'controls': control_frame,
            'serial_trades': pd.DataFrame(trade_parts),
            'serial_statuses': pd.DataFrame(status_parts),
        }
        folder = run / 'streams' / key
        folder.mkdir(parents=True)
        file_hashes = {name + '.csv.gz': _write_csv(folder / (name + '.csv.gz'), frame)
                       for name, frame in tables.items()}
        feature_folder = run / 'features' / key
        feature_folder.mkdir(parents=True)
        feature_receipt = {'status': 'complete', 'run_identity': rid, 'input_sha256': 'input-hash'}
        (feature_folder / 'receipt.json').write_text(json.dumps(feature_receipt, sort_keys=True))
        receipt = {'status': 'complete', 'run_identity': rid, 'symbol': 'BTCUSDT', 'minutes': minutes,
                   'input_sha256': 'input-hash', 'baseline_parity': True,
                   'feature_receipt_sha256': stats.digest(feature_folder / 'receipt.json'),
                   'thresholds_sha256': stats.digest(run / 'thresholds.json'), 'files': file_hashes}
        (folder / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True))
        receipts[key] = stats.digest(folder / 'receipt.json')
    (run / 'identity.json').write_text(json.dumps(identity, sort_keys=True))
    manifest = {'complete': True, 'full_universe': False, 'errors': [], 'run_identity': rid,
                'thresholds_sha256': stats.digest(run / 'thresholds.json'), 'receipts': receipts}
    (run / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    return run, cfg


def test_aggregate_authenticates_receipts_and_reports_crosscut_controls_and_retention(tmp_path):
    run, _ = _make_synthetic_run(tmp_path)
    output = tmp_path / 'summary'
    summary = stats.aggregate(run, output)
    assert summary['complete'] and not summary['full_universe']
    assert summary['thresholds_sha256'] == stats.digest(run / 'thresholds.json')

    metrics = pd.read_csv(output / 'metrics.csv')
    base_early = metrics.query("timeframe_min == 15 and arm == 'v9_both' and policy == 'baseline' and period == 'earlier'").iloc[0]
    base_all = metrics.query("timeframe_min == 15 and arm == 'v9_both' and policy == 'baseline' and period == 'all'").iloc[0]
    gate_all = metrics.query("timeframe_min == 15 and arm == 'v9_both' and policy == 'vwap_near' and period == 'all'").iloc[0]
    assert (base_early.filled, base_early.closed, base_early.crosscut) == (2, 1, 1)
    assert (base_all.filled, base_all.closed, base_all.crosscut) == (4, 3, 1)
    assert (gate_all.samekey_retained, gate_all.samekey_lost, gate_all.samekey_new) == (2, 1, 1)
    assert gate_all.samekey_retained_ge3r >= 0

    controls = pd.read_csv(output / 'controls.csv')
    primary = controls.query("timeframe_min == 15 and arm == 'v9_both' and period == 'later' and policy == 'vwap_near' and control_mode == 'same_gate'").iloc[0]
    assert primary.target_closed == 2
    assert primary.draw_assigned == 2
    assert primary.paired_closed == 2
    assert primary.matched == 2
    assert len(summary['primary_tests']) == 4
    diagnostics = pd.read_csv(output / 'diagnostics.csv')
    q10 = diagnostics.query("diagnostic == 'frozen_early_q10_baseline_serial_later' and timeframe_min == 15 and feature == 'vwap'").iloc[0]
    assert (q10.baseline_serial_closed, q10.q10_closed, q10.same_gate_pairs) == (2, 1, 1)
    assert (output / 'summary.json').is_file()


def test_authentication_rejects_changed_threshold_hash(tmp_path):
    run = tmp_path / 'bad'
    run.mkdir()
    cfg = json.loads(stats.CONFIG.read_text())
    identity = {'config': cfg, 'config_sha256': stats.digest(stats.CONFIG),
                'code': {'yoyo/evaluation/spike_v128_vwap_stats.py': stats.digest('yoyo/evaluation/spike_v128_vwap_stats.py')},
                'symbols': ['BTCUSDT'], 'timeframes': [15, 60], 'inputs': {'BTCUSDT': 'input'}, 'subset': True}
    keys = stats._expected_streams(identity['symbols'], identity['timeframes'])
    (run / 'identity.json').write_text(json.dumps(identity, sort_keys=True))
    (run / 'thresholds.json').write_text('{}')
    manifest = {'complete': True, 'errors': [], 'full_universe': False,
                'run_identity': stats.fingerprint(identity), 'thresholds_sha256': 'wrong',
                'receipts': {key: 'unused' for key in keys}}
    (run / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    with pytest.raises(ValueError, match='threshold file hash mismatch'):
        stats.authenticate_run(run)


def test_aggregate_refuses_to_overwrite_existing_output(tmp_path):
    run, _ = _make_synthetic_run(tmp_path)
    output = tmp_path / 'summary'
    output.mkdir()
    with pytest.raises(ValueError, match='output exists'):
        stats.aggregate(run, output)

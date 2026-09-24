"""Test causal session price distance on receipt-bound SPIKE V12.8 outcomes.

Sources: TradingView's VWAP formula (HLC3 times base volume / base volume),
and the existing V12.8 long-history fixed-entry engine. Session starts at UTC
00:00. Features use high/low/close/volume through the signal bar only. TWAP
uses identical complete, equally spaced chart bars with unit volume weights.
ATR is the unchanged SPIKE SMA-seeded SMMA14 of true range. Missing session
bars invalidate that day's average; missing volume is never filled with zero.

Only entry eligibility changes. Every candidate's authenticated cached exit
is retained, including original raw reverse exits, initial risk and 20bp cost.
Each gate replays serial occupancy, with common baseline carry at each fold
boundary. Thresholds use earlier candidate features, never earlier outcomes.
This is retrospective research on a frozen Binance universe, not native Pine
parity or an OKX performance claim. No production defaults are changed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import _smma
from yoyo.evaluation.spike_v126_htf_recheck import complete_bars
from yoyo.evaluation.spike_v128_recent import digest, dump
from yoyo.evaluation.spike_v128_expansion_entry import assert_parity
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v128-vwap-20260924-v1')
CONFIG = EXP / 'config.json'
TEST = Path('tests/evaluation/test_spike_v128_vwap_study.py')
POLICIES = ('baseline', 'vwap_near', 'twap_near')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read_table(path):
    try:
        frame = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        frame = pd.DataFrame()
    # Parent empty streams predate typed empty-table contracts. Add headers,
    # never observations; populated malformed streams still fail normally.
    if frame.empty:
        for col in ('trade_key', 'arm', 'symbol', 'timeframe_min', 'signal_i', 'signal_close', 'side',
                    'status', 'policy', 'blocking_trade', 'entry_time', 'entry_price', 'initial_stop',
                    'initial_risk', 'exit_i', 'exit_time', 'exit_price', 'exit_reason', 'gross_return',
                    'net_return', 'gross_r', 'net_r', 'censored', 'control_pool', 'matched',
                    'control_sig', 'control_signal_close', 'control_exit_time', 'control_net_return',
                    'control_net_r', 'control_censored'):
            if col not in frame:
                frame[col] = pd.Series(dtype=object)
    for col in ('signal_close', 'entry_time', 'exit_time', 'control_signal_close', 'control_exit_time'):
        if col in frame:
            frame[col] = pd.to_datetime(frame[col], utc=True)
    for col in ('censored', 'control_censored', 'matched'):
        if col in frame:
            frame[col] = frame[col].astype(str).str.lower().isin(('true', '1', '1.0'))
    return frame


def session_features(bars, minutes):
    """Use only current/prior HLCV in the UTC session, and causal SMMA14 ATR.

    A bar is timestamped at OPEN: the 23:45 bar belongs to the old session,
    even though it closes at midnight. Partial first days and all bars after
    an intraday gap are unknown until the next complete midnight start.
    """
    if not bars.index.is_monotonic_increasing or not bars.index.is_unique:
        raise ValueError('unordered or duplicate chart bars')
    if bars.index.tz is None or str(bars.index.tz) != 'UTC':
        raise ValueError('UTC chart index required')
    if bars.empty:
        return pd.DataFrame(index=bars.index, columns=['atr', 'vwap', 'twap', 'vwap_distance', 'twap_distance', 'session_valid'])
    day = bars.index.normalize()
    ordinal = pd.Series(np.arange(len(bars)), index=bars.index).groupby(day).cumcount()
    expected = np.asarray((bars.index-day).total_seconds()/60/minutes, int)
    prefix_complete = ordinal.to_numpy() == expected
    prices = bars[['high', 'low', 'close']].to_numpy(float)
    valid_input = np.isfinite(prices).all(axis=1) & np.isfinite(bars.volume) & (bars.volume >= 0)
    invalid_before = pd.Series(~valid_input, index=bars.index).groupby(day).cumsum().gt(0)
    valid = prefix_complete & ~invalid_before.to_numpy()
    typical = (bars.high + bars.low + bars.close)/3
    volume = bars.volume.groupby(day).cumsum()
    vwap = ((typical*bars.volume).groupby(day).cumsum()/volume.where(volume > 0)).where(valid)
    twap = (typical.groupby(day).cumsum()/(ordinal+1)).where(valid)
    tr = pd.concat([bars.high-bars.low, (bars.high-bars.close.shift()).abs(),
                    (bars.low-bars.close.shift()).abs()], axis=1).max(axis=1)
    atr = _smma(tr, 14)
    divisor = atr.where(atr > 0)
    return pd.DataFrame({'atr': atr, 'vwap': vwap, 'twap': twap,
                         'vwap_distance': (bars.close-vwap)/divisor,
                         'twap_distance': (bars.close-twap)/divisor,
                         'session_valid': valid}, index=bars.index)


def attach_features(rows, feature, minutes, *, controls=False):
    """Join by both saved bar ordinal and visible close clock; never forward-fill."""
    result = rows.copy()
    index_col, clock_col = ('control_sig', 'control_signal_close') if controls else ('signal_i', 'signal_close')
    prefix = 'control_' if controls else ''
    for name in ('vwap_distance', 'twap_distance'):
        result[prefix+name] = np.nan
    if result.empty:
        return result
    present = result[index_col].notna()
    pos = result.loc[present, index_col].to_numpy(float)
    if not np.equal(pos, np.floor(pos)).all() or (pos < 0).any() or (pos >= len(feature)).any():
        raise ValueError('invalid cached bar index')
    pos = pos.astype(int)
    expected = feature.index[pos]+pd.Timedelta(minutes=minutes)
    observed = pd.DatetimeIndex(result.loc[present, clock_col])
    if not expected.equals(observed):
        raise ValueError('cached event/feature clocks differ')
    for name in ('vwap_distance', 'twap_distance'):
        result.loc[present, prefix+name] = feature[name].to_numpy()[pos]
    return result


def fit_thresholds(frames, cfg):
    """Fit only known earlier RAW candidate distances, including unfilled ones.

    No returns, censor status or direction select the quantile sample. Each
    timeframe gets one pooled q50 cutoff and q10 diagnostic across symbols.
    Joint candidates use that same raw-signal threshold, not another search.
    """
    values = {m: {f: [] for f in ('vwap', 'twap')} for m in cfg['timeframes']}
    for frame in frames:
        if frame.empty:
            continue
        mask = (frame.arm.eq('v9_both') & (frame.signal_close >= pd.Timestamp(cfg['analysis_start']))
                & (frame.signal_close < pd.Timestamp(cfg['split'])))
        earlier = frame.loc[mask]
        for minutes in cfg['timeframes']:
            part = earlier[earlier.timeframe_min.eq(minutes)]
            for name in ('vwap', 'twap'):
                a = part[name+'_distance'].abs().to_numpy(float)
                values[minutes][name].extend(a[np.isfinite(a)])
    thresholds = {}
    for minutes in cfg['timeframes']:
        thresholds[str(minutes)] = {}
        for name in ('vwap', 'twap'):
            a = np.asarray(values[minutes][name])
            if len(a) < 100:
                raise ValueError(f'insufficient earlier feature support {minutes}/{name}: {len(a)}')
            thresholds[str(minutes)][name] = {'q50': float(np.quantile(a, .5)),
                                              'q10': float(np.quantile(a, .1)), 'n': len(a)}
    return thresholds


def add_gates(frame, thresholds, minutes, *, controls=False):
    result = frame.copy()
    prefix = 'control_' if controls else ''
    for name in ('vwap', 'twap'):
        value = pd.to_numeric(result[prefix+name+'_distance'], errors='coerce')
        result[prefix+name+'_near'] = np.isfinite(value) & value.abs().le(thresholds[str(minutes)][name]['q50'])
    return result


def select_serial(rows, policy, frame_length, carry=(-1, None)):
    """Apply one close-time gate before serial occupancy; exits remain frozen."""
    flat_from, holder = carry
    trades, statuses = [], []
    for row in sorted(rows, key=lambda r: int(r['signal_i'])):
        r = dict(row, policy=policy)
        if policy != 'baseline' and not bool(r[policy]):
            status = 'filtered_unknown' if pd.isna(r[policy.replace('_near', '_distance')]) else 'filtered_distance'
        elif int(r['signal_i']) < flat_from:
            status = 'skipped_in_position'
        else:
            status = r['status']
            if pd.notna(r.get('exit_i')):
                trades.append(r)
                holder = r['trade_key']
                flat_from = frame_length+1 if status == 'censored_boundary' else int(r['exit_i'])
        statuses.append({k: r.get(k) for k in ('trade_key', 'arm', 'symbol', 'timeframe_min', 'signal_i',
                                              'signal_close', 'side', 'policy', 'evaluation_fold')} |
                        {'status': status, 'blocking_trade': holder if status == 'skipped_in_position' else None})
    return trades, statuses


def carry_at(parent_trades, cut, frame_length):
    prior = parent_trades[parent_trades.signal_close < pd.Timestamp(cut)].sort_values('signal_i')
    if prior.empty:
        return -1, None
    last = prior.iloc[-1]
    return (frame_length+1 if last.status == 'censored_boundary' else int(last.exit_i)), last.trade_key


def validate_run(path):
    path = Path(path)
    ident, manifest = [json.loads((path/name).read_text()) for name in ('identity.json', 'manifest.json')]
    if manifest.get('errors') or not manifest.get('complete') or ident.get('subset'):
        raise ValueError('parent run is incomplete')
    if fingerprint(ident) != manifest['run_identity']:
        raise ValueError('parent run identity mismatch')
    return ident, manifest


def verified_receipt(folder, manifest, *, input_sha=None):
    key = folder.name
    if digest(folder/'receipt.json') != manifest['receipts'][key]:
        raise ValueError(f'changed stream receipt {key}')
    receipt = json.loads((folder/'receipt.json').read_text())
    if receipt['run_identity'] != manifest['run_identity'] or receipt['status'] != 'complete':
        raise ValueError('invalid stream identity/status')
    if input_sha is not None and receipt['input_sha256'] != input_sha:
        raise ValueError('stream input hash differs')
    for name, sha in receipt['files'].items():
        if digest(folder/name) != sha:
            raise ValueError(f'changed stream artifact {folder/name}')
    return receipt


def feature_worker(task):
    symbol, source, cfg, output, ident, cached_manifest, baseline_manifest = task
    started = time.perf_counter()
    path = Path(source['path'])
    if digest(path) != source['sha256']:
        raise ValueError(f'input changed {symbol}')
    raw = pd.read_csv(path, usecols=['ts', 'open', 'high', 'low', 'close', 'volume'])
    ts = raw.pop('ts').to_numpy()
    if not (np.diff(ts) > 0).all() or (ts % 300000 != 0).any():
        raise ValueError('invalid five-minute input clock')
    base = raw.set_axis(pd.DatetimeIndex(pd.to_datetime(ts, unit='ms', utc=True)))
    receipts = []
    for minutes in cfg['timeframes']:
        key = f'{symbol}_{minutes}m'
        cached = Path(cfg['cached_run'])/'streams'/key
        original = Path(cfg['baseline_run'])/'streams'/key
        cr = verified_receipt(cached, cached_manifest, input_sha=source['sha256'])
        br = verified_receipt(original, baseline_manifest, input_sha=source['sha256'])
        if cr['parent_receipt_sha256'] != digest(original/'receipt.json'):
            raise ValueError('cached outcomes refer to another baseline')
        bars, partial = complete_bars(base, minutes)
        if len(bars) != br['summary']['chart_bars_total']:
            raise ValueError('chart sample differs from parent')
        feature = session_features(bars, minutes)
        outcomes = read_table(cached/'candidate_outcomes.csv.gz')
        outcomes = outcomes[outcomes.policy.eq('baseline')].copy()
        controls = read_table(cached/'controls.csv.gz')
        controls = controls[controls.control_pool.eq('baseline')].copy()
        decisions = read_table(original/'decisions.csv.gz')
        if len(outcomes) != len(decisions) or outcomes.trade_key.duplicated().any():
            raise ValueError('cached candidate coverage mismatch')
        if len(decisions):
            assert_parity(outcomes, decisions, ['arm', 'signal_i'], ['side', 'signal_close'])
        if controls.trade_key.duplicated().any():
            raise ValueError('multiple random draws per candidate')
        outcomes = attach_features(outcomes, feature, minutes)
        controls = attach_features(controls, feature, minutes, controls=True)
        folder = Path(output)/'features'/key
        if folder.exists():
            raise ValueError('refusing to overwrite feature stream')
        folder.mkdir(parents=True)
        for name, table in [('candidate_outcomes', outcomes), ('controls', controls)]:
            table.to_csv(folder/(name+'.csv.gz'), index=False, compression={'method': 'gzip', 'mtime': 0})
        receipt = {'status': 'complete', 'symbol': symbol, 'minutes': minutes, 'run_identity': ident,
                   'input_sha256': source['sha256'], 'cached_receipt_sha256': digest(cached/'receipt.json'),
                   'baseline_receipt_sha256': digest(original/'receipt.json'), 'chart_bars': len(bars),
                   'partial_chart_buckets': partial, 'feature_valid_bars': int(feature.session_valid.sum()),
                   'feature_known_vwap': int(feature.vwap_distance.notna().sum()),
                   'files': {n+'.csv.gz': digest(folder/(n+'.csv.gz')) for n in ('candidate_outcomes', 'controls')},
                   'wall_seconds': time.perf_counter()-started}
        dump(folder/'receipt.json', receipt)
        receipts.append(receipt)
    if digest(path) != source['sha256']:
        raise ValueError('input changed during feature calculation')
    return receipts


def replay_stream(task):
    key, cfg, output, identity_hash, thresholds, feature_manifest, baseline_manifest = task
    folder = Path(output)/'features'/key
    receipt = verified_receipt(folder, feature_manifest)
    minutes = receipt['minutes']
    outcomes = add_gates(read_table(folder/'candidate_outcomes.csv.gz'), thresholds, minutes)
    controls = add_gates(read_table(folder/'controls.csv.gz'), thresholds, minutes, controls=True)
    original = Path(cfg['baseline_run'])/'streams'/key
    verified_receipt(original, baseline_manifest)
    old_trades = read_table(original/'trades.csv.gz')
    old_status = read_table(original/'statuses.csv.gz')
    old_controls = read_table(original/'controls.csv.gz')
    if len(old_trades):
        cached_fills = outcomes[outcomes.trade_key.isin(old_trades.trade_key)]
        assert_parity(cached_fills, old_trades, ['trade_key'],
                      ['side', 'entry_time', 'entry_price', 'initial_stop', 'initial_risk', 'exit_i',
                       'exit_time', 'exit_price', 'exit_reason', 'gross_return', 'net_return', 'net_r', 'censored'])
        assert_parity(controls[controls.trade_key.isin(old_trades.trade_key)], old_controls, ['trade_key'],
                      ['matched', 'control_sig', 'control_signal_close', 'control_exit_time', 'control_net_return'])
    trades, statuses = [], []
    folds = [('earlier', cfg['analysis_start'], cfg['split']), ('later', cfg['split'], cfg['end'])]
    for arm in ('v9_both', 'joint'):
        original_arm = old_trades[old_trades.arm.eq(arm)]
        for fold, start, end in folds:
            carry = carry_at(original_arm, start, receipt['chart_bars'])
            rows = outcomes[(outcomes.arm.eq(arm)) & (outcomes.signal_close >= pd.Timestamp(start))
                            & (outcomes.signal_close < pd.Timestamp(end))].copy()
            rows['evaluation_fold'] = fold
            for policy in POLICIES:
                t, s = select_serial(rows.to_dict('records'), policy, receipt['chart_bars'], carry)
                trades.extend(t)
                statuses.extend(s)
    columns = list(outcomes.columns)+['evaluation_fold']
    tframe = pd.DataFrame(trades) if trades else pd.DataFrame(columns=columns)
    scolumns = ['trade_key', 'arm', 'symbol', 'timeframe_min', 'signal_i', 'signal_close', 'side', 'policy',
                'evaluation_fold', 'status', 'blocking_trade']
    sframe = pd.DataFrame(statuses, columns=scolumns)
    expected = old_trades[(old_trades.signal_close >= pd.Timestamp(cfg['analysis_start']))
                          & (old_trades.signal_close < pd.Timestamp(cfg['end']))]
    assert_parity(tframe[tframe.policy.eq('baseline')], expected, ['trade_key'],
                  ['entry_time', 'exit_time', 'initial_risk', 'net_return', 'net_r', 'censored'])
    expected_s = old_status[(old_status.signal_close >= pd.Timestamp(cfg['analysis_start']))
                            & (old_status.signal_close < pd.Timestamp(cfg['end']))]
    assert_parity(sframe[sframe.policy.eq('baseline')], expected_s, ['arm', 'signal_i'], ['status', 'blocking_trade'])
    final = Path(output)/'streams'/key
    final.mkdir(parents=True, exist_ok=False)
    tables = {'candidate_outcomes': outcomes, 'controls': controls, 'serial_trades': tframe, 'serial_statuses': sframe}
    for name, table in tables.items():
        table.to_csv(final/(name+'.csv.gz'), index=False, compression={'method': 'gzip', 'mtime': 0})
    result = {'status': 'complete', 'run_identity': identity_hash, 'symbol': receipt['symbol'], 'minutes': minutes,
              'input_sha256': receipt['input_sha256'], 'feature_receipt_sha256': digest(folder/'receipt.json'),
              'baseline_parity': True, 'thresholds_sha256': digest(Path(output)/'thresholds.json'),
              'files': {n+'.csv.gz': digest(final/(n+'.csv.gz')) for n in tables}}
    dump(final/'receipt.json', result)
    return result


def run(output, workers=4, symbols=None):
    cfg = json.loads(CONFIG.read_text())
    if cfg['round_trip_cost'] != .002 or cfg['timeframes'] != [15, 60]:
        raise ValueError('frozen cost/timeframes changed')
    roots = (Path(__file__), Path('yoyo/evaluation/spike_v128_vwap_stats.py'))
    sources = tuple(dict.fromkeys((*roots, TEST, Path('tests/evaluation/test_spike_v128_vwap_stats.py'), CONFIG,
                                  EXP/'PROJECT_PLAN.md', *_local_transitive_python(roots))))
    if not _committed(sources):
        raise ValueError('commit study, statistics, tests and plan before reading outcomes')
    pi, pm = validate_run(cfg['baseline_run'])
    ci, cm = validate_run(cfg['cached_run'])
    for parent_identity in (pi, ci):
        for path, sha in parent_identity['code'].items():
            if digest(Path(path)) != sha:
                raise ValueError(f'parent source changed: {path}')
    if ci['parent_identity_sha256'] != digest(Path(cfg['baseline_run'])/'identity.json'):
        raise ValueError('cached parent identity differs')
    if ci['parent_manifest_sha256'] != digest(Path(cfg['baseline_run'])/'manifest.json'):
        raise ValueError('cached parent manifest differs')
    if pi['inputs'] != ci['inputs']:
        raise ValueError('cached input selection differs')
    manifest_path = Path(pi['input_manifest'])
    if digest(manifest_path) != pi['input_manifest_sha256']:
        raise ValueError('input manifest changed')
    meta = pi['symbol_meta_source']
    if digest(Path(meta['path'])) != meta['sha256']:
        raise ValueError('frozen metadata changed')
    inputs = {r['symbol']: r for r in json.loads(manifest_path.read_text())['streams']}
    selected = sorted(pi['symbols'] if symbols is None else symbols)
    if not selected or not set(selected) <= set(pi['symbols']):
        raise ValueError('invalid symbol subset')
    for s in selected:
        if inputs[s]['sha256'] != pi['inputs'][s]:
            raise ValueError('input hash contract differs')
    identity = {'config': cfg, 'config_sha256': digest(CONFIG), 'code': {str(p): digest(p) for p in sources},
                'baseline_identity_sha256': digest(Path(cfg['baseline_run'])/'identity.json'),
                'cached_identity_sha256': digest(Path(cfg['cached_run'])/'identity.json'),
                'baseline_manifest_sha256': digest(Path(cfg['baseline_run'])/'manifest.json'),
                'cached_manifest_sha256': digest(Path(cfg['cached_run'])/'manifest.json'),
                'input_manifest': str(manifest_path), 'input_manifest_sha256': digest(manifest_path),
                'inputs': {s: pi['inputs'][s] for s in selected}, 'symbols': selected,
                'timeframes': cfg['timeframes'], 'subset': symbols is not None}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    dump(output/'identity.json', identity)
    rid = fingerprint(identity)
    tasks = [(s, inputs[s], cfg, str(output), rid, cm, pm) for s in selected]
    receipts, errors = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(feature_worker, t): t[0] for t in tasks}
        for n, f in enumerate(as_completed(pending), 1):
            try:
                receipts.extend(f.result())
            except Exception as exc:
                errors.append({'symbol': pending[f], 'error': repr(exc)})
            if n == 1 or n % 40 == 0 or n == len(tasks):
                print(json.dumps({'phase': 'features', 'done': n, 'total': len(tasks), 'errors': len(errors)}), flush=True)
    fm = {'complete': not errors, 'errors': errors, 'run_identity': rid,
          'receipts': {f"{r['symbol']}_{r['minutes']}m": digest(output/'features'/f"{r['symbol']}_{r['minutes']}m"/'receipt.json') for r in receipts}}
    dump(output/'feature_manifest.json', fm)
    if errors:
        raise RuntimeError(f'feature errors retained: {len(errors)}')
    thresholds = fit_thresholds((read_table(output/'features'/key/'candidate_outcomes.csv.gz')
                                 for key in sorted(fm['receipts'])), cfg)
    dump(output/'thresholds.json', thresholds)
    tasks = [(key, cfg, str(output), rid, thresholds, fm, pm) for key in sorted(fm['receipts'])]
    receipts, errors = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(replay_stream, t): t[0] for t in tasks}
        for n, f in enumerate(as_completed(pending), 1):
            try:
                receipts.append(f.result())
            except Exception as exc:
                errors.append({'stream': pending[f], 'error': repr(exc)})
            if n == 1 or n % 100 == 0 or n == len(tasks):
                print(json.dumps({'phase': 'serial', 'done': n, 'total': len(tasks), 'errors': len(errors)}), flush=True)
    result = {'complete': not errors, 'full_universe': symbols is None, 'errors': errors, 'run_identity': rid,
              'thresholds_sha256': digest(output/'thresholds.json'),
              'receipts': {f"{r['symbol']}_{r['minutes']}m": digest(output/'streams'/f"{r['symbol']}_{r['minutes']}m"/'receipt.json') for r in receipts}}
    dump(output/'manifest.json', result)
    if errors:
        raise RuntimeError(f'serial errors retained: {len(errors)}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--symbols', nargs='+')
    args = parser.parse_args()
    run(args.output, args.workers, args.symbols)

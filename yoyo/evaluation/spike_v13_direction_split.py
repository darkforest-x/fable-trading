"""Compare frozen 15m V13 retest directions without changing price-path outcomes.

Owner 2026-10-16 asks whether short-only improves the historical 15m strategy.
The only admission feature is the candidate's already-known side. Read archived
candidate signal_i/anchor_i/side in chronological order, retaining the original
initial stop, raw-opposite exits, 2R/4ATR trail, gaps and 20bp cost. An occupied
confirmation expires. Replay the warmup before applying the analysis window;
future exit_i is only the precomputed lifecycle of an admitted position, never
an admission ranking. Exact two-sided parity is required before any comparison.

Controls are original same-symbol/side/UTC-anchor-week/volatility-bin draws with
the same retest policy, not redrawn or a separate tradable random portfolio.
Reuse the prior summary's matched-control eligibility (including its Sunday
anchor exclusion), Beijing entry-week blocks, time split and calendar semantics.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_entry_clock as clock
from yoyo.evaluation.spike_v128_expansion_report import holm
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v13-15m-direction-20261016-v1')
CONFIG = EXP / 'config.json'
TEST = Path('tests/evaluation/test_spike_v13_direction_split.py')
COLS = ['trade_key', 'arm', 'policy', 'symbol', 'timeframe_min', 'side',
        'signal_i', 'anchor_i', 'entry_i', 'exit_i', 'entry_time', 'exit_time',
        'entry_price', 'initial_stop', 'initial_risk', 'status', 'net_r',
        'gross_return', 'net_return']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_columns(path):
    available = pd.read_csv(path, nrows=0).columns
    rows = pd.read_csv(path, usecols=[k for k in COLS if k in available], low_memory=False)
    if rows.empty:
        return pd.DataFrame(columns=COLS)
    if set(COLS) - set(rows):
        raise ValueError(f'nonempty ledger missing columns: {path}')
    return rows[rows.arm.eq('v9_both') & rows.policy.eq('retest')].copy()


def select_direction(candidates, side=None):
    """One-position chronology from the archive boundary, with no outcome sorting."""
    rows = candidates[candidates.exit_i.notna()]
    if side is not None:
        rows = rows[rows.side.eq(side)]
    if not rows.status.isin(['closed', 'censored_boundary', 'censored_gap']).all():
        raise ValueError('execution outcome has unexpected status')
    selected, flat_from = [], -1
    for r in rows.sort_values(['signal_i', 'anchor_i'], kind='stable').itertuples():
        if r.signal_i < flat_from:
            continue
        selected.append(r.Index)
        flat_from = math.inf if r.status == 'censored_boundary' else r.exit_i
    return candidates.loc[selected].copy()


def check_parity(replayed, original):
    if replayed.trade_key.duplicated().any() or original.trade_key.duplicated().any():
        raise ValueError('duplicate event identity')
    a, b = [x.set_index('trade_key').sort_index() for x in (replayed, original)]
    if not a.index.equals(b.index):
        raise ValueError('two-sided occupancy differs from parent')
    for key in ['status', 'side', 'entry_time', 'exit_time']:
        if a[key].fillna('').tolist() != b[key].fillna('').tolist():
            raise ValueError(f'parent parity failed: {key}')
    for key in ['entry_price', 'initial_stop', 'initial_risk', 'exit_i', 'net_return', 'net_r']:
        np.testing.assert_allclose(a[key].astype(float), b[key].astype(float), rtol=0, atol=1e-10, equal_nan=True)


def in_window(frame, cfg):
    return frame[(frame.entry_time >= pd.Timestamp(cfg['analysis_start'])) &
                 (frame.entry_time < pd.Timestamp(cfg['end']))].copy()


def load_stream(job):
    symbol, cfg, identity, manifest = job
    root = Path(cfg['source_run']) / 'streams' / f'{symbol}_15m'
    receipt_path = root / 'receipt.json'
    if digest(receipt_path) != manifest['receipts'][root.name]:
        raise ValueError(f'receipt changed: {root}')
    receipt = json.loads(receipt_path.read_text())
    if (receipt['run_identity'] != manifest['run_identity'] or receipt['status'] != 'complete'
            or receipt['input_sha256'] != identity['inputs'][symbol]
            or receipt['minutes'] != 15 or receipt['symbol'] != symbol
            or not receipt['summary']['parent_parity']):
        raise ValueError(f'invalid source receipt: {symbol}')
    hashes = {}
    for name in ['candidate_outcomes.csv.gz', 'serial_trades.csv.gz', 'controls.csv.gz']:
        hashes[name] = digest(root / name)
        if hashes[name] != receipt['files'][name]:
            raise ValueError(f'ledger changed: {root / name}')
    candidates, original = [read_columns(root / n) for n in ['candidate_outcomes.csv.gz', 'serial_trades.csv.gz']]
    both = select_direction(candidates)
    check_parity(both, original)
    selected = []
    for name, side in [('both', None), ('long_only', 1), ('short_only', -1)]:
        f = both.copy() if side is None else select_direction(candidates, side)
        f['admission'] = name
        selected.append(f)
    controls = pd.read_csv(root / 'controls.csv.gz', low_memory=False)
    if len(controls):
        controls = controls[controls.arm.eq('v9_both')].copy()
    return pd.concat(selected, ignore_index=True), controls, {root.name: hashes}


def summary_row(frame, controls, period, cfg, **labels):
    split = pd.Timestamp(cfg['split'])
    target, random = frame, controls
    if period.startswith('year_'):
        year = int(period[5:])
        target = target[target.entry_time.dt.tz_convert(cfg['timezone']).dt.year.eq(year)]
        random = random[random.entry_time.dt.tz_convert(cfg['timezone']).dt.year.eq(year)]
        grouping = 'all'
    else:
        grouping = period
    stats = clock.describe(clock.cohort(target, grouping, split), cfg)
    pairs = clock.pair_rows(target, random, 'overall', grouping, split)
    # pair_rows deliberately projects only outcome columns; direction is checked
    # against source keys globally before this function instead.
    compared = clock.comparator(pairs, cfg, test=period in ['earlier', 'later'])
    if len(pairs):
        compared['paired_target_mean_net_r'] = float(pairs.net_r.mean())
        compared['random_mean_net_r'] = float(pairs.net_r_random.mean())
    return labels | {'period': period} | stats | compared


def direction_contrast(frame, cfg):
    """Paired entry-week bootstrap of short minus long per-trade mean net R."""
    out = []
    for period in ['all', 'earlier', 'later']:
        rows = clock.cohort(frame, period, pd.Timestamp(cfg['split']))
        rows = rows[rows.status.eq('closed')]
        groups = rows.groupby(['week', 'side']).net_r.agg(['sum', 'count'])
        sums = groups['sum'].unstack(fill_value=0).reindex(columns=[-1, 1], fill_value=0)
        counts = groups['count'].unstack(fill_value=0).reindex(columns=[-1, 1], fill_value=0)
        rng = np.random.default_rng(cfg['statistics_seed'])
        choices = rng.integers(0, len(sums), (cfg['bootstrap'], len(sums)))
        means = sums.to_numpy()[choices].sum(axis=1) / counts.to_numpy()[choices].sum(axis=1)
        delta = means[:, 0] - means[:, 1]
        actual = sums.sum() / counts.sum()
        out.append({'period': period, 'short_minus_long_mean_net_r': float(actual[-1] - actual[1]),
                    'ci_low_r': float(np.quantile(delta, .025)), 'ci_high_r': float(np.quantile(delta, .975))})
    return out


def run(config, output):
    cfg = json.loads(config.read_text())
    declared = (Path(__file__), TEST, config, Path(clock.__file__),
                Path('yoyo/evaluation/spike_v128_expansion_report.py'))
    if not _committed(declared):
        raise ValueError('commit builder, tests and config before analysis')
    if output.exists():
        raise ValueError('refusing to overwrite an existing result directory')
    root = Path(cfg['source_run'])
    identity = json.loads((root / 'identity.json').read_text())
    manifest = json.loads((root / 'manifest.json').read_text())
    if (digest(root / 'manifest.json') != cfg['source_manifest_sha256']
            or hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest() != manifest['run_identity']
            or not manifest['complete'] or manifest['errors'] or identity['subset']
            or len(identity['symbols']) != 638 or identity['config']['round_trip_cost'] != .002):
        raise ValueError('source identity or completeness mismatch')
    jobs = [(s, cfg, identity, manifest) for s in identity['symbols']]
    pieces, control_pieces, hashes = [], [], {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        for i, (part, control, stream_hash) in enumerate(executor.map(load_stream, jobs), 1):
            pieces.append(part)
            if len(control):
                control_pieces.append(control)
            hashes.update(stream_hash)
            if i % 100 == 0:
                print(f'Authenticated and replayed {i}/638 streams', flush=True)
    trades = in_window(clock.clock(pd.concat(pieces, ignore_index=True), cfg['timezone']), cfg)
    raw_controls = pd.concat(control_pieces, ignore_index=True)
    controls = clock.prepare_controls(raw_controls, cfg['timezone'])
    controls = in_window(controls[controls.policy.eq('retest')], cfg)
    direction_check = trades[['trade_key', 'side']].drop_duplicates().merge(
        controls[['trade_key', 'side']], on='trade_key', suffixes=('', '_control'), validate='one_to_one')
    if not direction_check.side.eq(direction_check.side_control).all():
        raise ValueError('matched control side differs from target')
    closed = trades[trades.status.eq('closed')]
    np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12, rtol=0)
    np.testing.assert_allclose(closed.net_return * closed.entry_price / closed.initial_risk, closed.net_r, atol=1e-9)
    groups = {name: trades[trades.admission.eq(name)] for name in ['both', 'long_only', 'short_only']}
    groups['old_longs'] = groups['both'][groups['both'].side.eq(1)]
    groups['old_shorts'] = groups['both'][groups['both'].side.eq(-1)]
    summary = pd.DataFrame([summary_row(f, controls, period, cfg, admission=name)
                            for name, f in groups.items()
                            for period in ['all', 'earlier', 'later', 'cross', 'year_2023', 'year_2024', 'year_2025', 'year_2026']])
    # Four prespecified primary direction x split comparisons, not yearly winners.
    primary = summary.admission.isin(['long_only', 'short_only']) & summary.period.isin(['earlier', 'later'])
    summary.loc[primary, 'holm_p_four'] = holm(summary.loc[primary, 'p_one_sided'].to_numpy(float))
    prior_path = Path(cfg['source_summary'])
    if digest(prior_path) != cfg['source_summary_sha256']:
        raise ValueError('prior aggregate changed')
    prior = pd.read_csv(prior_path)
    expected = prior[(prior.timeframe_min == 15) & prior.arm.eq('v9_both') & prior.policy.eq('retest') &
                     prior.kind.eq('overall') & prior.period.eq('all')].iloc[0]
    actual = summary[summary.admission.eq('both') & summary.period.eq('all')].iloc[0]
    for key in ['taken', 'closed', 'censored', 'win_rate', 'sum_net_r', 'pf_r', 'matched', 'mean_excess_bp']:
        np.testing.assert_allclose(actual[key], expected[key], atol=1e-9, rtol=0)
    changes = {}
    for side, name in [(1, 'long_only'), (-1, 'short_only')]:
        before = set(groups['both'].loc[groups['both'].side.eq(side), 'trade_key'])
        after = set(groups[name].trade_key)
        changes[name] = {'new_entries': len(after - before), 'displaced_entries': len(before - after)}
    result = {'occupancy_changes': changes, 'direction_mean_contrast': direction_contrast(groups['both'], cfg)}
    output.mkdir(parents=True)
    summary.to_csv(output / 'summary.csv', index=False)
    trades.to_csv(output / 'trades.csv.gz', index=False, compression={'method': 'gzip', 'mtime': 0})
    controls.to_csv(output / 'matched_controls.csv.gz', index=False, compression={'method': 'gzip', 'mtime': 0})
    (output / 'comparison.json').write_text(json.dumps(result, indent=2))
    receipt = {'experiment_id': cfg['experiment_id'], 'generated_at': datetime.now(timezone.utc).isoformat(),
               'builder_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
               'config': cfg, 'code': {str(p): digest(p) for p in declared}, 'source_hashes': hashes,
               'streams': len(hashes), 'parent_aggregate_parity': True, **result,
               'metric_scope': 'No classifier/ranking: AUC and top-decile metrics do not apply. Direction is the single-feature gate.',
               'files': {p.name: digest(p) for p in output.iterdir()},
               'training_eligible': False, 'production_eligible': False}
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2))
    print(summary[summary.period.eq('all')][['admission', 'closed', 'win_rate', 'mean_net_r', 'sum_net_r', 'pf_r', 'matched', 'mean_excess_bp']].to_string(index=False))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.config, args.output)

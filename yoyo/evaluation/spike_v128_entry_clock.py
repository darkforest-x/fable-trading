"""Describe authenticated SPIKE trades by actual Beijing entry clock.

This is an outcome diagnostic, not a new execution gate. No feature or entry
decision consumes outcomes. The fixed cuts and display guards were frozen before
grouped results. Earlier selection uses entries AND exits before the cut. Reused
random controls must share the actual clock bucket; missing pairs are not redrawn.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v128_expansion_report import boolean, holm, inference
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v128-entry-clock-20260923-v1')
CONFIG = EXP / 'config.json'
TEST = Path('tests/evaluation/test_spike_v128_entry_clock.py')
METRICS = ('win_rate', 'mean_net_bp', 'mean_net_r')
KEYS = ['timeframe_min', 'arm', 'policy']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clock(frame, timezone):
    out = frame.copy()
    for key in ('entry_time', 'exit_time'):
        out[key] = pd.to_datetime(out[key], utc=True)
    local = out.entry_time.dt.tz_convert(timezone)
    out['hour'] = local.dt.hour
    out['weekday'] = local.dt.dayofweek
    out['week'] = (local.dt.normalize() - pd.to_timedelta(local.dt.dayofweek, unit='D')).dt.strftime('%Y-%m-%d')
    out['net_bp'] = out.net_return * 1e4
    return out


def cohort(frame, period, split):
    if period == 'all':
        return frame
    early = frame.entry_time < split
    if period == 'earlier':
        return frame[early & (frame.exit_time < split)]
    if period == 'later':
        return frame[~early]
    if period == 'cross':
        return frame[early & ((frame.exit_time >= split) | frame.exit_time.isna())]
    raise ValueError(period)


def bucket(frame, kind):
    if kind in ('1h', '4h'):
        width = int(kind[:-1])
        return frame.hour // width * width
    if kind in ('week', 'weekday'):
        return frame[kind]
    return pd.Series(0, index=frame.index)


def describe(frame, cfg):
    d = frame[frame.status == 'closed']
    n = len(d)
    out = {'taken': len(frame), 'closed': n, 'censored': len(frame)-n,
           'weeks': int(d.week.nunique()), 'symbols': int(d.symbol.nunique())}
    for key in METRICS:
        out[key] = math.nan
    if not n:
        return out
    out.update(wins=int((d.net_return > 0).sum()), win_rate=float((d.net_return > 0).mean()),
               mean_net_bp=float(d.net_bp.mean()), mean_net_r=float(d.net_r.mean()),
               sum_net_r=float(d.net_r.sum()), mean_gross_bp=float(d.gross_return.mean()*1e4),
               median_net_bp=float(d.net_bp.median()), ge5r=int((d.net_r >= 5).sum()),
               ge10r=int((d.net_r >= 10).sum()))
    losses = -d.loc[d.net_r < 0, 'net_r'].sum()
    out['pf_r'] = float(d.loc[d.net_r > 0, 'net_r'].sum()/losses) if losses else math.nan
    out['ge5r_rate'] = out['ge5r']/n
    # Resample whole entry weeks, preserving cross-symbol contemporaneous shocks.
    values = pd.DataFrame({'win_rate': (d.net_return > 0).astype(float),
                           'mean_net_bp': d.net_bp, 'mean_net_r': d.net_r, 'week': d.week})
    g = values.groupby('week')
    if len(g) >= 2:
        sums = g[list(METRICS)].sum().to_numpy()
        counts = g.size().to_numpy()
        rng = np.random.default_rng(cfg['statistics_seed'])
        draws = rng.integers(0, len(counts), (cfg['bootstrap'], len(counts)))
        means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)[:, None]
        for i, key in enumerate(METRICS):
            out[f'{key}_low'], out[f'{key}_high'] = np.quantile(means[:, i], [.025, .975]).tolist()
    return out


def prepare_controls(controls, timezone):
    controls = controls.copy()
    anchors = controls[controls.control_pool == 'baseline'][['trade_key', 'control_signal_close']].rename(
        columns={'control_signal_close': 'control_anchor_close'})
    if anchors.trade_key.duplicated().any():
        raise ValueError('duplicate random anchor')
    controls = controls.merge(anchors, on='trade_key', validate='many_to_one')
    controls['control_anchor_close'] = pd.to_datetime(controls.control_anchor_close, utc=True)
    controls = controls[boolean(controls.matched) & (controls.control_status == 'closed') &
                        (controls.control_anchor_close.dt.dayofweek != 6)]
    controls = controls.rename(columns={'control_pool': 'policy', 'control_signal_close': 'entry_time',
        'control_exit_time': 'exit_time', 'control_net_return': 'net_return', 'control_net_r': 'net_r'})
    controls = clock(controls, timezone)
    if controls.duplicated(['trade_key', 'policy']).any():
        raise ValueError('duplicate matched control')
    if not np.isfinite(controls[['net_return', 'net_r']].to_numpy()).all():
        raise ValueError('missing control outcome')
    return controls


def pair_rows(frame, controls, kind, period, split):
    targets = cohort(frame, period, split)
    targets = targets[targets.status == 'closed'].copy()
    random = cohort(controls, period, split).copy()
    targets['bucket'] = bucket(targets, kind)
    random['bucket'] = bucket(random, kind)
    return targets.merge(random[['trade_key', 'policy', 'bucket', 'net_return', 'net_r']],
                         on=['trade_key', 'policy', 'bucket'], suffixes=('', '_random'), validate='one_to_one')


def comparator(pairs, cfg, *, test=False):
    out = {'matched': len(pairs), 'paired_target_mean_net_bp': math.nan,
           'random_mean_net_bp': math.nan, 'random_win_rate': math.nan, 'mean_excess_bp': math.nan}
    if not len(pairs):
        return out
    out.update(paired_target_mean_net_bp=float(pairs.net_return.mean()*1e4),
               random_mean_net_bp=float(pairs.net_return_random.mean()*1e4),
               random_win_rate=float((pairs.net_return_random > 0).mean()),
               mean_excess_bp=float((pairs.net_return-pairs.net_return_random).mean()*1e4))
    if test:
        stats = inference((pairs.net_return-pairs.net_return_random)*1e4, pairs.week,
                          cfg['statistics_seed'], cfg['bootstrap'])
        out.update({('paired_weeks' if k == 'weeks' else k): v for k, v in stats.items()})
    return out


def choose(table, metric, cfg, *, earlier=False):
    if earlier:
        ok = (table.closed >= cfg['early_rank_min_closed']) & (table.weeks >= cfg['early_rank_min_weeks'])
    else:
        ok = ((table.closed >= cfg['rank_min_closed']) & (table.weeks >= cfg['rank_min_weeks']) &
              (table.earlier_closed >= cfg['rank_min_each_half']) & (table.later_closed >= cfg['rank_min_each_half']))
    ranked = table[ok].sort_values([metric, 'closed', 'bucket'], ascending=[False, False, True])
    return None if ranked.empty else ranked.iloc[0]


def sensitivity(frame):
    d = frame[frame.status == 'closed']
    if len(d) < 2:
        return {}
    symbol = d.groupby('symbol').net_bp.sum().idxmax()
    week = d.groupby('week').net_bp.sum().idxmax()
    return {'without_best_trade_bp': float(d.drop(index=d.net_bp.idxmax()).net_bp.mean()),
            'best_symbol': symbol, 'without_best_symbol_bp': float(d[d.symbol != symbol].net_bp.mean()),
            'best_week': week, 'without_best_week_bp': float(d[d.week != week].net_bp.mean())}


def authenticate(cfg):
    root = Path(cfg['source_summary']); run = Path(cfg['source_run'])
    if digest(root/'summary_receipt.json') != cfg['source_summary_receipt_sha256']:
        raise ValueError('source summary receipt changed')
    rec = json.loads((root/'summary_receipt.json').read_text())
    manifest = json.loads((run/'manifest.json').read_text())
    identity = json.loads((run/'identity.json').read_text())
    expected = {f'{s}_{m}m' for s in identity['symbols'] for m in cfg['timeframes']}
    if (digest(run/'manifest.json') != rec['run_manifest_sha256'] or not manifest['complete'] or
            manifest['errors'] or set(manifest['receipts']) != expected or identity['subset'] or
            len(expected) != rec['streams'] or rec['parent_parity_streams'] != rec['streams']):
        raise ValueError('incomplete or changed source')
    if hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest() != manifest['run_identity']:
        raise ValueError('changed source identity')
    for path, sha in (identity['code'] | rec['source']).items():
        if digest(path) != sha:
            raise ValueError(f'changed source code: {path}')
    for name, sha in rec['files'].items():
        if digest(root/name) != sha:
            raise ValueError(f'changed source ledger: {name}')
    for key in ('start', 'split', 'end', 'round_trip_cost'):
        if rec['config'][key] != cfg[key]:
            raise ValueError(f'changed source assumption: {key}')
    trades = pd.read_csv(root/'serial_trades.csv.gz', low_memory=False)
    trades = trades[trades.policy.isin(cfg['policies'])].copy()
    if trades.duplicated(['trade_key', 'policy']).any():
        raise ValueError('duplicate serial trade')
    trades = clock(trades, cfg['timezone'])
    if not trades.entry_time.between(pd.Timestamp(cfg['start']), pd.Timestamp(cfg['end']), inclusive='left').all():
        raise ValueError('out of range actual entry')
    closed = trades[trades.status == 'closed']
    if not np.isfinite(closed[['net_return', 'net_r']].to_numpy()).all():
        raise ValueError('missing closed outcome')
    np.testing.assert_allclose(closed.gross_return-closed.net_return, cfg['round_trip_cost'], atol=1e-12)
    source = pd.read_csv(root/'summary.csv')
    source = source[(source.period == 'all') & source.policy.isin(cfg['policies'])].set_index(KEYS)
    reconciled = []
    for key, d in trades.groupby(KEYS):
        got = describe(d, cfg); old = source.loc[key]
        for metric in ('taken', 'closed', 'censored', 'win_rate', 'mean_net_r', 'mean_net_bp'):
            np.testing.assert_allclose(got[metric], old[metric], rtol=1e-10, atol=1e-10)
        reconciled.append(dict(zip(KEYS, (int(key[0]), key[1], key[2]))) |
                          {'closed': got['closed'], 'source_metrics_match': True})
    controls = prepare_controls(pd.read_csv(root/'controls.csv.gz', low_memory=False), cfg['timezone'])
    return trades, controls, reconciled


def build(output):
    sources = [Path(__file__), CONFIG, TEST, EXP/'PROJECT_PLAN.md']
    if not _committed(sources):
        raise ValueError('commit builder, configuration, protocol and tests first')
    if output.exists():
        raise ValueError('output exists; preserve previous outputs')
    cfg = json.loads(CONFIG.read_text()); split = pd.Timestamp(cfg['split'])
    trades, controls, reconciled = authenticate(cfg)
    rows = []
    for key, frame in trades.groupby(KEYS):
        c = controls[(controls.timeframe_min == key[0]) & (controls.arm == key[1]) & (controls.policy == key[2])]
        meta = dict(zip(KEYS, key))
        for kind in ('overall', '4h', '1h', 'weekday', 'week'):
            bins = {'overall': [0], '4h': range(0, 24, 4), '1h': range(24),
                    'weekday': range(7), 'week': sorted(trades.week.unique())}[kind]
            pairs = {p: pair_rows(frame, c, kind, p, split) for p in ('all', 'earlier', 'later', 'cross')}
            for value in bins:
                f = frame[bucket(frame, kind) == value]
                earlier_n = int((cohort(f, 'earlier', split).status == 'closed').sum())
                later_n = int((cohort(f, 'later', split).status == 'closed').sum())
                for period in ('all', 'earlier', 'later', 'cross'):
                    p = pairs[period]; p = p[p.bucket == value]
                    row = meta | {'kind': kind, 'bucket': value, 'period': period,
                                  'earlier_closed': earlier_n, 'later_closed': later_n}
                    row |= describe(cohort(f, period, split), cfg)
                    row |= comparator(p, cfg, test=(kind == '4h' and period == 'all'))
                    row['random_coverage'] = len(p)/row['closed'] if row['closed'] else math.nan
                    rows.append(row)
    table = pd.DataFrame(rows)
    primary = (table.kind == '4h') & (table.period == 'all')
    assert primary.sum() == 48
    table.loc[primary, 'holm_p_48_slots'] = holm(table.loc[primary, 'p_one_sided'])
    winners, selected = [], []
    for (minutes, arm, policy, kind), group in table[table.kind.isin(['4h', '1h'])].groupby(KEYS+['kind']):
        meta = dict(zip(KEYS+['kind'], (minutes, arm, policy, kind)))
        for metric in METRICS:
            winner = choose(group[group.period == 'all'], metric, cfg)
            if winner is None:
                winners.append(meta | {'metric': metric, 'eligible': False})
            else:
                frame = trades[(trades.timeframe_min == minutes) & (trades.arm == arm) & (trades.policy == policy)]
                frame = frame[bucket(frame, kind) == winner.bucket]
                winners.append(winner.to_dict() | {'metric': metric, 'eligible': True} | sensitivity(frame))
            early = choose(group[group.period == 'earlier'], metric, cfg, earlier=True)
            row = meta | {'metric': metric, 'eligible': early is not None}
            if early is not None:
                late = group[(group.period == 'later') & (group.bucket == early.bucket)].iloc[0]
                overall = table[(table.timeframe_min == minutes) & (table.arm == arm) & (table.policy == policy) &
                                (table.kind == 'overall') & (table.period == 'later')].iloc[0]
                row['bucket'] = early.bucket
                for prefix, item in [('earlier', early), ('later', late), ('later_overall', overall)]:
                    for k in ('closed', *METRICS, 'matched', 'random_mean_net_bp', 'mean_excess_bp'):
                        row[f'{prefix}_{k}'] = item[k]
            selected.append(row)
    output.mkdir(parents=True)
    table.to_csv(output/'buckets.csv', index=False)
    pd.DataFrame(winners).to_csv(output/'winners.csv', index=False)
    pd.DataFrame(selected).to_csv(output/'earlier_selected_later.csv', index=False)
    receipt = {'config': cfg, 'generated_at': pd.Timestamp.now(tz='UTC').isoformat(),
               'source': {str(p): digest(p) for p in sources}, 'reconciled': reconciled,
               'files': {p.name: digest(p) for p in sorted(output.glob('*.csv'))},
               'closed': int((trades.status == 'closed').sum()), 'taken': len(trades),
               'primary_test_slots': int(primary.sum())}
    (output/'receipt.json').write_text(json.dumps(receipt, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({'output': str(output), 'taken': len(trades), 'rows': len(table)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=EXP/'summary_v1')
    build(parser.parse_args().output)

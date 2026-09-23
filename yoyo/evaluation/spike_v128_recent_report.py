"""Descriptive V12.8 outcomes from authenticated ledgers, never from future scores.

Metrics distinguish signal candidates, serial fills, censored observations and
closed trades. MFE is descriptive after entry, not a deployable entry feature.
Weekly blocks retain cross-symbol dependence; R totals are not account returns.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v104_forward import wilson

THRESHOLDS = (0., .5, 1., 2., 3., 5., 10.)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_csv(path):
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def enrich(frame, split):
    """Add only ex-post labels from existing trade outcomes and actual timestamps."""
    if frame.empty:
        return frame
    frame = frame.copy()
    for col in ('signal_close', 'entry_time', 'exit_time'):
        frame[col] = pd.to_datetime(frame[col], utc=True)
    frame['net_bp'] = frame.net_return * 1e4
    frame['gross_bp'] = frame.gross_return * 1e4
    frame['month'] = frame.signal_close.dt.strftime('%Y-%m')
    monday = frame.signal_close.dt.normalize() - pd.to_timedelta(frame.signal_close.dt.dayofweek, unit='D')
    frame['week'] = monday.dt.strftime('%Y-%m-%d')
    frame['period'] = np.where(frame.signal_close >= split, 'later',
                               np.where(frame.exit_time < split, 'earlier', 'cross_split'))
    frame['protective_stop'] = frame.exit_reason.str.contains('stop', regex=False)
    frame['loss'] = frame.net_return < 0
    frame['gross_loss'] = frame.gross_return < 0
    if 'mfe_known_r' not in frame:
        frame['mfe_known_r'] = frame.mfe_r
    if 'mfe_upper_r' not in frame:
        frame['mfe_upper_r'] = frame.mfe_known_r
    frame['fee_r'] = .002 / frame.initial_risk_frac
    frame['mfe_known_net_r'] = frame.mfe_known_r - frame.fee_r
    frame['giveback_r'] = frame.mfe_known_r - frame.gross_r
    frame['holding_hours'] = (frame.exit_time - frame.entry_time).dt.total_seconds() / 3600
    return frame


def metrics(frame):
    """Return explicit counts; censoring is removed by the caller, never called a win."""
    n = len(frame)
    if n == 0:
        return {'closed': 0, 'wins': 0, 'losses': 0, 'win_rate': math.nan}
    positive, negative = frame.net_r[frame.net_r > 0], frame.net_r[frame.net_r < 0]
    rate_lo, rate_hi = wilson(int((frame.net_return > 0).sum()), n)
    ordered = frame.sort_values(['exit_time', 'trade_key']).net_r.to_numpy().cumsum()
    equity = np.r_[0., ordered]
    out = {'closed': n, 'wins': int((frame.net_return > 0).sum()),
           'losses': int(frame.loss.sum()), 'flat': int((frame.net_return == 0).sum()),
           'win_rate': float((frame.net_return > 0).mean()), 'win_ci_low': rate_lo, 'win_ci_high': rate_hi,
           'gross_win_rate': float((frame.gross_return > 0).mean()),
           'mean_net_r': float(frame.net_r.mean()), 'sum_net_r': float(frame.net_r.sum()),
           'median_net_r': float(frame.net_r.median()), 'mean_net_bp': float(frame.net_bp.mean()),
           'mean_gross_bp': float(frame.gross_bp.mean()),
           'pf_r': float(positive.sum() / -negative.sum()) if len(negative) else math.nan,
           'average_win_r': float(positive.mean()), 'average_loss_r': float(negative.mean()),
           'best_net_r': float(frame.net_r.max()), 'worst_net_r': float(frame.net_r.min()),
           'event_ordered_drawdown_r_nonaccount': float((np.maximum.accumulate(equity) - equity).max()),
           'mean_holding_hours': float(frame.holding_hours.mean()),
           'median_holding_hours': float(frame.holding_hours.median()),
           'median_risk_bp': float(frame.initial_risk_frac.median()*1e4),
           'stop_exits': int(frame.protective_stop.sum()),
           'stop_net_losses': int((frame.protective_stop & frame.loss).sum()),
           'fee_only_losses': int((frame.loss & (frame.gross_return >= 0)).sum()),
           'net_gt5r': int((frame.net_r > 5).sum()), 'net_gt10r': int((frame.net_r > 10).sum()),
           'net_float_to_stop_loss': int((frame.protective_stop & frame.loss & (frame.mfe_known_net_r > 0)).sum()),
           'net_float_to_any_loss': int((frame.loss & (frame.mfe_known_net_r > 0)).sum()),
           'mfe_ge2r_no_close_arm_stop_losses': int((frame.protective_stop & frame.loss & (frame.mfe_known_r >= 2)
                                                     & (frame.close_peak_r < 2)).sum())}
    # This table reports reached thresholds, so levels overlap intentionally.
    for threshold in THRESHOLDS:
        hit = frame.mfe_known_r > 0 if threshold == 0 else frame.mfe_known_r >= threshold
        upper = frame.mfe_upper_r > 0 if threshold == 0 else frame.mfe_upper_r >= threshold
        tag = f'{threshold:g}r'
        out[f'reached_{tag}'] = int(hit.sum())
        out[f'float_to_stop_loss_{tag}'] = int((hit & frame.protective_stop & frame.loss).sum())
        out[f'float_to_any_loss_{tag}'] = int((hit & frame.loss).sum())
        out[f'ambiguous_stop_profit_{tag}'] = int((~hit & upper & frame.protective_stop & frame.loss).sum())
    return out


def block_inference(values, blocks, seed=923129, bootstrap=2000):
    """Bootstrap weeks and enumerate sign flips when few blocks limit resolution."""
    data = pd.DataFrame({'value': np.asarray(values, float), 'block': np.asarray(blocks)})
    grouped = data.groupby('block').value
    sums, counts = grouped.sum().to_numpy(), grouped.size().to_numpy()
    nblocks = len(sums)
    if nblocks < 2:
        return {'weeks': nblocks, 'mean_excess_bp': float(data.value.mean()), 'p_one_sided': math.nan,
                'ci_low_bp': math.nan, 'ci_high_bp': math.nan}
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, nblocks, (bootstrap, nblocks))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    if nblocks <= 16:
        signs = np.array(list(itertools.product((-1., 1.), repeat=nblocks)))
        p = float(((signs*sums).sum(axis=1) >= sums.sum()-1e-12).mean())
        resolution = 1 / len(signs)
    else:
        signs = rng.choice((-1., 1.), size=(10000, nblocks))
        p = float((1+((signs*sums).sum(axis=1) >= sums.sum()-1e-12).sum())/10001)
        resolution = 1/10001
    return {'weeks': nblocks, 'mean_excess_bp': float(data.value.mean()),
            'ci_low_bp': float(np.quantile(means, .025)), 'ci_high_bp': float(np.quantile(means, .975)),
            'p_one_sided': p, 'minimum_p_resolution': resolution}


def grouped_metrics(frame, keys):
    rows = []
    for key, group in frame.groupby(keys, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        rows.append(dict(zip(keys, key)) | metrics(group))
    return pd.DataFrame(rows)


def plot_outcomes(summary, output):
    """Export descriptive results with denominators and ambiguous-stop wicks separated."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), constrained_layout=True)
    labels = [f"{int(row.timeframe_min)}m {'SPIKE L/S' if row.arm == 'v9_both' else 'bk+spike L'}"
              for row in summary.itertuples()]
    x = np.arange(len(summary))
    rates = summary.win_rate.to_numpy()*100
    axes[0].bar(x, rates, color=['#466e9e' if 'SPIKE' in s else '#dc9056' for s in labels])
    axes[0].set(xticks=x, xticklabels=labels, ylabel='Closed-trade net win rate (%)', ylim=(0, 100),
                title='After the frozen 20 bp round-trip cost')
    for i, row in enumerate(summary.itertuples()):
        axes[0].text(i, rates[i]+2, f'{row.win_rate:.1%}\nn={row.closed:,}', ha='center', fontsize=10)
    levels = ('0r','1r','2r','3r','5r')
    for row, label in zip(summary.itertuples(), labels):
        values = [getattr(row, f'float_to_stop_loss_{level}') / row.closed * 100 for level in levels]
        axes[1].plot(np.arange(5), values, marker='o', label=label)
    axes[1].set(xticks=np.arange(5), xticklabels=['>0R', '>=1R', '>=2R', '>=3R', '>=5R'],
                xlabel='Established favourable excursion before exit',
                ylabel='Net-losing stop exits / all closed trades (%)', title='Profit given back before a losing stop')
    axes[1].legend(fontsize=8)
    for ax in axes:
        ax.grid(axis='y', alpha=.18); ax.set_axisbelow(True)
        ax.tick_params(axis='x', labelsize=8)
        ax.spines[['top','right']].set_visible(False)
    fig.suptitle('SPIKE V12.8 | 2026-07-23 to 2026-09-23 04:00 UTC\nResearch replay; stop-bar wick order is excluded from established profit', fontsize=12)
    fig.savefig(output/'outcomes.png', dpi=170)
    plt.close(fig)


def build(root, output):
    """All stream ledger hashes must match their completed receipt before reading."""
    root, output = Path(root), Path(output)
    manifest = json.loads((root/'manifest.json').read_text())
    if not manifest.get('complete'):
        raise ValueError('incomplete replay manifest')
    identity = json.loads((root/'identity.json').read_text())
    run_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if manifest['run_identity'] != run_hash:
        raise ValueError('run identity mismatch')
    expected = set(manifest['stream_keys'])
    if expected != set(manifest['receipts']):
        raise ValueError('manifest receipt inventory mismatch')
    actual = {p.name for p in (root/'streams').iterdir() if p.is_dir()}
    if expected != actual:
        raise ValueError('stream directory inventory mismatch')
    tables = {name: [] for name in ('trades', 'decisions', 'statuses', 'controls', 'frames', 'hints')}
    receipts = []
    for directory in sorted((root/'streams').iterdir()):
        if not directory.is_dir():
            continue
        receipt = json.loads((directory/'receipt.json').read_text())
        if digest(directory/'receipt.json') != manifest['receipts'][directory.name]:
            raise ValueError('receipt hash mismatch')
        if receipt['status'] != 'complete' or receipt['run_identity'] != run_hash:
            raise ValueError('incomplete stream')
        for filename, sha in receipt['files'].items():
            if digest(directory/filename) != sha:
                raise ValueError(f'ledger hash mismatch {directory}/{filename}')
        receipts.append(receipt)
        for name in tables:
            path = directory/f'{name}.csv.gz'
            if path.exists():
                tables[name].append(read_csv(path))
    tables = {n: pd.concat([x for x in xs if not x.empty], ignore_index=True) if any(not x.empty for x in xs)
              else pd.DataFrame() for n, xs in tables.items()}
    cfg = identity['config']
    trades = enrich(tables['trades'], pd.Timestamp(cfg['split']))
    closed = trades.loc[~trades.censored.astype(bool)].copy()
    output.mkdir(parents=True, exist_ok=False)
    keys = ['timeframe_min', 'arm']
    views = {'summary': keys, 'direction': keys+['side'], 'monthly': keys+['month'],
             'weekly': keys+['week'], 'periods': keys+['period'], 'symbols': keys+['symbol']}
    for name, group_keys in views.items():
        grouped_metrics(closed, group_keys).to_csv(output/f'{name}.csv', index=False)
    plot_outcomes(grouped_metrics(closed, keys), output)
    grouped_metrics(closed.loc[closed.symbol.isin(['BTCUSDT', 'ETHUSDT'])], keys+['symbol']).to_csv(output/'btc_eth.csv', index=False)
    closed.sort_values('net_r', ascending=False).to_csv(output/'all_closed_trades.csv.gz', index=False, compression='gzip')
    closed.loc[closed.loss & closed.protective_stop & (closed.mfe_known_r>0)].sort_values('mfe_known_r', ascending=False).to_csv(output/'floating_profit_stop_losses.csv', index=False)
    for name in ('decisions', 'statuses', 'controls', 'frames', 'hints'):
        tables[name].to_csv(output/f'{name}.csv.gz', index=False, compression='gzip')
    status = tables['statuses']
    status.groupby(keys+['status']).size().rename('count').reset_index().to_csv(output/'status_counts.csv', index=False)
    rows = []
    controls = tables['controls']
    if len(controls):
        matched = controls.loc[controls.matched.astype(bool)].merge(closed, on='trade_key', suffixes=('_ctrl',''), validate='one_to_one')
        matched['excess_bp'] = (matched.net_return - matched.control_net_return)*1e4
        for view, group_keys in views.items():
            if view == 'symbols':
                continue
            for key, group in matched.groupby(group_keys):
                if view == 'periods' and key[-1] == 'earlier':
                    control_exit = pd.to_datetime(group.control_exit_time, utc=True)
                    group = group.loc[control_exit < pd.Timestamp(cfg['split'])]
                if group.empty:
                    continue
                rows.append(dict(zip(group_keys, key)) | {'view':view,'matched':len(group),
                            'target_net_bp':float(group.net_bp.mean()),
                            'control_net_bp':float(group.control_net_return.mean()*1e4),
                            'control_win_rate':float((group.control_net_return>0).mean())}
                            | block_inference(group.excess_bp, group.week, cfg['stat_seed'], cfg['bootstrap']))
    comparison = pd.DataFrame(rows)
    if len(comparison):
        comparison['p_holm_primary_four'] = np.nan
        primary = comparison.loc[comparison.view == 'summary'].dropna(subset=['p_one_sided']).sort_values('p_one_sided')
        running = 0.
        for rank, (idx, row) in enumerate(primary.iterrows()):
            running = max(running, min(1., row.p_one_sided*(len(primary)-rank)))
            comparison.loc[idx, 'p_holm_primary_four'] = running
    comparison.to_csv(output/'random_comparison.csv', index=False)
    receipt = {'input_run':str(root), 'input_manifest_sha256':digest(root/'manifest.json'),
               'input_identity_sha256':digest(root/'identity.json'), 'stream_count':len(receipts),
               'trade_rows':len(trades), 'closed':len(closed), 'censored':int(trades.censored.sum()),
               'files':{p.name:digest(p) for p in output.iterdir() if p.is_file()},
               'generated_at':pd.Timestamp.now(tz='UTC').isoformat()}
    (output/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(); print(json.dumps(build(args.run,args.output),indent=2))

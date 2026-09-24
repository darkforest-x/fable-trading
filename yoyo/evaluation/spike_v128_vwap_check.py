"""Independently reconcile VWAP summary metrics with saved serial ledgers.

Only existing immutable research outputs are read. No statistics helper or
signal engine participates in this arithmetic/selection audit. Gate overlap
is a descriptive comparison of the frozen rules, never an optimized policy.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(run, summary, output):
    run, summary, output = Path(run), Path(summary), Path(output)
    cfg = json.loads((run/'identity.json').read_text())['config']
    manifest = json.loads((run/'manifest.json').read_text())
    split, start, end = [pd.Timestamp(cfg[k]) for k in ('split', 'analysis_start', 'end')]
    parts, overlap = [], []
    for key, expected_sha in sorted(manifest['receipts'].items()):
        folder = run/'streams'/key
        assert sha(folder/'receipt.json') == expected_sha
        receipt = json.loads((folder/'receipt.json').read_text())
        for name in ('serial_trades.csv.gz', 'candidate_outcomes.csv.gz'):
            assert sha(folder/name) == receipt['files'][name]
        trades = pd.read_csv(folder/'serial_trades.csv.gz')
        if len(trades):
            for col in ('signal_close', 'exit_time'):
                trades[col] = pd.to_datetime(trades[col], utc=True)
            trades['censored'] = trades.censored.astype(str).str.lower().eq('true')
            for policy in ('vwap_near', 'twap_near'):
                assert trades.loc[trades.policy.eq(policy), policy].astype(str).str.lower().eq('true').all()
            for _, group in trades.groupby(['policy', 'arm', 'evaluation_fold']):
                rows = group.sort_values('signal_i')
                assert (rows.signal_i.iloc[1:].to_numpy() >= rows.exit_i.iloc[:-1].to_numpy()).all()
                assert not rows.status.iloc[:-1].eq('censored_boundary').any()
            parts.append(trades)
        candidates = pd.read_csv(folder/'candidate_outcomes.csv.gz')
        if len(candidates):
            clock = pd.to_datetime(candidates.signal_close, utc=True)
            candidates = candidates[candidates.arm.eq('v9_both') & (clock >= split) & (clock < end)]
            a = candidates.vwap_near.astype(str).str.lower().eq('true')
            b = candidates.twap_near.astype(str).str.lower().eq('true')
            overlap.append({'timeframe_min': int(receipt['minutes']), 'candidates': len(candidates),
                            'both': int((a & b).sum()), 'vwap_only': int((a & ~b).sum()),
                            'twap_only': int((b & ~a).sum()), 'neither': int((~a & ~b).sum())})
    all_trades = pd.concat(parts, ignore_index=True)
    metrics = pd.read_csv(summary/'metrics.csv')
    comparisons = 0
    for row in metrics.to_dict('records'):
        group = all_trades[all_trades.timeframe_min.eq(row['timeframe_min']) & all_trades.arm.eq(row['arm'])
                           & all_trades.policy.eq(row['policy'])]
        if row['period'] == 'earlier':
            group = group[group.signal_close < split]
        elif row['period'] == 'later':
            group = group[group.signal_close >= split]
        assert len(group) == row['filled']
        mature = group[group.status.eq('closed') & ~group.censored
                       & ((group.signal_close >= split) | (group.exit_time < split))]
        assert len(mature) == row['closed']
        if len(mature):
            np.testing.assert_allclose(math.fsum(mature.net_return)/len(mature)*1e4, row['mean_net_bp'], rtol=1e-12, atol=1e-9)
            np.testing.assert_allclose(math.fsum(mature.net_r)/len(mature), row['mean_net_r'], rtol=1e-12, atol=1e-10)
            np.testing.assert_allclose((mature.net_return > 0).sum()/len(mature), row['win_rate'], atol=1e-12)
            np.testing.assert_allclose(mature.gross_return-mature.net_return, .002, atol=1e-12)
        base = all_trades[all_trades.timeframe_min.eq(row['timeframe_min']) & all_trades.arm.eq(row['arm'])
                          & all_trades.policy.eq('baseline')]
        if row['period'] == 'earlier':
            base = base[base.signal_close < split]
        elif row['period'] == 'later':
            base = base[base.signal_close >= split]
        base = base[base.status.eq('closed') & ~base.censored & ((base.signal_close >= split) | (base.exit_time < split))]
        for level in (3, 5, 10):
            a = set(base.loc[base.net_r >= level, 'trade_key'])
            b = set(mature.loc[mature.net_r >= level, 'trade_key'])
            assert len(b) == row[f'net_ge{level}r']
            assert len(a & b) == row[f'samekey_retained_ge{level}r']
            assert len(a-b) == row[f'samekey_lost_ge{level}r']
            assert len(b-a) == row[f'samekey_new_ge{level}r']
        comparisons += 1
    result = {'status': 'passed', 'streams': len(manifest['receipts']), 'metric_rows_verified': comparisons,
              'manifest_sha256': sha(run/'manifest.json'), 'metrics_sha256': sha(summary/'metrics.csv'),
              'checker_sha256': sha(__file__),
              'later_raw_gate_overlap': pd.DataFrame(overlap).groupby('timeframe_min').sum().reset_index().to_dict('records')}
    if output.exists():
        raise ValueError('refusing to overwrite audit')
    output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', required=True)
    p.add_argument('--summary', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    audit(args.run, args.summary, args.output)

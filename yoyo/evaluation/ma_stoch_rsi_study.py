"""Fixed two-arm RSI-zone comparison on explicitly allowed pre-holdout data.

The experiment protocol freezes RSI settings before any price scoring. Reuses
the frozen v2 execution and matching engine; no exit parameter search or live
imports. All price access goes through the timestamp-first prefix reader.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.data.spike_fanshen_prefix import read_prefix
from yoyo.evaluation.ma_stoch_exit_v2 import prepare, simulate, POLICIES, BAR
from yoyo.evaluation.ma_stoch_exit_v2_study import committed_identity, matched_controls, summarise
from yoyo.evaluation.ma_stoch_rsi_filter import add_filter
from yoyo.evaluation.ma_shift_stoch_study import dump, sha, control_stats

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-ma-stoch-parabolic-rsi-filter-20260916-v1'
PREVIOUS = ROOT / 'experiments/active/exp-ma-stoch-exit-optimization-20260915-v2'
SOURCE = 'experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/sources/NI0Qhwy7.pine'
BUILDERS = [
    'yoyo/evaluation/' + f + '.py' for f in (
        'ma_stoch_rsi_filter', 'ma_stoch_rsi_study', 'ma_stoch_exit_v2',
        'ma_stoch_exit_v2_study', 'ma_stoch_exit_engine', 'ma_shift_stoch',
        'ma_shift_stoch_study', 'spike_fanshen_exit', 'spike_v6_bb_squeeze')
] + ['yoyo/data/spike_fanshen_prefix.py', 'tests/test_ma_stoch_rsi_filter.py', SOURCE,
     str((EXP/'config.json').relative_to(ROOT)), str((EXP/'PROJECT_PLAN.md').relative_to(ROOT))]


def longest_run(flags):
    longest = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return longest


def check_previous(result, phase, source, name):
    """Check unchanged arm against already saved v2 ledgers, not new scoring."""
    prior = json.loads((PREVIOUS/phase/'summary.json').read_text())
    if source['prefix_sha256'] != prior['source']['prefix_sha256']:
        raise ValueError('original data prefix changed')
    old = pd.read_csv(PREVIOUS/phase/f'{name}_trades.csv')
    new = result['trades']
    if len(new) != len(old): raise AssertionError('old baseline trade count changed')
    for col in ('entry_time', 'exit_time'):
        assert pd.to_datetime(new[col], utc=True).tolist() == pd.to_datetime(old[col], utc=True).tolist()
    for col in ('side', 'signal_i', 'entry_price', 'exit_price_weighted', 'net_return'):
        np.testing.assert_allclose(new[col], old[col], atol=1e-12, rtol=1e-12)
    np.testing.assert_allclose(result['final_equity'], prior['arms'][name]['final_equity'], atol=1e-9, rtol=0)
    return dict(status='passed', summary_sha256=sha(PREVIOUS/phase/'summary.json'),
                trades_sha256=sha(PREVIOUS/phase/f'{name}_trades.csv'))


def run(phase, output=None):
    cfg = json.loads((EXP/'config.json').read_text())
    assert cfg['round_trip_cost'] == .002 and cfg['initial_equity'] == 1000
    assert cfg['rsi_timeframe_minutes'] == 5 and cfg['zone_comparison'] == 'strict_lt_gt_on_signal_bar'
    code = committed_identity(BUILDERS)
    start = pd.Timestamp(cfg['dev_start' if phase == 'dev' else 'val_start'])
    end = pd.Timestamp(cfg['dev_end' if phase == 'dev' else 'val_end'])
    out = EXP/phase if output is None else Path(output).resolve()
    if not out.is_relative_to(EXP): raise ValueError('output must remain inside this experiment')
    if out.exists(): raise ValueError('frozen output cannot be overwritten')
    frame, source = read_prefix(ROOT/cfg['source'], 5, end)
    base = prepare(frame)
    gated = add_filter(base, cfg['rsi_length'], cfg['lower'], cfg['upper'])
    clock = frame.index + BAR
    scope = (clock >= start) & (clock < end)
    candidates = pd.DataFrame(dict(signal_i=np.arange(len(frame)), signal_close=clock,
        arrow=base['arrow'], direction_15m=base['direction'], original_admission=base['admission'],
        rsi=gated['rsi'], filtered_admission=gated['admission']))
    candidates = candidates[scope & (base['arrow'] != 0)]
    out.mkdir(parents=True)
    candidates.to_csv(out/'candidates.csv', index=False)
    counts = dict(arrows=len(candidates), ma_pass=int((candidates.original_admission != 0).sum()),
                  rsi_and_ma_pass=int((candidates.filtered_admission != 0).sum()),
                  undefined_rsi=int(candidates.rsi.isna().sum()))
    for label, side in [('long', 1), ('short', -1)]:
        counts[label] = dict(arrows=int((candidates.arrow == side).sum()),
            ma_pass=int((candidates.original_admission == side).sum()),
            rsi_and_ma_pass=int((candidates.filtered_admission == side).sum()))
    policy = cfg['policy']
    summary = dict(phase=phase, start=start, end=end, config=cfg, policy=asdict(POLICIES[policy]),
        code=code, source=source, generated_at=pd.Timestamp.now(tz='UTC'),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
        evaluated_bars=int(((frame.index >= start) & (frame.index < end)).sum()),
        counts=counts, holdout_consumed=False, arms={})
    for name, ctx in [('unfiltered', base), ('rsi_zone', gated)]:
        result = simulate(ctx, start, end, policy, initial_equity=cfg['initial_equity'], tick=cfg['tick'])
        stats = summarise(result)
        if name == 'unfiltered': stats['previous_parity'] = check_previous(result, phase, source, policy)
        for key in ('trades', 'fills', 'open_positions', 'curve'):
            result[key].to_csv(out/f'{name}_{key}.csv', index=False)
        tr = result['trades']
        stops = tr.exit_reason.isin(['stop', 'stop_gap', 'close_stop']).to_numpy()
        stats['longest_stop'] = longest_run(stops)
        stats['longest_losing_stop'] = longest_run(stops & (tr.net_return.to_numpy() < 0))
        stats['candidates'] = counts['ma_pass' if name == 'unfiltered' else 'rsi_and_ma_pass']
        if name == 'rsi_zone' and len(tr):
            inds = tr.signal_i.to_numpy(int)
            assert np.all(ctx['admission'][inds] == tr.side.to_numpy(int))
            assert np.all(np.where(tr.side == 1, ctx['rsi'][inds] < cfg['lower'], ctx['rsi'][inds] > cfg['upper']))
        controls = matched_controls(base, tr, start, end, policy, cfg)
        controls.to_csv(out/f'{name}_controls.csv', index=False)
        stats['control'] = control_stats(controls, cfg)
        summary['arms'][name] = stats
        print(phase, name, json.dumps(stats, default=str), flush=True)
    dump(out/'summary.json', summary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', required=True, choices=['dev', 'recheck'])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    run(args.phase, args.output)

"""Summarize a frozen MA-cycle diagnostic without fitting thresholds.

Inputs are the completed event/state, availability and matched-pair tables.
Returns use original V8 executions; later milestones are delay/cost diagnostics.
Asset-month block sign flips describe paired associations, not causal effects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metrics(frame):
    r = frame.net_r
    loss = -r.loc[r.lt(0)].sum()
    return dict(n=len(frame), win_rate=r.gt(0).mean(), mean_r=r.mean(),
                median_r=r.median(), net_r=r.sum(),
                profit_factor=r.loc[r.gt(0)].sum()/loss if loss else np.nan,
                realized_10r=int(r.ge(10).sum()), mfe_10r=int(frame.mfe_r.ge(10).sum()))


def paired_stats(frame, rng):
    # A block contains every venue/timeframe for the same underlying asset/month.
    # No pair is treated as an independent new market event.
    if frame.empty:
        return dict(n=0, mean_difference_r=np.nan, block_sign_flip_p=np.nan, blocks=0)
    blocks = frame.groupby(['asset', 'calendar_month']).difference_r.sum().to_numpy()
    observed = abs(blocks.sum())
    exceed = 0
    for _ in range(100):
        signs = rng.integers(0, 2, size=(100, len(blocks))) * 2 - 1
        exceed += int((np.abs(signs @ blocks) >= observed - 1e-12).sum())
    return dict(n=len(frame), mean_difference_r=frame.difference_r.mean(),
                block_sign_flip_p=(1+exceed)/10001, blocks=len(blocks))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().relative_to(Path.cwd().resolve())
    if (subprocess.run(['git', 'cat-file', '-e', f'HEAD:{source}'], capture_output=True).returncode
        or subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', str(source)]).returncode):
        raise ValueError('Commit summary source before building')
    receipt = json.loads((args.input/'receipt.json').read_text())
    for name, expected in receipt.items():
        if sha(args.input/name) != expected:
            raise ValueError(f'Frozen diagnostic changed: {name}')
    if args.output.exists():
        raise ValueError('Preserve prior summary output')
    events = pd.read_csv(args.input/'event_states.csv.gz')
    events['static_order12'] = np.where(events.side.eq(1), events.order_long, events.order_short) == 12
    process = pd.read_csv(args.input/'process_availability.csv.gz')
    pairs = pd.read_csv(args.input/'same_stream_month_side_volatility_pairs.csv')
    closed = events.loc[events.scoring_closed].copy()
    assert len(events) == 113295 and len(closed) == 94180
    assert len(process) == len(events)*2
    keys = ['stream_key', 'signal_bar_open', 'period', 'side']
    assert not events.duplicated(keys).any()
    assert not process.duplicated(keys+['milestone_target']).any()
    assert closed.trade_id.is_unique
    valid = process.valid_for_confirmation_diagnostic
    assert process.loc[valid, 'has_original_trade_reference'].all()
    assert not process.loc[valid, 'original_exit_before_milestone'].any()
    assert not process.loc[valid, 'original_stop_hit_before_milestone'].any()
    assert process.loc[valid, 'next_open_available'].all()
    assert np.isfinite(process.loc[valid, 'confirmation_cost_r']).all()
    matched = pairs.loc[pairs.matched].copy()
    assert not matched.duplicated(['comparison','target_trade_id']).any()
    assert not matched.duplicated(['comparison','control_trade_id']).any()
    matched = matched.merge(closed[['trade_id','asset','calendar_month']],
                            left_on='target_trade_id',right_on='trade_id',validate='many_to_one')
    aggregate = []
    month = []
    for period, part in closed.groupby('period'):
        total10 = int(part.net_r.ge(10).sum())
        cohorts = [('baseline_all', part),
                   ('same_direction_launch', part.loc[part.same_direction_launch]),
                   ('non_launch', part.loc[~part.same_direction_launch]),
                   ('same_direction_expansion', part.loc[part.same_direction_expansion]),
                   ('non_expansion', part.loc[~part.same_direction_expansion]),
                   ('static_order12', part.loc[part.static_order12]),
                   ('static_compression', part.loc[part.compression_qualified])]
        for name, sub in cohorts:
            row = metrics(sub)
            aggregate.append(dict(period=period, comparison=name, **row,
                                  original_10r_retention=row['realized_10r']/total10))
            for calendar_month, cell in sub.groupby('calendar_month'):
                month.append(dict(period=period, calendar_month=calendar_month,
                                  comparison=name, **metrics(cell)))
    ages = []
    for k, sub in closed.groupby(['period','state','state_direction_match','state_age_bucket']):
        ages.append(dict(zip(['period','state','state_direction_match','state_age_bucket'],k))|metrics(sub))
    strata = []
    for period, part in closed.groupby('period'):
        for flag in ('same_direction_launch','same_direction_expansion','static_order12','compression_qualified'):
            for (minutes, side), sub in part.loc[part[flag]].groupby(['timeframe_min','side']):
                strata.append(dict(period=period,comparison=flag,timeframe_min=minutes,side=side,**metrics(sub)))
    rng = np.random.default_rng(20260913)
    pairing = []
    for (period, comparison), group in matched.groupby(['period','comparison']):
        pairing.append(dict(period=period,comparison=comparison,**paired_stats(group,rng)))
    # Reused frozen random entries carry no reliable control exit clock. Keep
    # these as a separate descriptive table and never use them for selection.
    random = pd.read_csv(args.input/'existing_random_controls_reused.csv')
    random = random.merge(events[keys+['static_order12','compression_qualified']],on=keys,validate='many_to_one')
    random_summary = []
    for flag in ('same_direction_launch','same_direction_expansion','static_order12','compression_qualified'):
        for (period, tagged), group in random.groupby(['period',flag],dropna=False):
            group = group.loc[group.matched & group.net_r_difference.notna()]
            random_summary.append(dict(period=period,comparison=flag,tagged=tagged,n=len(group),
                                       mean_difference_r=group.net_r_difference.mean(),
                                       limitation='control exit clock absent; descriptive only'))
    process = process.merge(events[keys+['timeframe_min','executed','scoring_closed']],on=keys,validate='many_to_one')
    costs = []
    for k, p in process.groupby(['period','timeframe_min','milestone_target']):
        eligible = p.loc[p.valid_for_confirmation_diagnostic]
        later = eligible.loc[eligible.delay_bars.gt(0)]
        costs.append(dict(zip(['period','timeframe_min','milestone_target'],k))|
                     dict(events=len(p), milestones=int(p.delay_bars.notna().sum()),
                          reference_valid=len(eligible), later_reference_valid=len(later),
                          later_delay_median=later.delay_bars.median(),
                          later_cost_median_r=later.confirmation_cost_r.median(),
                          later_cost_p75_r=later.confirmation_cost_r.quantile(.75),
                          later_cost_p90_r=later.confirmation_cost_r.quantile(.90)))
    args.output.mkdir(parents=True)
    for filename, data in [('aggregate.csv',aggregate),('monthly.csv',month),('state_age.csv',ages),
                           ('matched_summary.csv',pairing),('random_summary.csv',random_summary),
                           ('confirmation_cost.csv',costs),('timeframe_side.csv',strata)]:
        pd.DataFrame(data).to_csv(args.output/filename,index=False)
    events.groupby(['period','state','state_direction_match'],dropna=False).size().rename('n').to_csv(args.output/'all_event_state_counts.csv')
    process.groupby(['period','milestone_target','status','has_original_trade_reference'],dropna=False).size().rename('n').to_csv(args.output/'process_status_counts.csv')
    pairs.loc[~pairs.matched].groupby(['comparison','missing_reason']).size().rename('n').to_csv(args.output/'unmatched_counts.csv')
    manifest = dict(source_sha256=sha(source),input_receipt_sha256=sha(args.input/'receipt.json'),
                    assertions='all event coverage; unique event and milestone keys; valid reference; no matched-control reuse',
                    statistics='10000 fixed-seed two-sided asset-month block sign flips, plus-one correction; descriptive association, no threshold selection',
                    files={p.name:sha(p) for p in args.output.iterdir() if p.is_file()})
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2))


if __name__ == '__main__':
    main()

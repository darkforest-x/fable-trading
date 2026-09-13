"""Describe frozen asset rankings without turning hindsight into entry features.

Sources: verified V1/V8 normalized closed-event ledger and V8 entry-evidence
snapshot. Entry covariates use only signal-bar or earlier data; net R, holding
time, rank groups and future-period results are explicitly outcome diagnostics.
The fixed plan lives in exp-spike-v1-v8-asset-ranking-20260914-v1/traits_plan.md.
No market fetch, signal replay, new threshold selection or production writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

EXP = Path('experiments/active/exp-spike-v1-v8-asset-ranking-20260914-v1')
EVIDENCE = Path('experiments/active/exp-spike-v8-entry-evidence-20260914-v1/results/full_v1/event_evidence.csv.gz')
EVIDENCE_SHA = '2829bb85f32877a971e8aed8281bbce7c5591255f22b47c0d3bce9835e87bb8c'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def views(frame: pd.DataFrame):
    yield 'v1_common_execution_long', frame[frame.system_source.eq('v1_common_execution_long')]
    yield 'v8_long', frame[frame.system_source.eq('v8') & frame.side.eq(1)]
    yield 'v8_both', frame[frame.system_source.eq('v8')]


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--ledger', type=Path, default=EXP / 'results/full_v1/normalized_ledger.csv.gz')
    parser.add_argument('--output', type=Path, default=EXP / 'interpretation/full_v1')
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    if sha(EVIDENCE) != EVIDENCE_SHA:
        raise ValueError('Frozen entry evidence changed')
    frame = pd.read_csv(args.ledger)
    ev = pd.read_csv(EVIDENCE)
    keys = ['stream_key', 'signal_confirm_time', 'side']
    covars = ['current_ma_width_close', 'current_ma_order_long', 'current_ma_order_short',
              'rope_distance_atr', 'bb_episode_directional_order9_run3',
              'bb_ma_tight_overlap_run3', 'htf_opposed_completed', 'holding_bars']
    ev = ev[keys + covars].copy()
    for d in (frame, ev):
        d['signal_confirm_time'] = pd.to_datetime(d.signal_confirm_time, utc=True)
    if ev.duplicated(keys).any():
        raise ValueError('Entry evidence keys are not unique')
    profiles, cases, persistence, transitions = [], [], [], []
    for view, source in views(frame):
        scored = source[source.scoring_closed.eq(True)].copy()
        dev = scored[scored.period.eq('development')]
        val = scored[scored.period.eq('validation')].copy()
        aggs = {}
        for name, f in [('development', dev), ('validation', val)]:
            aggs[name] = f.groupby('asset').net_r.agg(n='size', sum_r='sum', mean_r='mean')
        ds, vs = aggs['development'], aggs['validation']
        eligible = ds[ds.n.ge(30)].join(vs[vs.n.ge(30)], lsuffix='_dev', rsuffix='_val', how='inner')
        rho = eligible.mean_r_dev.rank().corr(eligible.mean_r_val.rank()) if len(eligible) > 1 else np.nan
        leaders = ds[ds.n.ge(30)].sort_values(['sum_r', 'mean_r'], ascending=False).head(10)
        rows = leaders.join(vs, lsuffix='_dev', rsuffix='_val', how='left').reset_index()
        rows['view'] = view
        rows['later_observed'] = rows.n_val.notna()
        transitions.extend(rows.to_dict('records'))
        persistence.append({'view': view, 'assets_30_each_period': len(eligible), 'spearman_mean_r': rho,
                            'development_eligible_assets': int(ds.n.ge(30).sum()),
                            'development_top_assets': len(leaders), 'top_later_observed': int(rows.later_observed.sum()),
                            'top_later_positive_assets': int(rows.mean_r_val.gt(0).sum()),
                            'top_later_sum_r': rows.sum_r_val.sum(min_count=1)})
        if view.startswith('v8'):
            val = val.merge(ev, on=keys, validate='one_to_one', how='left', indicator=True)
            if not val['_merge'].eq('both').all():
                raise ValueError('Missing V8 feature join')
            val['directional_order'] = np.where(val.side.eq(1), val.current_ma_order_long, val.current_ma_order_short)
            val['long_fraction'] = val.side.eq(1).astype(float)
            val['holding_hours'] = val.holding_bars * val.timeframe_min / 60
            for name in ['bb_episode_directional_order9_run3', 'bb_ma_tight_overlap_run3', 'htf_opposed_completed']:
                val[name] = val[name].map({True: 1., False: 0.})
        main_assets = vs[vs.n.ge(30)]
        for cohort, chosen in [('top10', main_assets.sort_values(['sum_r', 'mean_r'], ascending=False).head(10)),
                               ('bottom10', main_assets.sort_values(['sum_r', 'mean_r']).head(10))]:
            sub = val[val.asset.isin(chosen.index)]
            feature_names = ['risk_fraction_at_entry', 'cost_r']
            if view.startswith('v8'):
                feature_names += ['current_ma_width_close', 'directional_order', 'rope_distance_atr',
                                  'long_fraction', 'holding_hours', 'bb_episode_directional_order9_run3',
                                  'bb_ma_tight_overlap_run3', 'htf_opposed_completed']
            for feature in feature_names:
                per_asset = sub.groupby('asset')[feature].median()
                profiles.append({'view': view, 'cohort': cohort, 'feature': feature,
                                 'assets_observed': int(per_asset.notna().sum()),
                                 'event_observations': int(sub[feature].notna().sum()),
                                 'equal_asset_median': per_asset.median()})
            for asset in chosen.index:
                x = sub[sub.asset.eq(asset)].sort_values('net_r', ascending=False)
                positive = x.loc[x.net_r.gt(0), 'net_r'].sum()
                cases.append({'view': view, 'cohort': cohort, 'asset': asset, 'n': len(x),
                              'sum_r': x.net_r.sum(), 'win_rate': x.net_r.gt(0).mean(),
                              'realized10': int(x.net_r.ge(10).sum()), 'best_r': x.net_r.max(),
                              'best_trade_share_positive': x.net_r.max()/positive if positive > 0 else np.nan,
                              'sum_without_best_trade': x.net_r.sum()-x.net_r.max(),
                              'sum_without_best_asset_day': x.net_r.sum()-x.assign(day=x.signal_confirm_time.dt.floor('D')).groupby('day').net_r.sum().max(),
                              'median_risk': x.risk_fraction_at_entry.median(), 'median_fee_r': x.cost_r.median()})
    for name, records in [('profiles', profiles), ('leader_cases', cases), ('rank_persistence', persistence), ('development_top_transfer', transitions)]:
        pd.DataFrame(records).to_csv(out/f'{name}.csv', index=False)
    outputs = {p.name: {'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(out.glob('*.csv'))}
    (out/'manifest.json').write_text(json.dumps({'input_ledger': str(args.ledger), 'input_ledger_sha256': sha(args.ledger),
        'entry_evidence': str(EVIDENCE), 'entry_evidence_sha256': EVIDENCE_SHA,
        'builder_sha256': sha(Path(__file__)), 'method': 'fixed-descriptive-asset-top-bottom-and-rank-transfer-v1',
        'outcome_selected_groups': True, 'production_eligible': False, 'outputs': outputs}, indent=2))


if __name__ == '__main__':
    main()

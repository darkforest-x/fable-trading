"""Validate frozen single-wick filters using the already executed native ledger.

Source: Notion72 and owner authorization on 2026-10-29. No orders, replay, model,
or parameter optimization. Admission reads only signal-side risk_bp (high-close
over close), first_weekly_touch, reclaim_range (line-close over Monday width),
or volume_ratio20 (signal volume over the preceding 20-bar median). Each was
available at the completed signal close. Outcome fields are used for evaluation
only. Thresholds are the unchanged pre-2025 deciles saved in the source study;
choosing these features after seeing 2026 makes this a retrospective audit.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_ma6_retest import finite
from yoyo.evaluation.single_wick_1618_majors import metrics, paired

EXP = ROOT / 'experiments/active/exp-5m-wick-risk-filter-20261029-v1'


def select_ids(t, rule):
    """Select original signal identities without consulting future outcomes."""
    s = t[t.population.eq('signal') & t.side.eq(-1)]
    feature = rule.get('feature')
    if feature == 'first_weekly_touch':
        s = s[s[feature]]
    elif feature:
        s = s[s[feature] >= rule['cut']]
    return set(s.event_id)


def period_rows(t, start=None, end=None):
    """Require each signal/control's own entry and exit to fit the period.

    Source-censored rows remain explicitly censored. A completed trade whose
    exit crosses a reporting boundary is excluded, not relabeled as a loss or
    liquidated. Entry-cohort boundary counts are returned separately.
    """
    et = pd.to_datetime(t.entry_time, utc=True)
    xt = pd.to_datetime(t.exit_time, utc=True)
    admitted = pd.Series(True, index=t.index)
    if start:
        admitted &= et >= start
    if end:
        admitted &= et < end
    within = admitted.copy()
    if end:
        within &= xt < end
    sig = t.population.eq('signal')
    return t[within].copy(), dict(
        entry_cohort_n=int((admitted & sig).sum()),
        entry_cohort_censored_n=int((admitted & sig & ~t.closed).sum()),
        cross_boundary_n=int((admitted & ~within & sig).sum()))


def week_interval(s, field='net_r'):
    """Descriptive week-block bootstrap, conditional on an already chosen rule."""
    z = s[s.closed].groupby('week')[field].agg(['sum', 'count'])
    if z.empty:
        return None
    rng = np.random.default_rng(20261029)
    ix = rng.integers(len(z), size=(5000, len(z)))
    boot = z['sum'].to_numpy()[ix].sum(1) / z['count'].to_numpy()[ix].sum(1)
    return np.quantile(boot, [.025, .975]).tolist()


def readout(t):
    s = t[t.population.eq('signal')]
    c = s[s.closed]
    # This is a realized independent-R path, never a funded equity curve.
    increments = c.groupby('exit_time').net_r.sum().sort_index().to_numpy()
    path = np.r_[0., increments.cumsum()]
    losses = c.sort_values(['exit_time', 'trade_id']).net_r.le(0).to_numpy()
    longest = current = 0
    for loss in losses:
        current = current + 1 if loss else 0
        longest = max(longest, current)
    return dict(**metrics(s), control=paired(t),
        active_weeks=int(c.week.nunique()), active_symbols=int(c.symbol.nunique()),
        net_mean_r_week_bootstrap95=week_interval(s),
        stress_mean_r_week_bootstrap95=week_interval(s, 'net_stress_r'),
        realized_independent_r_drawdown=float((np.maximum.accumulate(path)-path).max()),
        longest_closed_loss_streak=longest)


def concentration(t):
    """Outcome deletion is a diagnostic, not a tradable filter or selection."""
    s = t[t.population.eq('signal') & t.closed]
    wins = s[s.net_r > 0].sort_values('net_r', ascending=False)
    out = dict(winning_sum_r=float(wins.net_r.sum()), top_winners=[], removal=[],
               leave_one_symbol_out=[], leave_one_week_out=[])
    fields = ['event_id', 'symbol', 'week', 'entry_time', 'exit_time',
              'net_r', 'net_stress_r', 'net_notional_bp', 'risk_bp', 'planned_rr']
    out['top_winners'] = wins.head(5)[fields].to_dict('records')
    for k in (1, 3, 5):
        removed = wins.head(k)
        left = t[~t.event_id.isin(removed.event_id)]
        out['removal'].append(dict(remove_top_winners=k, removed_n=len(removed),
            removed_net_r=float(removed.net_r.sum()),
            share_of_positive_r=float(removed.net_r.sum()/wins.net_r.sum()) if len(wins) else None,
            **readout(left)))
    for key in ('symbol', 'week'):
        for value in sorted(s[key].unique()):
            out[f'leave_one_{key}_out'].append(dict(removed=value, **readout(t[t[key].ne(value)])))
    return out


def assert_saved_readouts(table, source_features):
    """Fail if projection changes the saved native outcomes or period semantics."""
    checks = 0
    names = {'risk_only': 'risk_bp', 'reclaim_only': 'reclaim_range', 'volume_only': 'volume_ratio20'}
    for row in table:
        if row['rule'] not in names or row['period'] not in ('pre2025', 'y2025', 'y2026'):
            continue
        old = next(x for x in source_features if x['feature'] == names[row['rule']] and x['period'] == row['period'])
        for field in ('closed_n', 'target_n', 'gross_mean_r', 'net_mean_r', 'stress_mean_r'):
            assert np.isclose(row[field], old['frozen_top_decile'][field]), (row['rule'], row['period'], field)
            checks += 1
        for field in ('matched_n', 'signal_mean_r', 'random_mean_r', 'paired_week_signflip_p'):
            assert np.isclose(row['control'][field], old['top_control'][field]), (row['rule'], row['period'], field)
            checks += 1
    return checks


def main():
    cfg = json.loads((EXP/'config.json').read_text())
    source = ROOT / cfg['source_experiment']
    t = pd.read_parquet(source/'trades.parquet')
    t = t[t.side.eq(-1)].copy()
    assert not t.trade_id.duplicated().any()
    source_features = json.loads((source/'features_short.json').read_text())
    for rule in cfg['rules']:
        if rule.get('cut') is not None:
            old = next(x for x in source_features if x['feature'] == rule['feature'] and x['period'] == 'pre2025')
            assert old['direction'] == 1 and old['frozen_top_decile_cut'] == rule['cut']
    # Validate actual candle risk identity; controls retain translated geometry.
    s = t[t.population.eq('signal')]
    assert np.allclose(s.risk_bp, (s.stop-s.entry)/s.entry*10000)
    changed = t.copy()
    changed['net_r'] = -999.; changed['target_hit'] = ~changed.target_hit
    changed['closed'] = ~changed.closed; changed['single_touch_target'] = ~changed.single_touch_target
    for rule in cfg['rules']:
        assert select_ids(t, rule) == select_ids(changed, rule)

    periods = [('all', None, None), ('pre2025', None, '2025-01-01'),
               ('y2025', '2025-01-01', '2026-01-01'), ('y2026', '2026-01-01', None),
               ('since2025', '2025-01-01', None)]
    table, years, months, symbols, resolutions = [], [], [], [], []
    groups = {}
    source_cfg = json.loads((source/'config.json').read_text())
    step = {j['symbol']: j['execution_minutes'] for j in source_cfg['jobs']}
    for rule in cfg['rules']:
        selected = t[t.event_id.isin(select_ids(t, rule))]
        for label, start, end in periods:
            g, boundaries = period_rows(selected, start, end)
            row = dict(rule=rule['name'], period=label, **boundaries, **readout(g))
            table.append(row)
            if rule['name'] == 'risk_only':
                groups[label] = g
                for symbol, z in g.groupby('symbol'):
                    symbols.append(dict(period=label, symbol=symbol, **readout(z)))
                for minutes in (1, 5):
                    z = g[g.symbol.map(step).eq(minutes)]
                    resolutions.append(dict(period=label, source_minutes=minutes, **readout(z)))
        for year in range(2020, 2027):
            g, b = period_rows(selected, f'{year}-01-01', f'{year+1}-01-01')
            years.append(dict(rule=rule['name'], year=year, **b, **readout(g)))
        if rule['name'] == 'risk_only':
            for date in pd.date_range('2025-01-01', '2026-09-01', freq='MS', tz='UTC'):
                end = date + pd.offsets.MonthBegin(1)
                g, b = period_rows(selected, str(date), str(end))
                months.append(dict(month=str(date)[:7], **b, **readout(g)))
            selected.to_parquet(EXP/'selected_with_controls.parquet', index=False)
            signal = selected[selected.population.eq('signal')].sort_values(['entry_time', 'symbol'])
            signal.to_csv(EXP/'candidate_trades.csv', index=False)

    validation = dict(saved_numeric_checks=assert_saved_readouts(table, source_features),
        selected_ids_invariant_to_outcome_mutation=True, short_stop_risk_geometry=True,
        frozen_thresholds_exact=True, native_replays=0, new_trades=0)
    detail = {k: concentration(groups[k]) for k in ('all', 'y2025', 'y2026', 'since2025')}
    y25 = next(x for x in table if x['rule']=='risk_only' and x['period']=='y2025')
    y26 = next(x for x in table if x['rule']=='risk_only' and x['period']=='y2026')
    stable = all(x['net_mean_r'] > 0 and x['stress_mean_r'] > 0 and
                 x['control']['paired_week_signflip_p'] < .01 for x in (y25, y26))
    result = dict(experiment_id=EXP.name, comparison=table, annual=years,
        risk_monthly=months, risk_by_symbol=symbols, risk_by_resolution=resolutions,
        stable_across_later_years=stable, validation=validation,
        conclusion='Retrospective candidate only. No account sizing or combined filters without evidence of stability.',
        limitations=cfg['limitations'])
    dump(EXP/'summary.json', finite(result)); dump(EXP/'concentration.json', finite(detail))
    dump(EXP/'validation.json', validation)
    dump(EXP/'reproduction.json', dict(command='~/.local/bin/vectorbt-python -m yoyo.evaluation.single_wick_candidate_validation',
        code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True, cwd=ROOT).strip(),
        inputs=[dict(path=str(source/f), bytes=(source/f).stat().st_size, mtime_ns=(source/f).stat().st_mtime_ns)
                for f in ('trades.parquet', 'features_short.json', 'config.json')],
        mode='Read-only projection of existing Nautilus native outcomes; pandas summaries in isolated VectorBT environment.'))
    print(json.dumps(finite(dict(comparison=table, stable=stable, validation=validation)), indent=2))


if __name__ == '__main__':
    main()

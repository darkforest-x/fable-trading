"""Condition native 12H entries on previously completed outcomes on that contract and side."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as repaired
from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import repair_io
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_same_time_control as simultaneous
from yoyo.evaluation import cloud_close_slots as capacity


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-prior-expectancy-20261109-v1'


def scored_paths(paths):
    """At each entry_ms use only 8–12 prior published returns, exit_bar_ms + H1 < entry_ms."""
    if paths.duplicated(['event_id','tp_r']).any() or not paths.groupby('event_id').size().eq(2).all():
        raise ValueError('Prior expectancy requires one native tp0/tp2 pair per event')
    real = paths[~paths.is_control]
    closed = simultaneous.returns(real)
    roster = real[real.tp_r.eq(0)].set_index('parent_id')
    closed = closed.join(roster[['symbol', 'side']])
    closed['reported'] = closed['last'] + wallet.HOUR
    closed = closed.reset_index().sort_values(['reported', 'parent_id'])
    groups = closed.groupby(['symbol', 'side'])
    unique = paths[paths.tp_r.eq(0)].set_index('event_id')
    scores = pd.Series(np.nan, index=unique.index)
    for key, group in unique.groupby(['symbol', 'side']):
        if key not in groups.indices:
            continue
        prior = groups.get_group(key)
        reports = prior.reported.to_numpy()
        past = prior.net.rolling(12, min_periods=8).mean().to_numpy()
        last = np.searchsorted(reports, group.entry_ms.to_numpy(), side='left') - 1
        valid = last >= 0
        if not np.all(reports[last[valid]] < group.entry_ms.to_numpy()[valid]):
            raise ValueError('An unpublished position outcome entered the rolling estimate')
        scores.loc[group.index[valid]] = past[last[valid]]
    return paths.assign(prior_mean=paths.event_id.map(scores))


def load_paths(exchange):
    if exchange == 'Binance':
        # Reassemble corrected native fills: 43 repaired contracts plus the
        # 589 unaffected contracts, with market-wide rankability recomputed.
        paths = repaired.assembled(set(repaired.patch.affected_tickers()))
    else:
        if exchange != 'OKX':
            raise ValueError(f'Unsupported venue: {exchange}')
        paths = wallet.read_paths()
        rank = leader.leadership().dropna()
        event_clock = pd.MultiIndex.from_frame(paths[['entry_ms','symbol']])
        paths = paths[paths.tp_r.isin([0, 2]) & event_clock.isin(rank.index)].copy()
        source = pd.read_csv(wallet.SOURCE / 'signals.csv', usecols=['prior_atr_pct'])
        source.index = pd.RangeIndex(1, len(source)+1)
        paths['entry_order'] = paths.source_row_number.map(source.prior_atr_pct).fillna(np.inf)
    if paths.duplicated(['event_id','tp_r']).any() or not paths.groupby('event_id').size().eq(2).all():
        raise ValueError(f'{exchange} paths have duplicate or incomplete native target legs')
    event_rows = paths[paths.tp_r.eq(0)].copy()
    if event_rows.event_id.duplicated().any():
        raise ValueError(f'{exchange} has more than one metadata row per event')
    actual = event_rows[~event_rows.is_control]
    controls = event_rows[event_rows.is_control]
    if actual.parent_id.duplicated().any() or controls.parent_id.duplicated().any():
        raise ValueError(f'{exchange} has duplicate actual/control parent identities')
    # Controls must pass their own timestamp gate and also belong to an
    # eligible real parent. Drop eligible-control orphans before scoring.
    eligible_parent_ids = set(actual.parent_id)
    paths = paths[paths.parent_id.isin(eligible_parent_ids)].copy()
    kept_controls = paths[paths.is_control]
    if not kept_controls.parent_id.isin(eligible_parent_ids).all():
        raise ValueError(f'{exchange} control lacks an eligible actual parent')
    return scored_paths(paths)


def subset(paths, threshold):
    allowed = paths.prior_mean.notna() if threshold is None else paths.prior_mean.ge(threshold)
    real = paths[allowed & ~paths.is_control]
    return real, paths[allowed & paths.is_control & paths.parent_id.isin(real.parent_id)]


def simulate(exchange, events, slip=0.):
    if exchange == 'OKX':
        return capacity.run(events, 9, .75, slip)[0]
    if exchange == 'Binance':
        data = repaired.prepare(events)
        return repaired.scaleout.simulate(data, .75, slip)[0]
    raise ValueError(f'Unsupported venue: {exchange}')


def main(output_dir=None):
    output_dir = repair_io.new_output_dir(
        output_dir, root=wallet.ROOT, historical_dir=EXP,
        artifacts=('account_metrics.csv','summary.json'), description=__doc__)
    cfg = json.loads((EXP / 'config.json').read_text())
    paths = load_paths('OKX')
    baseline = pd.read_csv(leader.EXP / 'account_metrics.csv')
    rows = baseline[baseline.top_fraction.eq(1.)].drop(columns='top_fraction').assign(policy='all',venue='OKX').to_dict('records')
    counts = {}
    for policy, threshold in cfg['thresholds'].items():
        actual, controls = subset(paths, threshold)
        counts[policy] = dict(real=len(actual)//2, paired=len(controls)//2)
        metrics = simulate('OKX', actual)
        rows.extend(dict(policy=policy, venue='OKX', period=p, **m) for p, m in metrics.items())
        gc.collect()
    table = pd.DataFrame(rows)
    development = table[table.period.eq('dev')].sort_values('multiple', ascending=False)
    eligible = development[development.apply(wallet.qualifies, axis=1) & development.payoff_usdt.ge(2.5) &
                           development.trades.ge(100)]
    pick = None if eligible.empty else eligible.policy.iloc[0]
    checks = {}
    if pick in cfg['thresholds']:
        for exchange in ['OKX','Binance']:
            actual, controls = subset(paths if exchange=='OKX' else load_paths(exchange), cfg['thresholds'][pick])
            checks[exchange] = {}
            if exchange == 'Binance':
                metrics = simulate(exchange, actual)
                rows.extend(dict(policy=pick, venue=exchange, period=p, **m) for p,m in metrics.items())
            matched = actual[actual.parent_id.isin(controls.parent_id)]
            for name, pool, slip in [('paired_signals', matched, 0.), ('control', controls, 0.),
                                     ('slippage_5bp', actual, .0005)]:
                checks[exchange][name] = simulate(exchange, pool, slip)
    table = pd.DataFrame(rows)
    summary = dict(experiment_id=cfg['id'],output_dir=str(output_dir),pick=pick,
                   counts=counts,checks=checks,
                   binance_input=(str(repaired.EXP.relative_to(wallet.ROOT))
                                  if pick in cfg['thresholds'] else None),
                   binance_source_contract=(
                       'Gap-repaired native fills for the 43 affected contracts plus the 589 unchanged contracts; '
                       'recomputed own-entry 90d rankability and updated H1 quotes, no Binance re-selection.'
                       if pick in cfg['thresholds'] else None))
    table.to_csv(output_dir / 'account_metrics.csv', index=False)
    (output_dir / 'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))
    return dict(output_dir=str(output_dir),account_metrics=table,summary=summary)


if __name__=='__main__':
    main()

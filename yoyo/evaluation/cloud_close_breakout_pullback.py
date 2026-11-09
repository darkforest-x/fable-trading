"""Select pre-key seven-day histories containing a prior 90-day closing breakout."""
import json
import pandas as pd
import numpy as np

from yoyo.evaluation import cloud_close_direction_history as account

EXP = account.EXP.parent / 'exp-cloud-close-breakout-pullback-20261109-v1'
STEP = 12 * account.HOUR


def recent_breaks(bars):
    """Use the last 194 pre-key 12H close prices. Result is labelled at entry time."""
    close = bars.close
    if not close.index.is_unique or not close.index.is_monotonic_increasing or not np.isfinite(close).all():
        raise ValueError('Unexpected OHLC close series')
    history = close.shift(1).rolling(180, min_periods=180)
    highs = close.gt(history.max()).rolling(14, min_periods=14).max()
    lows = close.lt(history.min()).rolling(14, min_periods=14).max()
    complete = close.index.to_series().diff(193).eq(193*STEP)
    result = pd.DataFrame({'long':highs, 'short':lows}).where(complete, axis=0)
    result.index += 2*STEP
    return result


def main():
    if (EXP / 'summary.json').is_file():
        raise FileExistsError('Already evaluated')
    trend = account.tenth.trend
    paths = trend.thirds.okx_paths()
    trend.repair.check_parent_sets(paths)
    _, selected = trend.inputs('OKX', paths)
    events = selected[selected.tp_r.eq(0)].copy()
    for symbol, group in events.groupby('symbol'):
        feature = recent_breaks(trend.market.market_bars('OKX', symbol)).reindex(group.entry_ms)
        events.loc[group.index, 'recent_break'] = np.where(group.side.eq(1), feature.long, feature.short)
    coverage = selected[selected.event_id.isin(events.loc[events.recent_break.notna(), 'event_id'])]
    testing = selected[selected.event_id.isin(events.loc[events.recent_break.eq(1), 'event_id'])]
    summary, records = {}, []
    for label, pool in [('coverage', coverage), ('breakout', testing)]:
        real = pool[~pool.is_control]
        metrics, pos, eq = account.simulate(real, 0.)
        records.extend(dict(scenario=label, period=p, **m) for p,m in metrics.items())
        summary[label] = dict(eligible=len(real)//2, full=metrics['all'], dev=metrics['dev'],
                              dev_pass=trend.second.meets(metrics['dev']), full_pass=trend.second.meets(metrics['all']))
        pos.to_csv(EXP / f'funded_{label}.csv.gz')
        eq.to_csv(EXP / f'hourly_{label}.csv.gz', index_label='hour_end_utc')
        if label == 'breakout' and summary[label]['dev_pass'] and metrics['dev']['final_usdt'] > summary['coverage']['dev']['final_usdt']:
            controls = pool[pool.is_control & pool.parent_id.isin(real.parent_id)]
            paired = real[real.parent_id.isin(controls.parent_id)]
            for name, cohort, slip in [('paired', paired, 0.), ('control', controls, 0.), ('stress', real, .0005)]:
                checked = account.simulate(cohort, slip)[0]
                records.extend(dict(scenario=name, period=p, **m) for p,m in checked.items())
    pd.DataFrame(records).to_csv(EXP / 'account_metrics.csv', index=False)
    events[['event_id','symbol','entry_ms','side','is_control','recent_break']].to_csv(EXP / 'decisions.csv.gz',index=False)
    (EXP / 'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()

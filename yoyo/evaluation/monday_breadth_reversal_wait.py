"""How long to wait for zero additional lower first touches after a 25-coin alarm.

Reuse the frozen five-minute alarm and scored weekly first-touch universe. Observation
counts use bar closes in (alarm known, alarm known + X], excluding triggering symbols.
Every arm exits at the open 24h after alarm. Thirty-minute trades/controls are cached.
"""
from itertools import combinations

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_reversal_window as base

WAITS = (10, 20, 30, 60)
OUT = base.ARCHIVE/'exp-monday-breadth-reversal-wait-20261107-v1'


def observation_counts(frozen):
    source = base.scored_touches()
    stream = source.loc[source.side.eq(1), ['ms','symbol']].sort_values('ms')
    times = stream.ms.to_numpy()
    records = []
    for r in frozen.query('window == 5 and side == 1').itertuples(index=False):
        bar_open = r.known.value//10**6 - base.BAR_MS
        left = times.searchsorted(bar_open, side='left')
        right = times.searchsorted(bar_open, side='right')
        triggered = set(stream.symbol.iloc[left:right])
        assert len(triggered) == r.trigger_n
        for wait in WAITS:
            end = times.searchsorted(bar_open+60_000*wait, side='right')
            extra = set(stream.symbol.iloc[right:end]) - triggered
            if wait == 30:
                assert len(extra) == r.further_n
            records.append(dict(window=5, side=1, known=r.known, trigger_n=r.trigger_n,
                                wait=wait, further_n=len(extra)))
    result = pd.DataFrame(records)
    assert result.known.nunique() == 122
    counts = result.pivot(index='known', columns='wait', values='further_n')
    assert counts.shape == (122,len(WAITS)) and counts.diff(axis=1).iloc[:,1:].ge(0).all().all()
    return result


def clustered_interval(group, values):
    """Resample UTC Monday-Sunday weeks together, using each alarm as the estimand."""
    work = pd.DataFrame(dict(week=group.known.dt.tz_localize(None).dt.to_period('W-SUN'), value=values)).dropna()
    blocks = work.groupby('week').value.agg(['sum','count'])
    if len(blocks) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(base.SEED)
    ix = rng.integers(len(blocks), size=(9999,len(blocks)))
    means = blocks['sum'].to_numpy()[ix].sum(axis=1) / blocks['count'].to_numpy()[ix].sum(axis=1)
    return np.quantile(means, [.025,.975])


def summaries(events):
    all_rows, contrasts = [], []
    for period in ('all','early','late'):
        sample = events if period == 'all' else events[events.period.eq(period)]
        for asset, trials in sample.groupby('asset'):
            for wait, arm in trials.groupby('wait'):
                active = arm[arm.further_n.eq(0)]
                paired = active.net_pct - active.control_pct
                lo, hi = clustered_interval(active, paired)
                all_rows.append(dict(period=period, asset=asset, wait=wait, alarms=len(arm), trades=len(active),
                                     win_pct=100*active.net_pct.gt(0).mean(), mean_net_pct=active.net_pct.mean(),
                                     median_net_pct=active.net_pct.median(), worst_net_pct=active.net_pct.min(),
                                     per_alarm_pct=active.net_pct.sum()/len(arm), matched=paired.count(),
                                     excess_pp=paired.mean(), excess_ci_lo=lo, excess_ci_hi=hi))
            returns = trials.pivot(index='known',columns='wait',values='net_pct')
            enter = trials.pivot(index='known',columns='wait',values='further_n').eq(0)
            for early, late in combinations(WAITS, 2):
                both = enter[late]
                dropped = enter[early] & ~both
                assert not (both & ~enter[early]).any()
                time_effect = (returns[late] - returns[early]).where(both, 0.)
                filter_effect = -returns[early].where(dropped, 0.)
                diff = returns[late].where(both,0.) - returns[early].where(enter[early],0.)
                assert np.allclose(time_effect + filter_effect, diff)
                low, high = clustered_interval(diff.rename('diff').reset_index(),diff.to_numpy())
                contrasts.append(dict(period=period, asset=asset, earlier=early, later=late, alarms=len(diff),
                                      both=int(both.sum()), dropped=int(dropped.sum()),
                                      early_on_both=returns.loc[both,early].mean(), late_on_both=returns.loc[both,late].mean(),
                                      early_on_dropped=returns.loc[dropped,early].mean(),
                                      dropped_early_wins=int(returns.loc[dropped,early].gt(0).sum()),
                                      timing_per_alarm_pp=time_effect.mean(), filter_per_alarm_pp=filter_effect.mean(),
                                      diff_per_alarm_pp=diff.mean(), diff_ci_lo=low, diff_ci_hi=high))
    return pd.DataFrame(all_rows), pd.DataFrame(contrasts)


def main():
    frozen = pd.read_csv(base.OUT/'alarms.csv', parse_dates=['known'])
    counts = observation_counts(frozen)
    cached_events = pd.read_csv(base.OUT/'events.csv', parse_dates=['known','decision']).query('window == 5')
    cached_controls = pd.read_csv(base.OUT/'controls.csv.gz', parse_dates=['known','control_decision']).query('window == 5')
    events = [cached_events.assign(wait=30)]
    controls = [cached_controls.assign(wait=30)]
    for asset in ('BTC','ETH','SOL'):
        frame = base.market_prices(asset)
        for wait in WAITS:
            if wait == 30:
                continue
            trades, random = base.evaluate(asset, counts[counts.wait.eq(wait)], observe_minutes=wait,
                                           frame=frame, exclusion_times=frozen.known.unique())
            events.append(trades.assign(wait=wait))
            controls.append(random.assign(wait=wait))
    results = pd.concat(events,ignore_index=True).sort_values(['asset','known','wait'])
    assert len(results) == 122*3*len(WAITS) and not results.duplicated(['asset','known','wait']).any()
    assert results.groupby(['asset','known']).period.nunique().eq(1).all()
    assert set(results.period) == {'early','late'}
    assert results.groupby(['asset','known']).pre_alarm_1h_pct.agg(lambda x: np.ptp(x)).max() < 1e-10
    summary, comparisons = summaries(results)
    OUT.mkdir(exist_ok=True)
    results.to_csv(OUT/'events.csv',index=False)
    pd.concat(controls,ignore_index=True).to_csv(OUT/'controls.csv.gz',index=False)
    summary.to_csv(OUT/'summary.csv',index=False)
    comparisons.to_csv(OUT/'paired_waits.csv',index=False)
    print(summary.query('period == "all" and asset == "BTC"')[['wait','alarms','trades']].to_string(index=False))


if __name__ == '__main__':
    main()

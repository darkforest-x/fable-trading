"""Fixed UTC candles of weekly first touches, including candles in the alarm cooldown.

Use the archived symbol, direction and native 5m touch-open time. The initial set
uses [open, close), the follower set [close, close+30m). The return starts at
close+30m and ends at close+24h. All intervals refer to bar opens.
"""
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_bar_rebounds as previous


OUT = previous.census.ACTIVE/'exp-monday-breadth-fixed-timeframes-20261108-v1'
MINUTES = (5, 10, 15, 30, 60, 240, 720)
STEP = previous.STEP
WAIT = 6*STEP
LEDGER_END = pd.Timestamp('2026-10-28', tz='UTC')


def monitored_bars(times):
    """Scheduled count of observable native opens over the next 30m (NY Mon builds the range)."""
    return sum(((times+i*STEP).tz_convert('America/New_York').dayofweek != 0).astype('int8')
               for i in range(6))


def fixed_bars(touches, minutes, followers):
    span = pd.Timedelta(minutes=minutes)
    grouped = touches.assign(known=touches.touch.dt.floor(span)+span)
    buckets = grouped.groupby(['known', 'side']).symbol.nunique().unstack(fill_value=0)
    exclusions = buckets.index[buckets.max(axis=1).ge(25)]
    lower = grouped[grouped.side.eq(1)].groupby('known')
    names = lower.symbol.agg(frozenset)
    valid = names.index+WAIT <= LEDGER_END
    bars = names[valid].rename('symbols').to_frame()
    bars['initial'] = bars.symbols.map(len)
    bars['new_coins'] = [len(followers(known)-initial) for known,initial in bars.symbols.items()]
    bars['continued'] = bars.new_coins.gt(0)
    bars['band'] = pd.cut(bars.initial, previous.CUTS, labels=previous.LABELS)
    bars['last_touch_age_min'] = (bars.index-(lower.touch.max().loc[bars.index]+STEP))/pd.Timedelta(minutes=1)
    bars['monitor_bars'] = monitored_bars(bars.index)
    assert bars.loc[bars.monitor_bars.eq(0),'new_coins'].eq(0).all()
    return bars.drop(columns='symbols').reset_index(), exclusions, int((~valid).sum())


def control_clock(index, minutes):
    # Match the actual close phase and exclude wholly unmonitored initial candles.
    # A 12h candle cannot enclose a full NY Monday, so the two endpoints suffice.
    initial_close = index-WAIT
    aligned = initial_close.floor(f'{minutes}min') == initial_close
    positions = np.flatnonzero(aligned)
    ends = initial_close[positions]
    starts = ends-pd.Timedelta(minutes=minutes)
    active = ((starts.tz_convert('America/New_York').dayofweek != 0)
              | ((ends-STEP).tz_convert('America/New_York').dayofweek != 0))
    mask = np.zeros(len(index), dtype=bool)
    mask[positions] = active & (ends+WAIT <= LEDGER_END)
    phases = np.zeros(len(index), dtype='int8')
    phases[positions] = monitored_bars(ends)
    return mask, phases


def return_cache(frame, baseline):
    prior = baseline.dropna(subset=['net_pct'])
    stored = pd.Series(prior.net_pct.to_numpy(), index=frame.index.get_indexer(prior.decision))
    assert stored.index.is_unique and (stored.index>=0).all()

    def get_returns(asset, requested, prices):
        nonlocal stored
        positions = np.unique(requested)
        new = np.setdiff1d(positions,stored.index)
        if len(new):
            extra = previous.cached_returns(asset,new,prices)
            stored = pd.concat([stored,extra],verify_integrity=True)
        return stored.reindex(positions)
    return get_returns


def main():
    _, touches = previous.census.ledger_rows()
    lower = touches[touches.side.eq(1)].groupby('touch').symbol.agg(frozenset).to_dict()

    @lru_cache(maxsize=None)
    def followers(known):
        return frozenset().union(*(lower.get(known+i*STEP, ()) for i in range(6)))

    history = pd.read_csv(previous.OUT/'events.csv.gz',parse_dates=['known','decision'],
                          float_precision='round_trip')
    history_summary = pd.read_csv(previous.OUT/'summary.csv',float_precision='round_trip')
    frames = {}
    bounds = []
    for minutes in MINUTES:
        bars, excluded, dropped = fixed_bars(touches,minutes,followers)
        frames[minutes] = bars,excluded
        bounds.append(dict(minutes=minutes,lower_bars=len(bars),incomplete_followup=dropped,
                           first_close=bars.known.min(),last_close=bars.known.max()))
    baseline = history[history.asset.eq('BTC')]
    checked = frames[5][0].merge(baseline,on='known',validate='one_to_one',suffixes=('', '_old'))
    assert len(checked)==len(baseline)==len(frames[5][0])
    assert checked.initial.eq(checked.initial_old).all() and checked.new_coins.eq(checked.new_coins_old).all()

    metadata = ['known','last_touch_age_min','monitor_bars']
    saved = [history.merge(frames[5][0][metadata],on='known',validate='many_to_one').assign(minutes=5)]
    for asset in previous.ASSETS:
        prices = previous.prices(asset)
        cached = return_cache(prices,history[history.asset.eq(asset)])
        for minutes in MINUTES[1:]:
            bars,excluded = frames[minutes]
            mask,phases = control_clock(prices.index,minutes)
            trial = previous.evaluate(asset,bars,excluded,frame=prices,control_mask=mask,
                                      monitor_bars=phases,returns_for=cached)
            saved.append(trial.assign(minutes=minutes))
        print(f'Completed {asset}',flush=True)
    events = pd.concat(saved,ignore_index=True)
    assert not events.duplicated(['asset','minutes','known']).any()
    # Retain the frozen native summary verbatim. No notification cooldown is simulated
    # on the synthetic timeframe candles; every positive candle is included.
    summaries = [history_summary.assign(minutes=5)]
    for minutes,part in events[events.minutes.ne(5)].groupby('minutes'):
        summary = previous.summaries(part.drop(columns=['cooldown']))
        summaries.append(summary.assign(minutes=minutes))
    OUT.mkdir(exist_ok=True)
    events.to_csv(OUT/'events.csv.gz',index=False)
    pd.concat(summaries,ignore_index=True).to_csv(OUT/'summary.csv',index=False)
    pd.DataFrame(bounds).to_csv(OUT/'coverage.csv',index=False)


if __name__ == '__main__':
    main()

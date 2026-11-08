"""Compare simultaneous 12H V1.7 confirmations on two exchanges with commonly observable keys."""
import gc
import json
from collections import Counter
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as patch
from yoyo.evaluation import cloud_close_binance_transfer as venue
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as split
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds
from yoyo.monitor.monday_fib import week_window

EXP = wallet.ROOT/'experiments/active/exp-cloud-close-cross-venue-key-20261109-v1'
BEGIN = max(venue.START, wallet.START)
END = min(venue.END, wallet.END)
BAR = leader.BAR
HOUR = wallet.HOUR


@lru_cache(maxsize=None)
def week_bounds(stamp):
    window = week_window(stamp-BAR)
    first_key_open = (window['end']+BAR-1)//BAR*BAR
    return window['start'], first_key_open


def observed(clock, stamp, anchor):
    """Require every native hourly opening from NY Monday through the first H1 of the new 12H bar."""
    left, right = clock.searchsorted([anchor, stamp])
    hours = (stamp-anchor)//HOUR
    return (right < len(clock) and clock[right] == stamp and clock[left] == anchor
            and right-left == hours)


def peer_market(paths):
    keys = pd.concat([p.loc[p.tp_r.eq(0), ['entry_ms','symbol']] for p in paths.values()]).drop_duplicates()
    keys = keys[keys.entry_ms.ge(BEGIN) & keys.entry_ms.lt(END)]
    rank = leader.leadership()
    keys['okx_ranked'] = pd.MultiIndex.from_frame(keys[['entry_ms','symbol']]).isin(rank.index)
    coverage = pd.read_csv(wallet.SOURCE/'input_coverage.csv', dtype={'symbol':str})
    okx_symbols = set(coverage.symbol)
    member_counts, by_symbol = Counter(), {}
    for file in venue.WORK.glob('*/quotes.npz'):
        ticker = file.parent.name
        corrected = patch.WORK/ticker/'quotes.npz'
        with np.load(corrected if corrected.is_file() else file) as data:
            times = set(data['rank_times'])
            member_counts.update(times)
            if ticker.removesuffix('USDT') in okx_symbols:
                by_symbol[ticker.removesuffix('USDT')] = times
    if len(member_counts) == 0 or len(by_symbol) < 20:
        raise ValueError('Missing venue archives')
    lookup = {}
    for symbol, group in keys.groupby('symbol'):
        if symbol not in by_symbol:
            lookup.update({(ts,symbol):'no_exact_common_symbol' for ts in group.entry_ms})
            continue
        ok_clock, ok_bars = wallet.quotes(symbol)
        bn_clock, bn_bars = patch.quotes(symbol)
        file = wallet.SOURCE/'inputs'/symbol/'bars12h.csv.gz'
        if not file.is_file():
            file = wallet.TOP/'inputs'/symbol/'bars12h.csv.gz'
        actual12 = set(pd.read_csv(file, usecols=['time']).time)
        for event in group.itertuples():
            ts = int(event.entry_ms)
            anchor, first_key = week_bounds(ts)
            status = 'common'
            if not event.okx_ranked or ts not in by_symbol[symbol] or member_counts[ts]<10:
                status = 'unranked_on_at_least_one_venue'
            elif not (observed(ok_clock,ts,anchor) and observed(bn_clock,ts,anchor)
                      and all(t in actual12 for t in range(first_key,ts,BAR))):
                status = 'missing_week_or_open'
            else:
                a = ok_bars[ok_clock.searchsorted(ts),0]
                b = bn_bars[bn_clock.searchsorted(ts),0]
                if max(a,b)/min(a,b)>1.05:
                    status = 'price_basis_or_units'
            lookup[ts,symbol] = status
    return lookup


def at_cutoff(events, quotes):
    """Flatten only positions still open at END using the actual last included H1 close."""
    result = events.copy()
    mask = result.exit_bar_ms.ge(END)
    for symbol, g in result[mask].groupby('symbol'):
        clock, bars = quotes(symbol)
        pos = clock.searchsorted(END-HOUR)
        if pos == len(clock) or clock[pos] != END-HOUR:
            raise ValueError(f'{symbol}: no observed cutoff close')
        result.loc[g.index,['exit_bar_ms','exit_price','exit_phase','exit_reason','exit_fee_rate']] = [
            END-HOUR,bars[pos,3],'close','common_cutoff',wallet.TAKER]
    return result, int(mask.sum())


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Cross-venue comparison already exists')
    okx = thirds.okx_paths()
    repair.check_parent_sets(okx)
    pools = {'OKX':okx, 'Binance':patch.assembled(set(patch.patch.affected_tickers()))}
    coverage = peer_market(pools)
    originals = {}
    for label, paths in pools.items():
        real = paths[~paths.is_control & paths.tp_r.eq(0)]
        originals[label] = set(zip(real.symbol,real.entry_ms,real.side))
        if len(originals[label]) != len(real):
            raise ValueError(f'{label}: duplicate native key')
    inputs, accounts, summary = [], [], {}
    for label, paths in pools.items():
        other = 'Binance' if label=='OKX' else 'OKX'
        quotes = wallet.quotes if label=='OKX' else patch.quotes
        paths = paths[paths.entry_ms.ge(BEGIN) & paths.entry_ms.lt(END)].copy()
        together = set(originals[label]) & originals[other]
        paths['coverage'] = [coverage[t,s] for t,s in zip(paths.entry_ms,paths.symbol)]
        paths['dual'] = [(s,t,d) in together for s,t,d in zip(paths.symbol,paths.entry_ms,paths.side)]
        keys = paths.loc[paths.tp_r.eq(0),['event_id','parent_id','is_control','symbol','entry_ms','side','coverage','dual']]
        inputs.append(keys.assign(venue=label))
        eligible, clipped = at_cutoff(paths[paths.coverage.eq('common')], quotes)
        actual = eligible[~eligible.is_control]
        dual = actual[actual.dual]
        control = eligible[eligible.is_control & eligible.parent_id.isin(dual.parent_id)]
        def evaluate(events, slip=0.):
            data = split.pair_data(events[events.tp_r.eq(0)],events[events.tp_r.eq(2)],9,
                                   start=BEGIN,end=END,quote_loader=quotes)
            metrics, trades, equity = split.simulate(data,2/3,slip)
            if not np.isclose(1000+trades.cash_pnl.sum(),equity.nav.iloc[-1],rtol=1e-8,atol=.005):
                raise ValueError(f'{label}: native cash reconciliation failed')
            return metrics,trades,equity
        summary[label] = dict(source_actual=len(keys[~keys.is_control]),common_actual=len(actual)//2,
                              dual_actual=len(dual)//2,paired=len(control)//2,cutoff_legs=clipped,
                              coverage_actual=keys[~keys.is_control].coverage.value_counts().to_dict())
        for name,events,slip in [('common',actual,0.),('dual',dual,0.),('matched_dual',dual[dual.parent_id.isin(control.parent_id)],0.),
                                 ('matched_control',control,0.),('stress',dual,.0005)]:
            if name not in ('common','dual') and not summary[label]['dev_pass']:
                break
            metrics, trades, equity = evaluate(events,slip)
            accounts.extend(dict(venue=label,policy=name,period=p,**m) for p,m in metrics.items())
            if name in ('common','dual'):
                trades.to_csv(EXP/f'funded_{label}_{name}.csv.gz')
                equity.to_csv(EXP/f'hourly_{label}_{name}.csv.gz',index_label='hour_end_utc')
            if name=='dual':
                summary[label].update(dev_pass=second.meets(metrics['dev']),full_pass=second.meets(metrics['all']))
            gc.collect()
    if summary['OKX']['dual_actual'] != summary['Binance']['dual_actual']:
        raise ValueError('Same-clock dual keys differ between venues')
    pd.concat(inputs).to_csv(EXP/'key_coverage.csv.gz',index=False)
    table = pd.DataFrame(accounts)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__ == '__main__':
    main()

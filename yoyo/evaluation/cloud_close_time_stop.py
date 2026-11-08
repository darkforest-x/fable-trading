"""Test one fee-aware 48-hour progress check inside the original cloud 12H account."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as binance
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds
from yoyo.evaluation import cloud_close_volatility_expansion as market

wallet = thirds.wallet
EXP = wallet.ROOT/'experiments/active/exp-cloud-close-time-stop-20261109-v1'
HOURS = 48


def schedule(pool, quote_loader):
    """At entry+48h use entry price and the preceding completed H1 close; preserve opening-order priority."""
    target = pool[pool.tp_r.eq(2)].set_index('event_id')
    runner = pool[pool.tp_r.eq(0)].set_index('event_id')
    if not target.index.is_unique or not runner.index.is_unique or set(target.index)!=set(runner.index):
        raise ValueError('Every event must have its two original native paths')
    target = target.loc[runner.index]
    check_time = runner.entry_ms+HOURS*wallet.HOUR
    def still_open(leg):
        return leg.exit_bar_ms.gt(check_time) | (leg.exit_bar_ms.eq(check_time) & leg.exit_phase.ne('open'))
    eligible = runner.loc[still_open(target) & still_open(runner)].copy()
    audit = []
    for symbol, group in eligible.groupby('symbol'):
        clock, prices = quote_loader(symbol)
        close_ms = check_time.loc[group.index].to_numpy()
        end = clock.searchsorted(close_ms)
        begin = clock.searchsorted(group.entry_ms.to_numpy())
        valid = (end < len(clock)) & (end-begin == HOURS)
        rows = np.flatnonzero(valid)
        valid[rows] &= (clock[end[rows]]==close_ms[rows]) & (clock[begin[rows]]==group.entry_ms.to_numpy()[rows])
        rows = np.flatnonzero(valid)
        if len(rows):
            # The source is a unique hourly UTC grid. Exactly 48 intervals with matching
            # endpoints forces every observation to exist, including the decision's predecessor.
            if not (clock[end[rows]-1]==close_ms[rows]-wallet.HOUR).all():
                raise ValueError('Incomplete 48H decision interval')
            prior = prices[end[rows]-1,3]
            entry = group.entry_price.to_numpy()[rows]
            net_per_unit = group.side.to_numpy()[rows]*(prior-entry)-wallet.TAKER*(entry+prior)
            for i,net,mark,entry_price in zip(rows,net_per_unit,prior,entry):
                audit.append((group.index[i],int(close_ms[i]),float(mark),float(prices[end[i],0]),float(net/entry_price),bool(net <= 0)))
    observed = pd.DataFrame(audit,columns=['event_id','decision_ms','prior_closed_mark','executable_open','marked_net_return','exit_order']).set_index('event_id')
    observed['exit_order'] = observed.exit_order.astype(bool)
    orders = observed.loc[observed.exit_order]
    revised = pool.copy()
    metadata = dict(both_still_live=len(eligible),observed=len(observed),changed=len(orders))
    if orders.empty:
        return revised, observed, metadata
    mask = revised.event_id.isin(orders.index)
    ids = revised.loc[mask,'event_id']
    revised.loc[mask,'exit_bar_ms'] = ids.map(orders.decision_ms)
    revised.loc[mask,'exit_price'] = ids.map(orders.executable_open)
    revised.loc[mask,'exit_reason'] = '48h_unscaled_nonpositive'
    revised.loc[mask,'exit_phase'] = 'open'
    revised.loc[mask,'exit_fee_rate'] = wallet.TAKER
    revised.loc[mask,'same_bar_ambiguous'] = False
    if 'gap_flag' in revised:
        revised.loc[mask,'gap_flag'] = False
    if mask.sum()!=2*len(orders) or not (revised.exit_bar_ms<=pool.exit_bar_ms).all():
        raise ValueError('Progress deadline changed a non-live pair')
    return revised,observed,metadata


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Frozen account already evaluated')
    old = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    rows = old[old.scenario.eq('actual')].assign(policy='reference').to_dict('records')
    summary = {}
    for name in ('OKX','Binance'):
        if name=='OKX':
            paths = thirds.okx_paths()
            repair.check_parent_sets(paths)
            loader = wallet.quotes
        else:
            paths = binance.assembled(set(binance.patch.affected_tickers()))
            loader = binance.quotes
        revised,audit,meta = schedule(paths,loader)
        real = revised[~revised.is_control]
        controls = revised[revised.is_control & revised.parent_id.isin(real.parent_id)]
        audit.to_csv(EXP/f'decisions_{name}.csv.gz')
        for tag,sample,slip in [('actual',real,0.),('paired',real[real.parent_id.isin(controls.parent_id)],0.),
                                ('control',controls,0.),('stress',real,.0005)]:
            metrics,trades,eq = market.fund(name,sample,slip)
            rows.extend(dict(venue=name,policy='time_stop',scenario=tag,period=p,**m) for p,m in metrics.items())
            if tag=='actual':
                hit = trades.index.isin(audit.index[audit.exit_order])
                summary[name] = dict(**meta,actual_changed=int(real.exit_reason.eq('48h_unscaled_nonpositive').sum()//2),
                                     funded_changed=int(hit.sum()),dev_pass=second.meets(metrics['dev']),
                                     full_pass=second.meets(metrics['all']))
                trades.to_csv(EXP/f'funded_{name}.csv.gz')
                eq.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
            del eq,trades
            gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__ == '__main__':
    main()

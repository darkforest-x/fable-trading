"""Check the selected profit budget against two archived synchronous minute NAV windows on OKX."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_drawdown_bounds as dd
from yoyo.evaluation import cloud_close_partial_profit_budget as search

EXP = search.EXP


def main():
    out = EXP/'minute_drawdown_check.json'
    if out.exists():
        raise FileExistsError('Selected add-on minute drawdown already checked')
    summary = json.loads((EXP/'summary.json').read_text())
    fraction = summary['dev_pick']
    if fraction!=.125:
        raise ValueError('Frozen selected fraction differs')
    parents = pd.read_csv(EXP/f'funded_{fraction}.csv.gz',dtype={'symbol':str})
    adds = pd.read_csv(EXP/f'addons_{fraction}.csv.gz',dtype={'symbol':str})
    equity = pd.read_csv(EXP/f'hourly_{fraction}.csv.gz',index_col='hour_end_utc',parse_dates=True)
    paths,opps,_ = search.adapter.read_inputs()
    selected,_ = search.adapter._selected_originals(paths,9)
    candidates,_ = search.adapter._validate_reinvestment_candidates(selected,opps)
    child = adds.merge(candidates[['event_id','exit_phase','exit_bar_ms','exit_price']],on='event_id',suffixes=('','_path'),validate='one_to_one')
    if len(child)!=len(adds) or not child.exit_bar_ms.eq(child.exit_bar_ms_path).all() or not np.allclose(child.exit_price,child.exit_price_path,rtol=1e-10,atol=1e-14):
        raise ValueError('Actual addition exits differ from native path')
    child['signed_size'] = child.side*child.quantity
    base = paths.merge(parents[['event_id','target_quantity','runner_quantity']],on='event_id',validate='many_to_one')
    base['signed_size'] = base.side*np.where(base.tp_r.eq(2),base.target_quantity,base.runner_quantity)
    cols = ['symbol','entry_ms','exit_bar_ms','exit_phase','signed_size']
    legs = pd.concat([base[cols],child[cols]],ignore_index=True)
    quotes = pd.read_csv(dd.wallet.ROOT/'data/research/cloud_close_drawdown_bounds_1m_20261109/quotes.csv',dtype={'symbol':str})
    values = []
    for stamp in [dd.PEAK,pd.Timestamp('2025-11-12T14:00Z')]:
        time_ms = stamp.value//1_000_000
        end = stamp+pd.Timedelta(hours=1)
        active=legs[legs.entry_ms.le(time_ms) & (legs.exit_bar_ms.gt(time_ms) | (legs.exit_bar_ms.eq(time_ms)&legs.exit_phase.ne('open')))]
        if active.exit_bar_ms.eq(time_ms).any():
            raise ValueError('This exact minute-close test requires no intrahour liquidation')
        nav = np.full(60,equity.loc[end,'open_exit_nav'])
        for symbol,group in active.groupby('symbol'):
            block = quotes[quotes.symbol.eq(symbol)&quotes.time.between(time_ms,time_ms+dd.wallet.HOUR-1)].sort_values('time')
            if not np.array_equal(block.time,time_ms+np.arange(60)*60_000):
                raise ValueError('Missing simultaneous minute mark')
            clock,hourly = dd.wallet.quotes(symbol)
            i = clock.searchsorted(time_ms)
            bars = [block.open.iloc[0],block.high.max(),block.low.min(),block.close.iloc[-1]]
            if clock[i]!=time_ms or not np.allclose(bars,hourly[i],rtol=1e-10,atol=1e-13):
                raise ValueError('Fine quotes do not reconcile with native H1')
            nav += group.signed_size.sum()*(block.close.to_numpy()-hourly[i,0])
        if not np.isclose(nav[-1],equity.loc[end,'nav'],rtol=1e-9,atol=1e-6):
            raise ValueError('Minute-close ending NAV does not reconcile with funded equity')
        values.append(nav)
    peak,low = values[0].max(),values[1].min()
    result=dict(dev_pick=fraction,observed_peak_usdt=float(peak),observed_trough_usdt=float(low),
                peak_minute_close_utc=(dd.PEAK+pd.Timedelta(minutes=int(values[0].argmax())+1)).isoformat(),
                trough_minute_close_utc=(pd.Timestamp('2025-11-12T14:00Z')+pd.Timedelta(minutes=int(values[1].argmin())+1)).isoformat(),
                observed_drawdown_lower_pct=float(100*(1-low/peak)),scope='strictly ordered synchronous minute closes, lifetime lower bound')
    result['violates_35']=result['observed_drawdown_lower_pct']>35.
    out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(result)


if __name__=='__main__':
    main()

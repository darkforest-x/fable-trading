"""Screen closed V1.7 keys for a directional close beyond the preceding native 12H range."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as repaired
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds
from yoyo.evaluation import cloud_close_volatility_expansion as market

EXP = wallet.ROOT/'experiments/active/exp-cloud-close-prior-break-20261109-v1'
BAR = 12*wallet.HOUR


def screen(venue, paths):
    """Compare close[t] with high or low[t-1], using only adjacent native 12H bars at the entry clock."""
    f = paths.loc[paths.tp_r.eq(0),['symbol','side','is_control','event_id','parent_id','entry_ms']].copy()
    for symbol,group in f.groupby('symbol'):
        bars = market.market_bars(venue,symbol)
        clock = group.entry_ms.to_numpy(dtype='int64')
        key = bars.reindex(clock-BAR)
        prior = bars.reindex(clock-2*BAR)
        edge = np.where(group.side.eq(1),prior.high,prior.low)
        f.loc[group.index,'directional_clearance_pct'] = group.side.to_numpy()*(key.close.to_numpy()/edge-1)*100
    actual = f[~f.is_control]
    unknown = int(actual.directional_clearance_pct.isna().sum())
    passing = paths[paths.event_id.isin(f.loc[f.directional_clearance_pct.gt(0),'event_id'])]
    real = passing[~passing.is_control]
    controls = passing[passing.is_control & passing.parent_id.isin(real.parent_id)]
    f.to_csv(EXP/f'events_{venue}.csv.gz',index=False)
    return real,controls,dict(actual_observed=len(actual)-unknown,actual_missing=unknown,
                              actual_pass=len(real)//2,both_pass_pairs=len(controls)//2)


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Frozen prior-range breakout experiment already exists')
    original = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    rows = original[original.scenario.eq('actual')].assign(policy='unfiltered').to_dict('records')
    summary = {}
    for venue in ['OKX','Binance']:
        if venue=='OKX':
            paths = thirds.okx_paths()
            repair.check_parent_sets(paths)
        else:
            paths = repaired.assembled(set(repaired.patch.affected_tickers()))
        real,controls,counts = screen(venue,paths)
        values,trades,equity = market.fund(venue,real,0.)
        trades.to_csv(EXP/f'funded_{venue}.csv.gz')
        equity.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
        dev,full = second.meets(values['dev']),second.meets(values['all'])
        summary[venue] = dict(**counts,dev_pass=dev,full_pass=full)
        rows.extend(dict(venue=venue,policy='prior_break',scenario='actual',period=p,**m) for p,m in values.items())
        if dev:
            for name,pool,slip in [('paired',real[real.parent_id.isin(controls.parent_id)],0.),
                                   ('control',controls,0.),('stress',real,.0005)]:
                checks = market.fund(venue,pool,slip)[0]
                rows.extend(dict(venue=venue,policy='prior_break',scenario=name,period=p,**m) for p,m in checks.items())
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()

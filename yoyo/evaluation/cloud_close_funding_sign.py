"""Use the previous settled funding rate to screen a frozen 12-contract Binance V1.7 panel."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as repaired
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_volatility_expansion as expansion

EXP = wallet.ROOT/'experiments/active/exp-cloud-close-funding-sign-20261109-v1'
ARCHIVE = EXP.parent/'exp-monday-1618-flow-20261023-v1'
BEGIN = pd.Timestamp('2022-09-01',tz='UTC').value//1_000_000
END = repaired.venue.END


def asof(ticker, clocks):
    """Use only prior-month or current-month official settlements preceding the decision minute."""
    months = pd.period_range('2022-09','2026-09',freq='M')
    files = [ARCHIVE/'inputs'/'funding'/ticker/f'{ticker}-fundingRate-{m}.zip' for m in months]
    series = pd.concat([pd.read_csv(f) for f in files if f.is_file()]).sort_values('calc_time')
    times = series.calc_time.to_numpy(dtype='int64')
    intervals = series.funding_interval_hours.to_numpy(dtype='float64')
    rates = series.last_funding_rate.to_numpy(dtype='float64')
    if series.calc_time.duplicated().any() or np.any(intervals<=0) or not np.isfinite(rates).all():
        raise ValueError(f'{ticker} ambiguous funding history')
    pos = np.searchsorted(times,clocks-60_000,side='right')-1
    prev = np.maximum(pos,0)
    fresh = (pos>=0) & ((clocks-times[prev])<=intervals[prev]*wallet.HOUR+60_000)
    return np.where(fresh,rates[prev],np.nan), np.where(fresh,times[prev],-1), sum(f.is_file() for f in files)


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Frozen funding pilot already exists')
    panel = json.loads((ARCHIVE/'config.json').read_text())['symbols']
    paths = repaired.assembled(set(repaired.patch.affected_tickers()))
    paths = paths[(paths.symbol+'USDT').isin(panel) & paths.entry_ms.ge(BEGIN) & paths.entry_ms.lt(END)].copy()
    events = paths.loc[paths.tp_r.eq(0),['event_id','parent_id','symbol','side','is_control','entry_ms']].copy()
    months = {}
    for symbol,g in events.groupby('symbol'):
        r,t,n = asof(symbol+'USDT',g.entry_ms.to_numpy())
        events.loc[g.index,'known_rate'] = r
        events.loc[g.index,'settled_ms'] = t
        months[symbol] = n
    events['admitted'] = (events.known_rate*events.side).lt(0)
    events.to_csv(EXP/'event_rates.csv.gz',index=False)
    pools = paths[paths.event_id.isin(events.loc[events.known_rate.notna(),'event_id'])]
    real = pools[~pools.is_control]
    filtered = pools[pools.event_id.isin(events.loc[events.admitted,'event_id'])]
    signal = filtered[~filtered.is_control]
    controls = filtered[filtered.is_control & filtered.parent_id.isin(signal.parent_id)]
    summary = dict(month_archives=months,actual_missing=int(events[~events.is_control].known_rate.isna().sum()),
                   eligible=len(real)//2,filtered=len(signal)//2,eligible_pairs=len(controls)//2,
                   real_by_side=real[real.tp_r.eq(0)].side.value_counts().to_dict(),
                   filtered_by_side=signal[signal.tp_r.eq(0)].side.value_counts().to_dict())
    rows = []
    for label,pool,slip in [('panel',real,0.),('received_last',signal,0.),('paired',signal[signal.parent_id.isin(controls.parent_id)],0.),
                            ('control',controls,0.),('stress',signal,.0005)]:
        if label not in ('panel','received_last') and not summary['dev_pass']:
            break
        m,trades,eq = expansion.fund('Binance',pool,slip)
        if label in ('panel','received_last'):
            trades.to_csv(EXP/f'funded_{label}.csv.gz')
            eq.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
        if label=='received_last':
            summary.update(dev_pass=second.meets(m['dev']),full_pass=second.meets(m['all']))
        rows.extend(dict(policy=label,period=p,**r) for p,r in m.items())
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()

"""Apply cloud reclaim geometry to synchronized ETH/BTC closes and trade both perpetual legs."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_pair_branch as spread

wallet=spread.wallet
EXP=spread.EXP.parent/'exp-cloud-close-eth-btc-reclaim-20261109-v1'
H=wallet.HOUR
STEP=spread.STEP


def observed_ratio():
    """Pair simultaneous native hourly closes; every timestamp denotes the actual end of its hour."""
    series={}
    for symbol in ['ETH','BTC']:
        stamps,prices=wallet.quotes(symbol)
        series[symbol]=pd.Series(prices[:,3],index=stamps+H)
    prices=pd.DataFrame(series).dropna()
    return prices.ETH/prices.BTC


def entries(ratio):
    """Use Monday's 24 hourly ratio closes and the 12 signal-hour closes, all finalized at decision."""
    samples=ratio.reindex(np.arange(ratio.index[0],ratio.index[-1]+H,H))
    complete=samples.rolling(12).count().eq(12)
    key_close=samples[(samples.index%STEP==0)&complete]
    ema=spread.trend(key_close)
    marks=[]
    for clock,direction in ema.dropna().items():
        begin=int(clock)-STEP
        week=spread.calendar._ny_week_window(begin)
        if not week['monday_end']<=begin<clock<=week['expires']:
            continue
        monday=samples.loc[week['start']+H:week['monday_end']]
        if len(monday)!=24 or monday.isna().any():
            continue
        lo,hi=monday.min(),monday.max()
        if hi<=lo:
            continue
        lower,upper=lo-.618*(hi-lo),hi+.618*(hi-lo)
        key=samples.loc[begin+H:clock]
        if not lower<key.iat[-1]<upper:
            continue
        side=int(np.sign(direction))
        if (side==1 and key.min()<lower) or (side==-1 and key.max()>upper):
            marks.append(dict(clock=int(clock),side=side,stop=key.min() if side==1 else key.max(),
                              lower=lower,upper=upper,ratio=key.iat[-1]))
    return pd.DataFrame(marks),ema


def paths(marks,ratio,ema):
    """After a confirmed threshold break at hourly close or cloud reversal at 12H close use next common open."""
    prices={}
    for sym in ['ETH','BTC']:
        ts,q=wallet.quotes(sym)
        prices[sym]=pd.DataFrame(q,index=ts,columns=['open','high','low','close'])
    common=prices['ETH'].index.intersection(prices['BTC'].index)
    common=common[(common>=wallet.START)&(common<wallet.END)].sort_values()
    busy=-1
    rows=[]
    for r in marks.itertuples():
        if r.clock<=busy or r.clock not in common:
            continue
        lo=common.get_loc(r.clock)
        hrs=common[lo+1:]
        if not len(hrs):
            continue
        stop=(ratio.reindex(hrs)-r.stop)*r.side<0
        flip=ema.reindex(hrs)*r.side<0
        gaps=np.diff(common[lo:])!=H
        triggered=np.flatnonzero(stop.to_numpy()|flip.to_numpy()|gaps)
        if len(triggered):
            j=triggered[0]
            end=int(hrs[j])
            reason='gap' if gaps[j] else 'ratio_stop' if stop.iloc[j] else 'cloud_flip'
            phase='open'
        else:
            end=int(common[-1])
            reason='sample_end'
            phase='close'
        busy=end
        for slot,symbol in enumerate(['ETH','BTC']):
            frame=prices[symbol]
            sign=r.side if slot==0 else -r.side
            entry=frame.at[r.clock,'open']
            held=frame.loc[r.clock:end-H if phase=='open' else end]
            worst=held.high.max() if sign==-1 else held.low.min()
            final_price=frame.at[end,phase]
            worst=max(worst,final_price) if sign==-1 else min(worst,final_price)
            equity_ratio=1+sign*(worst/entry-1)
            if equity_ratio<=.1*worst/entry+.002:
                raise ValueError(f'Individual 1x leg crosses conservative collateral threshold {symbol} {r.clock}')
            rows.append(dict(event_id=str(r.clock),parent_id=str(r.clock),symbol=symbol,side=sign,slot=slot,
                   entry_ms=r.clock,exit_bar_ms=end,entry_price=entry,exit_price=final_price,exit_phase=phase,
                   exit_reason=reason,exit_fee_rate=wallet.TAKER))
    return pd.DataFrame(rows)


def fund(events,paired,slip):
    roster=events if paired else events[events.symbol.eq('ETH')]
    slots=2 if paired else 1
    data=wallet.prepare(None,slots,assigned=(roster,{}))
    legs,equity=wallet.account(data,slip=slip,exposure=1. if paired else .5,weights=(.5,.5) if paired else (1.,))
    groups=legs.groupby('event_id',sort=False)
    if not groups.size().eq(slots).all():
        raise ValueError('Missing a native leg')
    if paired:
        sizes=legs.assign(base=legs.quantity*legs.entry_price).groupby('event_id').base
        if not np.allclose(sizes.max(),sizes.min(),rtol=1e-9,atol=1.e-6):
            raise ValueError('Unequal paired opening budgets')
    funded=groups.agg(entry_ms=('entry_ms','first'),exit_bar_ms=('exit_bar_ms','first'),cash_pnl=('cash_pnl','sum'),
                      notional=('notional','sum'))
    funded['net_return']=funded.cash_pnl/funded.notional
    label=('pair' if paired else 'eth_half')+('_stress' if slip else '')
    legs.to_csv(EXP/f'legs_{label}.csv.gz',index=False)
    equity.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
    return label,dict(metrics={k:wallet.summarize(funded,equity,*bounds) for k,bounds in spread.periods.PERIODS.items()},
                      gap_marks=data['gap_marks'])


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed relative-price reclaim')
    ratio=observed_ratio()
    marks,ema=entries(ratio)
    events=paths(marks,ratio,ema)
    marks.to_csv(EXP/'confirmed_keys.csv',index=False)
    events.to_csv(EXP/'paths.csv',index=False)
    cases=dict(fund(events,b,s) for b in [True,False] for s in [0.,.0005])
    result=dict(confirmed=len(marks),funded_pairs=events.event_id.nunique(),cases=cases,
                exit_types=events.drop_duplicates('event_id').exit_reason.value_counts().to_dict())
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()

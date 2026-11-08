"""Value 100% or 75% deployment of each half-capital sleeve using concurrent marks.

All historical returns use fixed membership and the native fee-only exits. For
2026 re-use the exact funded five-minute marks of each respective full-size sleeve.
"""
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_payoffs as prices
from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation.monday_breadth_extension_pairs import EXP,PARENT
from yoyo.evaluation.monday_breadth_opt_exit_study import member_bridge


def alt_marks(alt):
    members=alt[['minutes','known','decision','exit_time']].merge(member_bridge(),on=['minutes','known'])
    if members.duplicated(['decision','symbol']).any():
        raise ValueError('Duplicate initial ALT basket member')
    groups=members.groupby('symbol')
    def read(job):
        symbol, events=job
        q,_=prices.load_full_ohlc(symbol,events.decision.min(),events.exit_time.max(),repair_missing=False)
        for r in events.itertuples():
            times=pd.date_range(r.decision,r.exit_time,freq='5min')
            unit=np.r_[1.,q.close.reindex(times[:-1]).to_numpy()/q.open.at[r.decision]]-.0005
            unit[-1]=q.open.at[r.exit_time]/q.open.at[r.decision]*(1-.0005)-.0005
            if not np.isfinite(unit).all():
                raise ValueError(f'Missing 6h member marks: {symbol} at {r.decision}')
            yield r.decision,unit
    counts=members.groupby('decision').size()
    total={t:np.zeros(73) for t in counts.index}
    with ThreadPoolExecutor(3) as pool:
        for i,items in enumerate(pool.map(lambda job:list(read(job)),groups)):
            for t,path in items:total[t]+=path/counts.at[t]
            if (i+1)%100==0:print('historical ALT mark symbols',i+1,'/',groups.ngroups,flush=True)
    for r in alt.itertuples():
        np.testing.assert_allclose(total[r.decision][-1],1+r.net_pct/100,atol=5e-7)
    return total


def btc_marks(book,prices5,partials):
    total={}
    for row in book.itertuples():
        times=pd.date_range(row.decision,row.exit_time,freq='5min')
        units=np.r_[row.entry_open,prices5.close.reindex(times[:-1]).to_numpy()]
        if row.exit_legs==2:
            sale=partials.at[row.decision,'first_exit_price']
            sold=times>row.first_exit_time
            units[sold]=.5*(units[sold]+sale*(1-.0002))
        marked=units/row.entry_open-.0005
        marked[-1]=1+row.net_pct/100
        if not np.isfinite(marked).all():
            raise ValueError(f'Missing BTC marks at {row.decision}')
        total[row.decision]=marked
    return total


def sleeve(events,paths,idx,stake,extra):
    """Each new position buys a fraction of its own sleeve; unused cash earns zero."""
    vals=pd.Series(np.nan,index=idx)
    vals.iloc[0]=wealth=1.
    for row in events.sort_values('decision').itertuples():
        ts=pd.date_range(row.decision,row.exit_time,freq='5min')
        unit=paths[row.decision].copy()
        # Split the extra round-trip stress between entry and terminal exit.
        unit-=extra/200
        unit[-1]-=extra/200
        actual=wealth*((1-stake)+stake*unit)
        vals.loc[ts]=actual
        wealth=actual[-1]
    return vals.ffill()


def stats(curve):
    dd=(1-curve/curve.cummax().clip(lower=1))
    low=dd.idxmax()
    return dict(growth_pct=100*(curve.iloc[-1]-1),max_dd_pct=100*dd.at[low],peak=curve.loc[:low].idxmax(),trough=low)


def funded_relative(events,curve):
    wealth=1.
    marks={}
    for t in events.sort_values('decision').itertuples():
        times=pd.date_range(t.decision,t.exit_time,freq='5min')
        marks[t.decision]=curve.loc[times].to_numpy()/wealth
        wealth*=1+t.funded_pct/100
        np.testing.assert_allclose(curve.at[t.exit_time],wealth,atol=1e-7)
    return marks


def run():
    out=EXP/'risk'
    out.mkdir(exist_ok=True)
    parent=pd.read_parquet(PARENT/'audit/fixed/all_rule_and_account_events.parquet')
    alt=parent[parent.candidate_id.eq('freq36_ALT')]
    partial=pd.read_parquet(EXP/'partial/events.parquet').query('exit_policy=="half_then_6d"')
    btc=pd.read_parquet(EXP/'dual/events.parquet').query('strategy=="medium_time"')
    variants={'simple':btc,'half_then_6d':partial}
    endpoints=pd.concat([g[['decision','exit_time']] for g in [alt,btc,partial]])
    idx=pd.date_range(endpoints.decision.min().floor('D'),endpoints.exit_time.max(),freq='5min')
    quotes,_=prices.load_full_ohlc('BTCUSDT',idx[0],idx[-1],repair_missing=False)
    fills=pd.read_parquet(funding.FUNDED_CACHE,filters=[('policy_id','=',4)]).set_index('entry_time')
    alt_paths=alt_marks(alt)
    np.savez_compressed(out/'alt_6h_marks.npz',timestamps_ns=pd.DatetimeIndex(sorted(alt_paths)).as_unit('ns').asi8,
                        unit_fee_values=np.stack([alt_paths[t] for t in sorted(alt_paths)]))
    curves=pd.read_parquet(EXP/'sleeves/equity_5m.parquet')
    a26=alt[alt.epoch.eq(3)]
    funded_alt=funded_relative(a26,curves.ALT)
    rows=[]
    for name,book in variants.items():
        paths=btc_marks(book,quotes,fills)
        b26=book[book.epoch.eq(3)]
        if name=='simple':
            funded_btc=funded_relative(b26,curves.medium_time_BTC)
        else:
            # All 2026 case legs have observed exact BTC settlement rates and marks.
            from yoyo.evaluation.monday_breadth_extension_partial_marks import accumulated_cost
            hist=funding.rates('BTCUSDT',history=True,local_only=True)
            settlements=hist.realized_rate*hist.mark_price.fillna(quotes.open.reindex(hist.index))
            funded_btc={}
            for r in b26.itertuples():
                times=pd.date_range(r.decision,r.exit_time,freq='5min')
                cost=accumulated_cost(settlements,r.decision,r.exit_time,times)
                if r.exit_legs==2:cost=.5*(cost+accumulated_cost(settlements,r.decision,r.first_exit_time,times))
                np.testing.assert_allclose(cost[-1],r.funding_points,atol=1e-6)
                funded_btc[r.decision]=paths[r.decision]-cost/r.entry_open
        for stake in (1.,.75):
            for slip in (0.,.2):
                for period,aa,bb,ap,bp,ii in [('all_fee',alt,book,alt_paths,paths,idx),
                                               ('2026_funded',a26,b26,funded_alt,funded_btc,curves.index)]:
                    curve=(sleeve(aa,ap,ii,stake,slip)+sleeve(bb,bp,ii,stake,slip))/2
                    vals=stats(curve)
                    rows.append(dict(strategy=name,scope=period,sleeve_deployment=stake,extra_roundtrip_pp=slip,
                                     distinct=pd.concat([aa.decision,bb.decision]).nunique(),**vals))
    pd.DataFrame(rows).to_csv(out/'accounts.csv',index=False)


if __name__=='__main__':
    run()

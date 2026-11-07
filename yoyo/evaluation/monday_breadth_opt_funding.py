"""Realized USD-M funding, with conservative ordering when a fill and settlement share a 5m candle."""
from pathlib import Path
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_exits as sim

ROOT=study.CACHE.parent
SOURCE_END=pd.Timestamp('2026-11-07',tz='UTC')


def rates(symbol,history=False):
    folder='funding_history' if history else 'funding_2026'
    path=ROOT/folder/'normalized/binance'/(symbol+'_funding.csv.gz')
    f=pd.read_csv(path)
    f['clock']=pd.to_datetime(f.funding_time,unit='ms',utc=True).dt.floor('5min')
    if f.clock.duplicated().any():raise ValueError('Overlapping settlements')
    return f.set_index('clock').sort_index()


def charged(fills,history,policy_map):
    """Use actual rates × mark per holding unit. Boundary ambiguity pays the larger cost.

    A positive rate is a cost to longs. At the entry, exit or partial-sale bar,
    use adverse ordering, including whether the opening unit yet held the position.
    Missing historical settlement prices remain missing.
    """
    if history.empty:return np.full(len(fills),np.nan)
    clocks=history.index.as_unit('ns').asi8
    cost=(history.realized_rate*history.mark_price).to_numpy()
    csum=np.r_[0,np.where(np.isfinite(cost),cost,0.).cumsum()]
    miss=np.r_[0,np.cumsum(~np.isfinite(cost))]
    def leg(starts,ends):
        lo=np.searchsorted(clocks,starts,'left');hi=np.searchsorted(clocks,ends,'right')
        vals=csum[hi]-csum[lo]
        for locations,idx in [(starts,lo),(ends,hi-1)]:
            ix=np.clip(idx,0,len(clocks)-1)
            boundary=(clocks[ix]==locations)
            if locations is ends:boundary&=ends!=starts
            vals=vals-np.where(boundary,np.minimum(cost[ix],0.),0.)
        return np.where(miss[hi]==miss[lo],vals,np.nan)
    start=pd.DatetimeIndex(fills.entry_time).as_unit('ns').asi8
    end=pd.DatetimeIndex(fills.exit_time).as_unit('ns').asi8
    first=pd.DatetimeIndex(fills.first_exit_time).as_unit('ns').asi8
    first_weight=np.array([policy_map[int(k)].take_fraction for k in fills.policy_id])*fills.exit_legs.eq(2).to_numpy()
    v=leg(start,end)
    # The size sold first is accounted for as a separate initial leg, with the same first entry.
    v=(1-first_weight)*v+first_weight*leg(start,first)
    if not len(history):return v*np.nan
    return np.where(np.array(fills.exit_time<SOURCE_END),v,np.nan)


def funded_policies():
    out=[]
    for stop in (6,12):
        out.append(sim.Policy('btc_funded_limit',144,stop,take_fraction=1))
        for width in (.5,1,3):out.append(sim.Policy('btc_funded_floor',144,stop,width=width))
        for fraction in (.5,.75):
            for width in (0,4):
                out.append(sim.Policy('btc_funded_partial',144,stop,width=width,take_fraction=fraction))
    return out


def btc_frame(times):
    """Only older exact funding marks are absent. Proxy those settlements with the BTC contract open."""
    f=study.full_frame('BTCUSDT',times)
    hist=rates('BTCUSDT',True).copy()
    hist=hist[(hist.index>=f.index[0])&(hist.index<=f.index[-1])]
    orig=hist.mark_price.isna()
    proxy=f.open.reindex(hist.index)
    hist['mark_price']=hist.mark_price.where(~orig,proxy.to_numpy())
    hist['price_proxy']=orig
    obs=hist.realized_rate*hist.mark_price
    series=obs.reindex(f.index,fill_value=0.)
    f['settled_pre']=series.fillna(0).cumsum().shift(1,fill_value=0.)
    f['settled_bar']=series
    f['scheduled']=((f.index.hour%8==0)&(f.index.minute==0)).astype(float)
    if not hist.index.to_series().map(lambda s:s.hour%8==0 and s.minute==0).all():
        raise ValueError('BTC funding changed its eight-hour UTC session schedule')
    return f,hist


def btc_extra(times):
    times=pd.DatetimeIndex(times).unique().sort_values()
    f,hist=btc_frame(times);pol=funded_policies()
    out=sim.simulate(f,times,pol,batch_size=12)
    out['funding_points']=charged(out,hist,pol)
    out['funded_points']=out.net_points-out.funding_points
    out['funded_pct']=100*out.funded_points/out.entry_open
    out['rule']=[pol[i].key for i in out.policy_id]
    return out


def named_case():
    out=study.OUT
    b=btc_extra([study.CASE_ENTRY])
    b.to_csv(out/'sept16_btc_funding_aware_rules.csv',index=False)
    original=pd.read_parquet(out/'sept16_all_policy_member_fills.parquet')
    pieces=[]
    for symbol,group in original.groupby('asset'):
        g=group.copy()
        f=rates(symbol)
        g['funding_points']=charged(g,f,sim.policies(symbol=='BTCUSDT'))
        g['funded_points']=g.net_points-g.funding_points
        g['funded_pct']=100*g.funded_points/g.entry_open
        pieces.append(g)
    audit=pd.concat(pieces,ignore_index=True)
    audit.to_parquet(out/'sept16_original_funded.parquet',index=False)
    alt=audit[~audit.asset.isin(['BTCUSDT','ETHUSDT','SOLUSDT'])].groupby(['policy_id','rule'])
    summary=alt.agg(n=('funded_pct','count'),mean_net_before_funding=('net_pct','mean'),
                    mean_after_funding=('funded_pct','mean'),mean_peak=('peak_pct','mean'),
                    win_after_funding=('funded_pct',lambda v:100*v.dropna().gt(0).mean())).reset_index()
    summary.to_csv(out/'sept16_alt_funding.csv',index=False)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['btc_case','case','all_btc'])
    a=parser.parse_args()
    if a.stage=='btc_case':
        btc_extra([study.CASE_ENTRY]).to_csv(study.OUT/'sept16_btc_funding_aware_rules.csv',index=False)
    elif a.stage=='case':named_case()
    else:
        events=pd.read_parquet(study.OUT/'all_event_fills/BTC.parquet')
        e=events[(events.hours==144)&(events.policy_id==5)].copy()
        add=btc_extra(list(e.decision))
        add.to_parquet(study.OUT/'btc_funded_rule_fills.parquet',index=False)
        result=e[['candidate_id','known','decision','epoch']].merge(add,left_on='decision',right_on='entry_time')
        result.to_parquet(study.OUT/'btc_funded_rule_events.parquet',index=False)

"""Realized USD-M funding, with conservative ordering when a fill and settlement share a 5m candle."""
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_exits as sim

ROOT=study.CACHE.parent
SOURCE_END=pd.Timestamp('2026-11-07',tz='UTC')
FUNDED_CACHE=ROOT/'btc_funded_exit_fills.parquet'


@lru_cache(maxsize=256)
def archive_month(symbol,month):
    """Observed settlement rates and official hourly mark-candle opens at the funding timestamps."""
    from yoyo.data.binance_um_vision_fetch import BASE,fetch
    base=ROOT/'funding_archives'
    name=f'{symbol}-fundingRate-{month}.zip'
    shared=study.search.ROOT/'experiments/active/exp-monday-1618-flow-20261023-v1/inputs/funding'/symbol/name
    if shared.is_file():
        rate=shared
    else:
        fetch(base,'funding',symbol,f'{BASE}/monthly/fundingRate/{symbol}/{name}')
        rate=base/'funding'/symbol/name
    markname=f'{symbol}-1h-{month}.zip'
    fetch(base,'mark_1h',symbol,f'{BASE}/monthly/markPriceKlines/{symbol}/1h/{markname}')
    mark=base/'mark_1h'/symbol/markname
    if not rate.is_file():return pd.DataFrame()
    rr=pd.read_csv(rate)
    def archive_opens(path):
        quotes=pd.read_csv(path,header=None,usecols=[0,1],names=['t','mark_price'])
        quotes=quotes[pd.to_numeric(quotes.t,errors='coerce').notna()].copy()
        quotes['t']=pd.to_datetime(quotes.t.astype('int64'),unit='ms',utc=True)
        return pd.to_numeric(quotes.set_index('t').mark_price)
    rr['clock']=pd.to_datetime(rr.calc_time,unit='ms',utc=True).dt.floor('5min')
    rr=rr.set_index('clock').rename(columns={'last_funding_rate':'realized_rate'})
    rr['mark_price']=archive_opens(mark).reindex(rr.index) if mark.is_file() else np.nan
    # Some monthly mark archives omit a complete UTC day present in the daily archive.
    for day in rr.index[rr.mark_price.isna()].normalize().unique():
        filename=f'{symbol}-1h-{day:%Y-%m-%d}.zip'
        fetch(base,'daily_mark_1h',symbol,f'{BASE}/daily/markPriceKlines/{symbol}/1h/{filename}')
        path=base/'daily_mark_1h'/symbol/filename
        if path.is_file():
            rr['mark_price']=rr.mark_price.fillna(archive_opens(path))
    rr['price_proxy']=True
    return rr


def rates(symbol,history=False,months=()):
    folder='funding_history' if history else 'funding_2026'
    path=ROOT/folder/'normalized/binance'/(symbol+'_funding.csv.gz')
    if not path.is_file():
        parts=[]
        for month in months:
            repaired=ROOT/'funding_months'/month/'normalized/binance'/(symbol+'_funding.csv.gz')
            parts.append(exact_rates(repaired) if repaired.is_file() else archive_month(symbol,month))
        if len(parts)==0 or any(p.empty for p in parts):return pd.DataFrame()
        return pd.concat(parts).sort_index()
    return exact_rates(path)


def exact_rates(path):
    f=pd.read_csv(path)
    f['price_proxy']=False
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


def existing_or_simulate(times):
    times=pd.DatetimeIndex(times).unique().sort_values()
    previous=pd.read_parquet(FUNDED_CACHE) if FUNDED_CACHE.is_file() else pd.DataFrame()
    if not previous.empty:
        coverage=previous.groupby('entry_time').policy_id.nunique()
        times=times.difference(coverage[coverage==len(funded_policies())].index)
    if len(times):
        part=btc_extra(times)
        previous=pd.concat([previous,part],ignore_index=True).drop_duplicates(['policy_id','entry_time'],keep='last')
        tmp=FUNDED_CACHE.with_suffix('.tmp')
        previous.to_parquet(tmp,index=False);tmp.replace(FUNDED_CACHE)
    return previous


def named_case():
    out=study.OUT
    b=btc_extra([study.CASE_ENTRY])
    b.to_csv(out/'sept16_btc_funding_aware_rules.csv',index=False)
    original=pd.read_parquet(out/'sept16_all_policy_member_fills.parquet')
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda s:rates(s,months=('2026-09',)),original.asset.unique()))
    pieces=[]
    for symbol,group in original.groupby('asset'):
        g=group.copy()
        f=rates(symbol,months=('2026-09',))
        g['funding_mark_proxy']=bool(not f.empty and f.price_proxy.any())
        g['funding_points']=charged(g,f,sim.policies(symbol=='BTCUSDT'))
        g['funded_points']=g.net_points-g.funding_points
        g['funded_pct']=100*g.funded_points/g.entry_open
        pieces.append(g)
    audit=pd.concat(pieces,ignore_index=True)
    audit.to_parquet(out/'sept16_original_funded.parquet',index=False)
    alt=audit[~audit.asset.isin(['BTCUSDT','ETHUSDT','SOLUSDT'])].groupby(['policy_id','rule'])
    summary=alt.agg(n=('funded_pct','count'),mean_net_before_funding=('net_pct','mean'),
                    mean_after_funding=('funded_pct','mean'),mean_peak=('peak_pct','mean'),
                    estimated_marks=('funding_mark_proxy','sum'),win_after_funding=('funded_pct',lambda v:100*v.dropna().gt(0).mean())).reset_index()
    summary.to_csv(out/'sept16_alt_funding.csv',index=False)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['btc_case','case','anchor_btc','all_btc'])
    a=parser.parse_args()
    if a.stage=='btc_case':
        btc_extra([study.CASE_ENTRY]).to_csv(study.OUT/'sept16_btc_funding_aware_rules.csv',index=False)
    elif a.stage=='case':named_case()
    elif a.stage=='anchor_btc':
        existing=pd.read_parquet(study.CACHE/'BTCUSDT.parquet',columns=['entry_time'])
        existing_or_simulate(existing.entry_time.unique())
    else:
        events=pd.read_parquet(study.OUT/'all_event_fills/BTC.parquet')
        e=events[(events.hours==144)&(events.policy_id==5)].copy()
        add=existing_or_simulate(list(e.decision))
        add.to_parquet(study.OUT/'btc_funded_rule_fills.parquet',index=False)
        result=e[['candidate_id','known','decision','epoch']].merge(add,left_on='decision',right_on='entry_time')
        result.to_parquet(study.OUT/'btc_funded_rule_events.parquet',index=False)

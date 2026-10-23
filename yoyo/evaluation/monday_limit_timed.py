"""Owner-authorized first1.618 limit entry with5/15/60/240minute exits.

Monday levels use only the completed NY Monday. Volatility matching uses the
previous14 completed5m true ranges divided by the current observed open.
Pending orders do not use the eventual candle body or later confirmation.
Only the framework searches price crossings and accounts for fills. Intrabar
clock uncertainty is retained as an interval, with grid-reference and bounded
true-duration returns; no interpolated tick trajectory is manufactured.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import time
import warnings
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_5m_wick_engulf as old
from yoyo.evaluation import monday_5m_wick_gold as quality
from yoyo.evaluation import monday_5m_wick_gold_clock as clock
from yoyo.evaluation.monday_15m_volume_rejection import load,week_keys
from yoyo.evaluation.monday_timed_engine import first_limits,account
from yoyo.evaluation.range_framework import ROOT,dump,file_stamp

EXP=ROOT/'experiments/active/exp-monday-limit-timed-vectorbt-20261024-v1'
CODE=['yoyo/evaluation/monday_limit_timed.py','yoyo/evaluation/monday_timed_engine.py',
      'tests/evaluation/test_monday_limit_timed.py','yoyo/evaluation/monday_5m_wick_engulf.py',
      'yoyo/evaluation/monday_15m_volume_rejection.py','yoyo/evaluation/monday_5m_wick_gold.py',
      'yoyo/evaluation/monday_5m_wick_gold_clock.py','yoyo/evaluation/range_framework.py']
OHLC=old.OHLC


def market_data(symbol,paths,cfg):
    q={};fine=None
    if symbol=='XAUUSD':
        raw,_=clock.read_minutes(paths);coarse,q=quality.clean_minutes(raw)
        bad=pd.DatetimeIndex(pd.to_datetime(q['excluded_bar_times'],utc=True))
        fine=raw[~raw.index.floor('5min').isin(bad)].sort_index()
        fine=fine[~fine.index.duplicated()].copy()
    else:
        coarse=load(paths);coarse['quality_segment']=0
        finepath=cfg['fine_series'].get(symbol)
        if finepath:
            fine=pd.read_csv(ROOT/finepath,usecols=['ts',*OHLC],float_precision='round_trip')
            fine.index=pd.DatetimeIndex(pd.to_datetime(fine.pop('ts'),unit='ms',utc=True)).as_unit('ns')
            dup=fine[fine.index.duplicated(False)]
            if len(dup) and dup.groupby(level=0).nunique().gt(1).any().any():raise ValueError('Conflicting fine source')
            fine=fine[~fine.index.duplicated()].sort_index()
    for f in [coarse,fine]:
        if f is None:continue
        f.index=f.index.as_unit('ns')
        if not np.isfinite(f[OHLC]).all().all() or (f[OHLC]<=0).any().any():raise ValueError('Invalid prices')
        if (f.high<f[OHLC].max(axis=1)).any() or (f.low>f[OHLC].min(axis=1)).any():raise ValueError('Invalid range')
    return coarse,fine,q


def pending_windows(f,symbol,q):
    local,week=week_keys(f.index)
    mon=f[local.dayofweek==0].assign(week=week[local.dayofweek==0])
    anchors=mon.groupby('week').agg(high=('high','max'),low=('low','min'),n=('close','size'))
    anchors=anchors[anchors.n.ge(240 if symbol=='XAUUSD' else 288)]
    blocked=quality.invalid_monday_weeks(q) if q else set()
    bad=pd.DatetimeIndex(pd.to_datetime(q.get('excluded_bar_times',[]),utc=True))
    groups=pd.Series(np.arange(len(f)),index=f.index).groupby(week)
    rows=[];detail=dict(valid_mondays=0,pending_orders=0,missing_tuesday_opens=0)
    for w,g in groups:
        if w not in anchors.index or w in blocked:continue
        a=anchors.loc[w]
        if not a.high>a.low:continue
        detail['valid_mondays']+=1
        ix=g.to_numpy();ix=ix[local[ix].dayofweek>=1]
        if not len(ix):continue
        start=int(ix[0]);end=int(ix[-1])+1
        tuesday=(pd.Timestamp(w)+pd.Timedelta(days=1)).tz_localize(old.TZ).tz_convert('UTC')
        if symbol!='XAUUSD':
            if f.index[start]!=tuesday:
                detail['missing_tuesday_opens']+=1;continue
            gap=np.flatnonzero(np.diff(f.index[start:end].as_unit('ns').asi8)!=old.STEP.value)
            if len(gap):end=start+int(gap[0])+1
        elif len(bad):
            upcoming=bad[(bad>=tuesday)&(bad<f.index[end-1]+old.STEP)]
            if len(upcoming):end=min(end,int(f.index.searchsorted(upcoming[0])))
        if end<=start:continue
        for side in [1,-1]:
            level=a.low-.618*(a.high-a.low) if side==1 else a.high+.618*(a.high-a.low)
            if level<=0:continue
            rows.append(dict(week=w,start=start,end=end,side=side,level=float(level),monday_high=float(a.high),monday_low=float(a.low),placed_time=str(tuesday)))
    detail['pending_orders']=len(rows)
    return pd.DataFrame(rows),detail


def sample_entries(f,symbol,q,cfg):
    pending,detail=pending_windows(f,symbol,q)
    if pending.empty:return pd.DataFrame(),detail
    prices=f[OHLC].to_numpy(float)
    hit=first_limits(prices,pending.start.to_numpy(int),pending.end.to_numpy(int),pending.level.to_numpy(),pending.side.to_numpy())
    found=np.isfinite(hit[:,0]);e=pending[found].copy().reset_index(drop=True)
    e['entry_i']=hit[found,0].astype(int);e['entry']=hit[found,1]
    e['parent_id']=[f'{symbol}-{w}-{s}' for w,s in zip(e.week,e.side)];e['pair_id']=e.parent_id
    e['population']='signal';e['symbol']=symbol
    tr=pd.concat([f.high-f.low,(f.high-f.close.shift()).abs(),(f.low-f.close.shift()).abs()],axis=1).max(axis=1)
    atr=tr.rolling(14,min_periods=14).mean().shift()/f.open
    bucket=np.floor(np.log2(atr.where(atr>0))*4).fillna(-999).to_numpy(int)
    e['bucket']=bucket[e.entry_i.to_numpy()]
    detail.update(touched=len(e),unfilled=int((~found).sum()),unmatched=0)
    local,week=week_keys(f.index);pools={}
    eligible=(local.dayofweek>=1)&(bucket!=-999)&f['count'].eq(5).to_numpy()
    for i in np.flatnonzero(eligible):pools.setdefault((week[i],int(bucket[i])),[]).append(i)
    rng=np.random.default_rng(cfg['seed']);controls=[]
    for r in e.itertuples():
        pool=np.asarray(pools.get((r.week,r.bucket),[]),int);pool=pool[pool!=r.entry_i]
        if r.bucket==-999 or len(pool)<cfg['controls_per_signal']:
            detail['unmatched']+=1;continue
        for k,i in enumerate(rng.choice(pool,cfg['controls_per_signal'],replace=False)):
            controls.append(dict(parent_id=f'{r.parent_id}-c{k}',pair_id=r.parent_id,population='random',symbol=symbol,
                week=r.week,side=r.side,entry_i=int(i),entry=float(f.open.iloc[i]),level=float(f.open.iloc[i]),
                bucket=r.bucket,monday_high=r.monday_high,monday_low=r.monday_low,placed_time=str(f.index[i])))
    return pd.concat([e,pd.DataFrame(controls)],ignore_index=True),detail


def fine_matches(f,fine,bar):
    """Require complete1m OHLC agreement with the registered5m source."""
    t=f.index[bar];j=int(fine.index.searchsorted(t));end=j+5
    if end>len(fine) or fine.index[j]!=t or fine.index[end-1]!=t+pd.Timedelta(minutes=4):return False
    if not np.all(np.diff(fine.index[j:end].as_unit('ns').asi8)==60_000_000_000):return False
    x=fine.iloc[j:end]
    return bool(np.allclose([x.open.iloc[0],x.high.max(),x.low.min(),x.close.iloc[-1]],f.iloc[bar][OHLC].to_numpy(float),rtol=1e-10,atol=1e-12))


def event_prices(f,fine,e,cfg):
    """Prepare observed prices/bounds; only account() computes trade outcomes."""
    events=[];coverage=[];horizons=cfg['holding_minutes']
    fp=fine[OHLC].to_numpy(float) if fine is not None else None
    for r in e.to_dict('records'):
        i=int(r['entry_i']);t=f.index[i];resolution=5;entry=r['entry'];source=f;refine='unavailable'
        if f['count'].iloc[i]!=5:
            coverage.append(dict(r,entry_time=str(t),eligible=False,reason='incomplete_entry_interval'));continue
        if r['population']=='signal' and fine is not None and fine_matches(f,fine,i):
            j=int(fine.index.searchsorted(t))
            hit=first_limits(fp[j:j+5],np.asarray([0]),np.asarray([5]),np.asarray([r['level']]),np.asarray([r['side']]))[0]
            if np.isfinite(hit[0]):
                jj=j+int(hit[0]);tt=fine.index[jj]
                # Refinement changes precision only if all requested future
                # quote windows agree with their registered coarse source.
                checks=[]
                for h in horizons:
                    ft=tt+pd.Timedelta(minutes=h);co=int(f.index.get_indexer([ft.floor('5min')])[0])
                    checks.append(co>=0 and fine.index.get_indexer([ft])[0]>=0 and fine_matches(f,fine,co))
                if all(checks):source=fine;t=tt;resolution=1;entry=float(hit[1]);refine='verified_1m'
                else:refine='missing_or_disagreeing_expiry_window'
            else:refine='touch_disagrees'
        elif r['population']=='signal' and fine is not None:refine='missing_or_disagreeing_entry_window'
        loc=int(source.index.get_indexer([t])[0]);at_open=bool(r['population']=='random' or r['side']*(r['level']-source.open.iloc[loc])>=0)
        if r['population']=='random':at_open=True
        valid=[];price_rows=[]
        for horizon in horizons:
            target=t+pd.Timedelta(minutes=horizon);j=int(source.index.get_indexer([target])[0])
            ok=j>=0 and (source is fine or source['count'].iloc[j]==5)
            valid.append(ok)
            if not ok:continue
            quote=source.iloc[j]
            grid=float(quote.open)
            worst=grid if at_open else float(quote.low if r['side']==1 else quote.high)
            best=grid if at_open else float(quote.high if r['side']==1 else quote.low)
            for scenario,exit_price in [('grid',grid),('lower',worst),('upper',best)]:
                price_rows.append(dict(r,entry=entry,horizon=horizon,scenario=scenario,exit_price=exit_price,
                    entry_time=str(t),entry_interval_end=str(t+pd.Timedelta(minutes=resolution)),entry_resolution_minutes=resolution,
                    opening_fill=at_open,refinement=refine,exit_time=str(target),exit_interval_end=str(target+pd.Timedelta(minutes=resolution)),
                    actual_duration_exact=at_open,grid_hold_min_minutes=horizon if at_open else horizon-resolution,
                    grid_hold_max_minutes=horizon))
        common=all(valid)
        for row in price_rows:row['common_horizons']=common
        events.extend(price_rows)
        coverage.append(dict(r,entry=entry,entry_time=str(t),entry_resolution_minutes=resolution,opening_fill=at_open,
            refinement=refine,eligible=True,common_horizons=common,available_horizons=[h for h,v in zip(horizons,valid) if v]))
    return pd.DataFrame(events),pd.DataFrame(coverage)


def process(job,cfg):
    warnings.filterwarnings('ignore',category=pd.errors.Pandas4Warning)
    symbol,paths=job;dest=EXP/'symbols'/symbol;dest.mkdir(parents=True,exist_ok=True)
    inputs=[*paths]
    if symbol in cfg['fine_series']:inputs.append(str(ROOT/cfg['fine_series'][symbol]))
    inputs.append(str(ROOT/cfg['crypto_root']/'exchange_info.json'))
    identity=dict(config=cfg,inputs=[file_stamp(p) for p in inputs],code=[file_stamp(ROOT/p) for p in CODE],
        versions={k:version(k) for k in ['vectorbt','pandas','numpy','numba']})
    if (dest/'done.json').exists():
        assert json.loads((dest/'identity.json').read_text())==identity,'Changed completed study';return json.loads((dest/'done.json').read_text())
    begin=time.monotonic();f,fine,q=market_data(symbol,paths,cfg)
    entries,detail=sample_entries(f,symbol,q,cfg)
    events,coverage=event_prices(f,fine,entries,cfg) if len(entries) else (pd.DataFrame(),pd.DataFrame())
    rows,fills=account(events,symbol,cfg)
    if len(rows):
        times=pd.to_datetime(rows.entry_time,utc=True);exits=pd.to_datetime(rows.exit_interval_end,utc=True)
        rows['segment']=(times>=pd.Timestamp(cfg['split'])).astype(int)
        rows['crosses_selection_cut']=(times<pd.Timestamp(cfg['split']))&(exits>pd.Timestamp(cfg['split']))
        rows.to_csv(dest/'trades.csv.gz',index=False);fills.to_csv(dest/'fills.csv.gz',index=False)
    if len(coverage):coverage.to_csv(dest/'coverage.csv.gz',index=False)
    if q:dump(dest/'quality.json',q)
    sig=coverage[coverage.population.eq('signal')] if len(coverage) else pd.DataFrame()
    done=dict(symbol=symbol,source_bars=len(f),fine_bars=len(fine) if fine is not None else 0,
        source_first=str(f.index[0]),source_last=str(f.index[-1]),parents=len(entries),
        signals=len(sig),common_signals=int(sig.common_horizons.fillna(False).sum()) if len(sig) and 'common_horizons' in sig else 0,
        native_positions=len(fills)//2,native_fills=len(fills),seconds=time.monotonic()-begin,**detail)
    dump(dest/'identity.json',identity);dump(dest/'done.json',done)
    return done


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--workers',type=int,default=3);ap.add_argument('--symbols',nargs='*');args=ap.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:raise RuntimeError('Commit builder first')
    cfg=json.loads((EXP/'config.json').read_text());jobs,_=old.jobs(cfg)
    if args.symbols:jobs=[j for j in jobs if j[0] in args.symbols]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(process,j,cfg) for j in jobs]):print(json.dumps(future.result()),flush=True)


if __name__=='__main__':main()

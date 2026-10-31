"""Census and price-path analysis of owner-requested 12h 1.618 reclaims.

Reuse registered frozen OKX15m snapshots. Clock matches Pine V2.42 screenshot:
NY calendar of 12h bar OPENs determines Monday anchors, not intraday NYMonday.
Signal uses only complete Monday12h OHLC and current closed OHLC. Features
read prior14 completed12h true ranges/priorclose,signal wick/body and earlier
sameweek touches only. Future1/2/6/14bars are outcomes,never admission filters.
No fills, stops, targets, fee assumptions, new models or production changes.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.btc_1618_early_marks import ROOT,EXP as BTC
from yoyo.evaluation.top20_1618_rank import EXP as ALTS

EXP=ROOT/'experiments/active/exp-top20-12h-1618-reclaims-20261101-v1'
STEP=pd.Timedelta(hours=12);OHLC=['open','high','low','close'];HORIZONS=[1,2,6,14]


def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False,default=str)+'\n')


def aggregate12(raw):
    """Only exact48x15m grids become12h candles;never bridge/fill missing data."""
    assert raw.index.is_unique and raw.index.is_monotonic_increasing
    assert (raw.index.asi8%pd.Timedelta(minutes=15).value==0).all()
    assert np.isfinite(raw[OHLC].to_numpy()).all()
    assert raw.low.le(raw[OHLC].min(axis=1)).all() and raw.high.ge(raw[OHLC].max(axis=1)).all()
    g=raw.resample('12h',origin='epoch',closed='left',label='left')
    b=g.agg(dict(open='first',high='max',low='min',close='last'))
    b['source_count']=g.close.count();b['complete']=b.source_count.eq(48)
    b.loc[~b.complete,OHLC]=np.nan
    return b


def classify(o,h,l,c,level,side):
    """Strict penetration and strict close-inside;subtype does not alter admission."""
    penetration=l<level if side==1 else h>level
    if not penetration or side*(c-level)<=0:return None
    if side*(o-level)>0:return 'pure_wick'
    if side*(o-level)<0:return 'body_reclaim'
    return 'body_on_level'


def outcome(b,i,side,level,edge,horizon,scale):
    """Future price descriptors only;missing timestamps or bars leave NA outcomes."""
    f=b.iloc[i+1:i+1+horizon]
    if len(f)!=horizon or not f.complete.all():return {}
    c=float(b.close.iloc[i]);pct=side*(float(f.close.iloc[-1])/c-1)*100
    favorable=(f.high.max()-c) if side==1 else (c-f.low.min())
    adverse=(c-f.low.min()) if side==1 else (f.high.max()-c)
    extreme=b.low.iloc[i] if side==1 else b.high.iloc[i]
    return {f'return_pct_{horizon}':pct,f'positive_{horizon}':float(pct>0),
        f'favorable_pct_{horizon}':max(0.,float(favorable/c*100)),f'adverse_pct_{horizon}':max(0.,float(adverse/c*100)),
        f'level_lost_{horizon}':float(((f.close-level)*side<0).any()),
        f'wick_broken_{horizon}':float((f.low<extreme).any() if side==1 else (f.high>extreme).any()),
        f'near_edge_reached_{horizon}':float((f.high>=edge).any() if side==1 else (f.low<=edge).any()),
        f'return_tr_{horizon}':side*(float(f.close.iloc[-1])-c)/scale if np.isfinite(scale) and scale>0 else np.nan}


def census(b,symbol):
    """Enumerate complete post-Monday closes;earlier missing bars only flag order."""
    local=b.index.tz_convert('America/New_York');keys=local.tz_localize(None).normalize()-pd.to_timedelta(local.dayofweek,unit='D')
    tr=pd.concat([b.high-b.low,(b.high-b.close.shift()).abs(),(b.low-b.close.shift()).abs()],axis=1).max(axis=1)
    tr=tr.where(b.complete&b.close.shift().notna());scale=tr.rolling(14,min_periods=14).mean().shift()
    vol=scale/b.close.shift();buckets=np.floor(4*np.log2(vol.where(vol>0)))
    groups=pd.Series(np.arange(len(b)),index=keys).groupby(level=0);rows=[];weeks=[]
    for week,g in groups:
        pos=g.to_numpy();mon=pos[local[pos].dayofweek==0];label=str(week.date())
        if len(mon)!=2 or not b.complete.iloc[mon].all():
            weeks.append(dict(symbol=symbol,week=label,status='incomplete_monday'));continue
        hi=float(b.high.iloc[mon].max());lo=float(b.low.iloc[mon].min());width=hi-lo
        if width<=0:weeks.append(dict(symbol=symbol,week=label,status='flat_monday'));continue
        ready=b.index[mon[-1]]+STEP;expiry=(week+pd.Timedelta(days=7)).tz_localize('America/New_York')
        levels={1:hi-1.618*width,-1:lo+1.618*width}
        weeks.append(dict(symbol=symbol,week=label,status='valid',monday_high=hi,monday_low=lo,upper=levels[-1],lower=levels[1],ready=str(ready),expiry=str(expiry),first_monday=str(b.index[mon[0]])))
        rest=pos[(b.index[pos]>=ready)&(b.index[pos]<expiry)]
        for side in [1,-1]:
            level=levels[side];touches=0;reclaims=0;prefix_complete=True
            for i in rest:
                if not b.complete.iloc[i]:prefix_complete=False;continue
                bar=b.iloc[i];o,h,l,c=(float(bar[x]) for x in OHLC)
                extreme=l if side==1 else h;penetrates=side*(level-extreme)>0
                kind=classify(o,h,l,c,level,side)
                ident=f'{symbol}|{b.index[i].isoformat()}|{side:+d}';span=h-l
                wick=(min(o,c)-l) if side==1 else (h-max(o,c))
                row=dict(event_id=ident,symbol=symbol,week=label,side=side,bar_i=int(i),signal_open=str(b.index[i].tz_convert('Asia/Shanghai')),signal_close=str((b.index[i]+STEP).tz_convert('Asia/Shanghai')),
                    monday_high=hi,monday_low=lo,level=level,ready=str(ready),expiry=str(expiry),first_monday=str(b.index[mon[0]]),kind=kind or 'not_reclaim',is_event=kind is not None,inside=bool(side*(c-level)>0),
                    equality_touch=bool(extreme==level),close_on_level=bool(c==level),prior_touch_count=touches if prefix_complete else np.nan,prior_reclaim_count=reclaims if prefix_complete else np.nan,
                    first_weekly_reclaim=bool(reclaims==0) if prefix_complete else None,week_prefix_complete=prefix_complete,
                    tr_prior14=float(scale.iloc[i]),vol_bucket=float(buckets.iloc[i]),wick_fraction=wick/span if span else 0.,
                    penetration_width=side*(level-extreme)/width,reclaim_width=side*(c-level)/width,signal_range_tr=span/scale.iloc[i] if scale.iloc[i]>0 else np.nan,
                    approach4_pct=-side*(c/b.close.iloc[i-4]-1)*100 if i>=4 and b.complete.iloc[i-4:i+1].all() else np.nan,
                    **{x:float(bar[x]) for x in OHLC})
                for horizon in HORIZONS:row.update(outcome(b,i,side,level,lo if side==1 else hi,horizon,scale.iloc[i]))
                rows.append(row)
                if penetrates:touches+=1
                if kind:reclaims+=1
    return pd.DataFrame(rows),pd.DataFrame(weeks)


def describe(g):
    out=dict(events=len(g),candles=int(g[['symbol','signal_open']].drop_duplicates().shape[0]),coin_week_sides=int(g[['symbol','week','side']].drop_duplicates().shape[0]))
    for h in HORIZONS:
        observed=g[g[f'return_pct_{h}'].notna()]
        out[f'n_{h}']=len(observed)
        for field,method in [('return_pct','mean'),('return_pct','median'),('positive','mean'),('favorable_pct','median'),('adverse_pct','median'),('level_lost','mean'),('wick_broken','mean'),('near_edge_reached','mean')]:
            out[f'{field}_{h}_{method}']=float(getattr(observed[f'{field}_{h}'],method)()) if len(observed) else None
    return out


def matched_pairs(obs,events):
    """Controls selected only by sameweek/coin/side/pastvol and observed inside close."""
    pool=obs[obs.inside&obs.vol_bucket.notna()].copy();groups={k:g for k,g in pool.groupby(['symbol','week','side','vol_bucket'])};rows=[];coverage=[]
    for e in events.itertuples():
        group=groups.get((e.symbol,e.week,e.side,e.vol_bucket),pd.DataFrame())
        if len(group):group=group[group.event_id.ne(e.event_id)]
        coverage.append(dict(event_id=e.event_id,available_controls=len(group)))
        for h in HORIZONS:
            value=getattr(e,f'return_pct_{h}');valid=group[group[f'return_pct_{h}'].notna()] if len(group) else group
            if not np.isfinite(value) or not len(valid):continue
            rows.append(dict(event_id=e.event_id,symbol=e.symbol,week=e.week,side=e.side,kind=e.kind,first_weekly_reclaim=e.first_weekly_reclaim,horizon=h,controls=len(valid),signal_pct=value,random_pct=float(valid[f'return_pct_{h}'].mean()),signal_positive=float(value>0),random_positive=float(valid[f'positive_{h}'].mean()),control_ids=';'.join(valid.event_id)))
    return pd.DataFrame(rows),pd.DataFrame(coverage)


def compare(p,seed=20261101):
    if p.empty:return dict(pairs=0)
    a=p.assign(delta=p.signal_pct-p.random_pct,positive_delta=p.signal_positive-p.random_positive)
    sums=a.groupby('week')[['delta','positive_delta']].sum();counts=a.groupby('week').size().reindex(sums.index).to_numpy();values=sums.to_numpy()
    rng=np.random.default_rng(seed);draw=rng.integers(0,len(sums),size=(2000,len(sums)));boot=values[draw].sum(axis=1)/counts[draw].sum(axis=1)[:,None]
    flips=rng.choice([-1,1],size=(2000,len(sums)));null=(flips*values[:,0]).sum(axis=1)/len(a);mean=float(a.delta.mean())
    return dict(pairs=len(a),weeks=len(sums),signal_mean_pct=float(a.signal_pct.mean()),random_mean_pct=float(a.random_pct.mean()),excess_mean_pct=mean,excess_mean_pct_95ci=np.quantile(boot[:,0],[.025,.975]).tolist(),signal_positive_rate=float(a.signal_positive.mean()),random_positive_rate=float(a.random_positive.mean()),excess_positive_rate=float(a.positive_delta.mean()),excess_positive_95ci=np.quantile(boot[:,1],[.025,.975]).tolist(),paired_week_signflip_p=float((1+(np.abs(null)>=abs(mean)-1e-12).sum())/2001))


def analyze(obs,events):
    pairs,coverage=matched_pairs(obs,events);pairs.to_csv(EXP/'matched_pairs.csv',index=False);coverage.to_csv(EXP/'control_coverage.csv',index=False)
    groups=[('all_reclaims',events),('pure_wick',events[events.kind.eq('pure_wick')]),('body_reclaim',events[events.kind.eq('body_reclaim')]),('pure_first_weekly',events[events.kind.eq('pure_wick')&events.first_weekly_reclaim.eq(True)])]
    groups += [(symbol,events[events.symbol.eq(symbol)&events.kind.eq('pure_wick')]) for symbol in ['BTC','ETH']]
    groups += [(side,events[events.side.eq(d)&events.kind.eq('pure_wick')]) for side,d in [('lower',1),('upper',-1)]]
    desc={};matched={}
    for name,g in groups:
        desc[name]=describe(g);matched[name]={str(h):compare(pairs[pairs.event_id.isin(g.event_id)&pairs.horizon.eq(h)]) for h in HORIZONS}
    breakdown=[]
    pure=events[events.kind.eq('pure_wick')]
    for dimension,series in [('symbol',pure.symbol),('year',pd.to_datetime(pure.signal_open,utc=True).dt.year),('wick_fraction',pd.cut(pure.wick_fraction,[-1,.25,.5,.75,1.00001],labels=['<=25%','25-50%','50-75%','>75%']))]:
        for key,g in pure.groupby(series,observed=True):breakdown.append(dict(dimension=dimension,group=str(key),**describe(g),matched_72h=compare(pairs[pairs.event_id.isin(g.event_id)&pairs.horizon.eq(6)])))
    dump(EXP/'breakdown.json',breakdown)
    return dict(descriptive=desc,matched=matched,controls_missing=int(coverage.available_controls.eq(0).sum()),controls_available=len(coverage)-int(coverage.available_controls.eq(0).sum()),primary='pure_wick72h',interpretation='Price paths after confirmed close,not trade winrate or PnL;all20coin historical selection is retrospective;other horizons/subgroups exploratory.')


def run():
    assert not (EXP/'summary.json').exists(), 'Completed census exists; reuse frozen outputs.'
    cfg=json.loads((EXP/'config.json').read_text());ranking=json.loads((ALTS/'ranking.json').read_text());all_rows=[];all_weeks=[];receipts=[];calibration={}
    for spec in ranking['top20']:
        symbol=spec['symbol'];source=(BTC if symbol=='BTC' else ALTS/symbol)/'bars.csv.gz';assert source.exists()
        stat=source.stat();raw=pd.read_csv(source,index_col=0,parse_dates=True,float_precision='round_trip');b=aggregate12(raw)
        folder=EXP/symbol;folder.mkdir(parents=True,exist_ok=True);b.to_csv(folder/'bars12.csv.gz',index_label='open_time')
        obs,weeks=census(b,symbol)
        if len(obs):
            obs['rank']=int(spec['rank']);obs['before_current_listTime']=pd.to_datetime(obs.signal_open,utc=True)<pd.to_datetime(int(spec['list_time']),unit='ms',utc=True)
            all_rows.append(obs)
        all_weeks.append(weeks)
        assert (source.stat().st_size,source.stat().st_mtime_ns)==(stat.st_size,stat.st_mtime_ns)
        if symbol=='ETH':
            for week,expected in cfg['screenshot_lower_levels'].items():
                actual=float(weeks.loc[weeks.week.eq(week),'lower'].iloc[0]);assert round(actual,2)==expected;calibration[week]=dict(expected=expected,actual=actual)
            for stamp in cfg['screenshot_arrow_signals_utc']:
                t=pd.Timestamp(stamp);hit=obs[(pd.to_datetime(obs.signal_open,utc=True)==t)&obs.side.eq(1)];assert len(hit)==1 and hit.kind.iloc[0]=='pure_wick'
            selected=b.loc[pd.Timestamp('2026-10-07T12:00:00Z'),OHLC].to_numpy(float)
            np.testing.assert_allclose(selected,[2577.30,2583.55,2532.55,2572.82],atol=1e-8,rtol=0)
        receipts.append(dict(symbol=symbol,rank=int(spec['rank']),source=str(source),bytes=stat.st_size,mtime_ns=stat.st_mtime_ns,source15m_rows=len(raw),first_source=str(raw.index[0]),last_source=str(raw.index[-1]),complete12h=int(b.complete.sum()),incomplete12h=int((~b.complete).sum()),first_complete=str(b.index[b.complete][0]),last_complete=str(b.index[b.complete][-1]),valid_weeks=int(weeks.status.eq('valid').sum()),invalid_weeks=int(weeks.status.ne('valid').sum()),reclaims=int(obs.is_event.sum()) if len(obs) else 0))
        print(symbol,'12h',int(b.complete.sum()),'reclaims',receipts[-1]['reclaims'],flush=True)
    obs=pd.concat(all_rows,ignore_index=True);events=obs[obs.is_event].copy();weeks=pd.concat(all_weeks,ignore_index=True)
    assert events.event_id.is_unique
    # Preserve every event even when its future window is incomplete.
    obs.to_csv(EXP/'observations.csv.gz',index=False);events.to_csv(EXP/'events.csv',index=False);weeks.to_csv(EXP/'weeks.csv',index=False)
    result=analyze(obs,events)
    counts=[]
    for receipt in receipts:
        g=events[events.symbol.eq(receipt['symbol'])];counts.append(dict(receipt,lower=int(g.side.eq(1).sum()),upper=int(g.side.eq(-1).sum()),pure_wick=int(g.kind.eq('pure_wick').sum()),body_reclaim=int(g.kind.eq('body_reclaim').sum()),body_on_level=int(g.kind.eq('body_on_level').sum()),old_listing_events=int(g.before_current_listTime.sum())))
    pd.DataFrame(counts).to_csv(EXP/'symbol_counts.csv',index=False)
    result.update(events=len(events),unique_candles=int(events[['symbol','signal_open']].drop_duplicates().shape[0]),symbols=counts,kind_counts=events.kind.value_counts().to_dict(),equality_only_touches=int((obs.equality_touch&obs.inside).sum()),close_on_level=int(obs.close_on_level.sum()),screenshot_calibration=calibration,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),training_eligible=False,production_eligible=False)
    dump(EXP/'input_receipt.json',dict(sources=receipts,universe_source=str(ALTS/'ranking.json'),ranking_window=dict(start=ranking['start'],end=ranking['end']),no_new_download=True,no_content_hashes=True))
    dump(EXP/'summary.json',result)
    print(json.dumps({k:result[k] for k in ['events','unique_candles','kind_counts','controls_missing']},indent=2),flush=True)
    print('PRIMARY',json.dumps(result['matched']['pure_wick']['6']),flush=True)


if __name__=='__main__':run()

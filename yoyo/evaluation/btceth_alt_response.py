"""Five-minute altcoin reactions at BTC/ETH native Monday1.618 touches.

Features use current confirmed5m OHLCV and previous24/14/288 bars as named.
Monday bounds use only288 complete NY-Monday bars after that Monday closes.
Future prices are descriptive outcomes, never event admission or features.
Current top20 is a frozen retrospective population. This is an event study,
not a fill simulation, fee-net return, predictive model or causal impact test.
"""
from pathlib import Path
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.btceth_alt_response_source import ROOT,EXP,RANK,OHLCV,STEP,write

HORIZONS=[1,3,6,12]

def levels(f):
    """Use complete5m NYMonday only; no preceding/future week substitution."""
    local=f.index.tz_convert('America/New_York')
    keys=local.tz_localize(None).normalize()-pd.to_timedelta(local.dayofweek,unit='D')
    out=pd.DataFrame(index=f.index,columns=['upper','lower','week']);out[['upper','lower']]=np.nan
    for key in keys.unique():
        a=key.tz_localize('America/New_York');ready=(key+pd.Timedelta(days=1)).tz_localize('America/New_York');end=(key+pd.Timedelta(days=7)).tz_localize('America/New_York')
        mon=f.reindex(pd.date_range(a,ready,freq=STEP,inclusive='left'))
        if len(mon)!=288 or mon[OHLCV[:4]].isna().any().any():continue
        hi,lo=mon.high.max(),mon.low.min()
        if hi<=lo:continue
        mask=(out.index>=ready)&(out.index<end)
        out.loc[mask,['upper','lower','week']]=[lo+1.618*(hi-lo),hi-1.618*(hi-lo),str(key.date())]
    return out

def load():
    cfg=json.loads((EXP/'config.json').read_text());grid=pd.date_range(cfg['start'],cfg['end_exclusive'],freq=STEP,inclusive='left')
    frames={};bounds={}
    for r in json.loads(RANK.read_text())['top20']:
        s=r['symbol'];f=pd.read_csv(EXP/'frozen'/f'{s}.csv.gz',index_col=0,parse_dates=True,float_precision='round_trip')
        f.index=pd.DatetimeIndex(f.index).as_unit('ns');f=f.reindex(grid);frames[s]=f;bounds[s]=levels(f)
    return frames,bounds

def event_rows(frames,bounds):
    """Fresh crossing, rearmed by a fully-inside preceding5m bar; causal60m cooldown."""
    rows=[]
    for symbol in ['BTC','ETH']:
        f=frames[symbol];b=bounds[symbol]
        for side,key,field in [(1,'lower','low'),(-1,'upper','high')]:
            level=b[key].astype(float);cross=side*(level-f[field])>0
            previous_inside=side*(f[field].shift(1)-level)>=0
            hit=cross&previous_inside
            last=None;seen=set()
            for i in np.flatnonzero(hit.to_numpy()):
                t=f.index[i]
                if last is not None and t-last<pd.Timedelta(hours=1):continue
                last=t;bar=f.iloc[i];lev=float(level.iloc[i]);inside=side*(bar.close-lev)>0;open_inside=side*(bar.open-lev)>0
                week=b.week.iloc[i];first=week not in seen;seen.add(week)
                rows.append(dict(event_id=f'{symbol}|{t.isoformat()}|{side:+d}',leader=symbol,side=side,week=week,time=t,
                    confirm=t+STEP,i=int(i),level=lev,kind=('pure_wick_reclaim' if open_inside else 'body_reclaim') if inside else 'not_reclaimed',
                    reclaimed=bool(inside),open_inside=bool(open_inside),first_weekly=first,day=str(t.date()),**{k:float(bar[k]) for k in OHLCV}))
    return pd.DataFrame(rows).sort_values(['time','leader','side']).reset_index(drop=True)

def all_features(frames,bounds,leader,side):
    """Touch-close features use only t and earlier; forward/lag columns are outcomes."""
    lf=frames[leader];lr=lf.close.pct_change(fill_method=None);alt_names=[s for s in frames if s not in ['BTC','ETH']]
    data={};index=lf.index;metrics={};paths={}
    for symbol in [leader]+alt_names:
        f=frames[symbol];r=f.close.pct_change(fill_method=None);prev=f.close.shift()
        tr=pd.concat([f.high-f.low,(f.high-prev).abs(),(f.low-prev).abs()],axis=1).max(axis=1).where(f.close.notna()&prev.notna())
        span=f.high-f.low
        one=pd.DataFrame(index=index)
        one['pre30']=side*(f.close.shift()/f.close.shift(7)-1)*100
        one['touch_return']=side*(f.close/prev-1)*100
        one['shock']=(-side*((f.low if side==1 else f.high)/prev-1)*100).clip(lower=0)
        one['volume_ratio']=f.volume/f.volume.rolling(24,min_periods=24).median().shift().replace(0,np.nan)
        one['range_ratio']=tr/tr.rolling(14,min_periods=14).mean().shift().replace(0,np.nan)
        one['recovery']=((f.close-f.low) if side==1 else (f.high-f.close))/span.replace(0,np.nan)
        one['new_extreme']=((f.low<f.low.rolling(6).min().shift()) if side==1 else (f.high>f.high.rolling(6).max().shift())).astype(float).where(f.close.shift(6).notna()&f.close.notna())
        lev=bounds[symbol]['lower' if side==1 else 'upper'].astype(float)
        touch=(f.low<lev) if side==1 else (f.high>lev)
        one['own_touch']=touch.astype(float).where(lev.notna()&f.close.notna())
        one['own_reclaim']=(touch&(side*(f.close-lev)>0)&(side*(f.open-lev)>0)).astype(float).where(lev.notna()&f.close.notna())
        one['vol_bucket']=np.floor(2*np.log2((tr.rolling(14).mean().shift()/prev).where(prev>0)))
        beta=r.rolling(288,min_periods=288).cov(lr).shift()/lr.rolling(288,min_periods=288).var().shift().replace(0,np.nan)
        one['beta']=beta
        one['relative_touch']=one.touch_return-beta*(side*lr*100)
        one['relative_pre30']=one.pre30-beta*(side*(lf.close.shift()/lf.close.shift(7)-1)*100)
        for h in HORIZONS:
            one[f'after_{h}']=side*(f.close.shift(-h)/f.close-1)*100
            one[f'residual_after_{h}']=one[f'after_{h}']-beta*(side*(lf.close.shift(-h)/lf.close-1)*100)
        extrema=np.column_stack([(f.low if side==1 else -f.high).shift(-j).to_numpy() for j in range(-6,7)])
        valid=np.isfinite(extrema).all(axis=1);offset=np.full(len(f),np.nan)
        offset[valid]=np.argmin(extrema[valid],axis=1)-6
        one['extreme_offset_bars']=offset
        data[symbol]=one
        paths[symbol]=np.column_stack([side*(f.close.shift(-j)/prev-1).to_numpy()*100 for j in range(-12,13)])
    for field in ['pre30','touch_return','shock','volume_ratio','range_ratio','recovery','relative_touch','relative_pre30']+[f'after_{h}' for h in HORIZONS]:
        a=pd.concat([data[s][field] for s in alt_names],axis=1)
        metrics['alt_'+field+'_mean']=a.mean(axis=1)
        metrics['alt_'+field+'_median']=a.median(axis=1)
    for field in ['new_extreme','own_touch','own_reclaim']:
        a=pd.concat([data[s][field] for s in alt_names],axis=1)
        metrics['alt_'+field+'_share']=a.mean(axis=1);metrics['alt_'+field+'_n']=a.notna().sum(axis=1)
    metrics['alt_n']=pd.concat([data[s].touch_return for s in alt_names],axis=1).notna().sum(axis=1)
    metrics['alt_recovery_gt_half']=pd.concat([data[s].recovery.gt(.5).astype(float).where(data[s].recovery.notna()) for s in alt_names],axis=1).mean(axis=1)
    metrics['alt_touch_positive_share']=pd.concat([data[s].touch_return.gt(0).astype(float).where(data[s].touch_return.notna()) for s in alt_names],axis=1).mean(axis=1)
    metrics['alt_extreme_earlier_share']=pd.concat([data[s].extreme_offset_bars.lt(0).astype(float).where(data[s].extreme_offset_bars.notna()) for s in alt_names],axis=1).mean(axis=1)
    for h in HORIZONS:
        a=pd.concat([data[s][f'after_{h}'] for s in alt_names],axis=1)
        metrics[f'alt_positive_after_{h}']=a.gt(0).astype(float).where(a.notna()).mean(axis=1)
    for field in data[leader]:metrics['leader_'+field]=data[leader][field]
    out=pd.DataFrame(metrics,index=index);out['week']=bounds[leader].week
    out['pre_bucket']=np.floor(out.leader_pre30/.5);out['vol_bucket']=data[leader].vol_bucket
    return out,data,paths

def bootstrap_mean(values,days):
    x=pd.DataFrame(dict(value=values,day=days)).dropna()
    if not len(x):return dict(n=0)
    groups=x.groupby('day').value.agg(['sum','count']);rng=np.random.default_rng(20261101)
    draw=rng.integers(0,len(groups),(2000,len(groups)));boot=groups['sum'].to_numpy()[draw].sum(axis=1)/groups['count'].to_numpy()[draw].sum(axis=1)
    return dict(n=len(x),days=len(groups),mean=float(x.value.mean()),median=float(x.value.median()),day_bootstrap95=np.quantile(boot,[.025,.975]).tolist() if len(groups)>=4 else None)

def summarize(g):
    out=dict(events=len(g),days=g.day.nunique(),alt_n_min=int(g.alt_n.min()) if len(g) else 0)
    for c in g:
        if c.startswith('alt_') or c.startswith('leader_'):
            if pd.api.types.is_numeric_dtype(g[c]):out[c]=float(g[c].mean()) if g[c].notna().any() else None
    for h in HORIZONS:
        c=f'alt_after_{h}_mean';out[c+'_uncertainty']=bootstrap_mean(g[c],g.day)
        if 'control_'+c in g:out[c+'_matched_difference']=bootstrap_mean(g[c]-g['control_'+c],g.day)
    return out

def run():
    assert not (EXP/'summary.json').exists(),'Reuse completed study'
    frames,bounds=load();events=event_rows(frames,bounds);assert len(events)
    # Non-event comparison excludes nearby shocks in either leader, including
    # controls preceding a shock. This is a retrospective background comparator.
    blocked=np.zeros(len(next(iter(frames.values()))),bool)
    for i in events.i:blocked[max(0,i-13):min(len(blocked),i+14)]=True
    allrows=[];coinrows=[];controls=[];event_paths=[];path_values=[];ranks=[]
    for leader in ['BTC','ETH']:
        for side in [1,-1]:
            feat,data,paths=all_features(frames,bounds,leader,side)
            chosen=events[events.leader.eq(leader)&events.side.eq(side)]
            pool=feat[~blocked & feat.week.notna() & feat.leader_after_12.notna() & feat.pre_bucket.notna() & feat.vol_bucket.notna()]
            groups={k:g for k,g in pool.groupby(['week','pre_bucket','vol_bucket'])}
            for e in chosen.to_dict('records'):
                i=e['i'];r=feat.iloc[i];one={**e,**{k:v for k,v in r.items() if k not in ['week']}}
                control=groups.get((e['week'],r.pre_bucket,r.vol_bucket),pd.DataFrame())
                one['controls']=len(control)
                if len(control):
                    for col in feat.select_dtypes(include=[np.number]).columns:one['control_'+col]=control[col].mean()
                    controls.append(dict(event_id=e['event_id'],control_times=';'.join(str(t) for t in control.index)))
                allrows.append(one)
                for sym,d in data.items():
                    if sym==leader:continue
                    coinrows.append(dict(event_id=e['event_id'],symbol=sym,leader=leader,side=side,day=e['day'],kind=e['kind'],**d.iloc[i].to_dict()))
                order=pd.DataFrame({s:d.iloc[i] for s,d in data.items() if s!=leader}).T
                for feature in ['relative_pre30','relative_touch','recovery']:
                    z=order.dropna(subset=[feature,'after_6','residual_after_6']).sort_values(feature)
                    if len(z)<12:continue
                    low=z.iloc[:len(z)//3];high=z.iloc[-(len(z)//3):]
                    ranks.append(dict(event_id=e['event_id'],day=e['day'],leader=leader,side=side,feature=feature,n=len(z),high_symbols=';'.join(high.index),low_symbols=';'.join(low.index),
                        high_after30=high.after_6.mean(),low_after30=low.after_6.mean(),difference=high.after_6.mean()-low.after_6.mean(),
                        residual_difference=high.residual_after_6.mean()-low.residual_after_6.mean()))
                path=np.stack([p[i] for s,p in paths.items() if s!=leader]);mean=np.nanmean(path,axis=0)
                if len(control):
                    indices=feat.index.get_indexer(control.index)
                    controlpath=np.nanmean(np.stack([p[indices] for s,p in paths.items() if s!=leader]),axis=(0,1))
                else:controlpath=np.full(25,np.nan)
                for j in range(25):event_paths.append(dict(event_id=e['event_id'],leader=leader,side=side,kind=e['kind'],offset=(j-12)*5,alt_mean=mean[j],leader_return=paths[leader][i,j],control=controlpath[j]))
                path_values.append(dict(event_id=e['event_id'],symbols=list(paths),values=np.stack([paths[s][i] for s in paths]).tolist()))
            print('Analyzed',leader,side,len(chosen),flush=True)
    result=pd.DataFrame(allrows).sort_values(['time','leader']);coins=pd.DataFrame(coinrows);rank=pd.DataFrame(ranks);ep=pd.DataFrame(event_paths)
    result.to_csv(EXP/'events.csv',index=False);coins.to_csv(EXP/'coin_observations.csv.gz',index=False);pd.DataFrame(controls).to_csv(EXP/'controls.csv.gz',index=False)
    rank.to_csv(EXP/'strength_rank_pairs.csv',index=False);ep.to_csv(EXP/'paths.csv',index=False);write(EXP/'path_values.json',path_values)
    groups={'all':result,'first_weekly':result[result.first_weekly],'pure_wick':result[result.kind.eq('pure_wick_reclaim')],'not_reclaimed':result[~result.reclaimed]}
    for leader in ['BTC','ETH']:
        for side,name in [(1,'lower'),(-1,'upper')]:
            g=result[result.leader.eq(leader)&result.side.eq(side)];groups[leader+'_'+name]=g
            groups[leader+'_'+name+'_pure']=g[g.kind.eq('pure_wick_reclaim')]
    summaries={k:summarize(g) for k,g in groups.items() if len(g)}
    rank_summary={}
    for feature,g in rank.groupby('feature'):
        rank_summary[feature]={k:bootstrap_mean(g[k],g.day) for k in ['difference','residual_difference']}
    bycoin=[]
    for sym,g in coins.groupby('symbol'):
        bycoin.append(dict(symbol=sym,n=int(g.touch_return.notna().sum()),volume_ratio_median=g.volume_ratio.median(),range_ratio_median=g.range_ratio.median(),shock_median=g.shock.median(),touch_mean=g.touch_return.mean(),after30_mean=g.after_6.mean(),recovery_mean=g.recovery.mean(),relative_touch_mean=g.relative_touch.mean()))
    pd.DataFrame(bycoin).to_csv(EXP/'by_coin.csv',index=False)
    summary=dict(groups=summaries,strength_ranks=rank_summary,events=len(result),unique_times=result.time.nunique(),days=result.day.nunique(),matched_events=int(result.controls.gt(0).sum()),
                 kinds=result.kind.value_counts().to_dict(),side_counts=result.groupby(['leader','side']).size().to_dict(),
                 start=str(result.time.min()),end=str(result.time.max()),source_end=json.loads((EXP/'config.json').read_text())['end_exclusive'],
                 no_trades_or_winrate=True,training_eligible=False,production_eligible=False)
    summary['side_counts']={str(k):int(v) for k,v in summary['side_counts'].items()}
    write(EXP/'summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in ['groups','strength_ranks']},default=str),flush=True)

if __name__=='__main__':
    assert subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(EXP/'config.json')],cwd=ROOT).returncode==0,'Commit builder first'
    run()

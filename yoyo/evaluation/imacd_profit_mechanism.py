"""Explore original IMACD 34/9 profit mechanisms with causal fills and controls.

Owner request and preregistration: exp-imacd-profit-mechanism-20260907-v2/PLAN.md.
Features use closed OHLC through decision: Wilder ATR14, previous zero run,
previous 34-bar high/low breakout, EMA120's nine-bar slope, and trailing240
ATR/close percentile. Future prices are only outcomes. No training, live writes,
parameter optimization or new TP/SL. Existing OKX data are read-only.
"""
from __future__ import annotations
import hashlib
import itertools
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from .imacd_indicator_audit import ROOT, indicator, aggregate, smma

OUT = ROOT/'experiments/active/exp-imacd-profit-mechanism-20260907-v2'
DATA_OUT = ROOT/'data/imacd_profit_mechanism_v2'
COST_BP = 20.0
SEED = 20260907
FOLDS = [('discovery','2023-01-01','2025-01-01'), ('replication','2025-01-01','2026-01-01'), ('exposed','2026-01-01','2026-07-01')]
POLICIES = [('cross_signal','cross','signal'), ('departure_signal','departure','signal'), ('departure_neutral','departure','neutral'), ('departure_opposite','departure','opposite')]


def features(b):
    """Closed-bar causal state, ATR14, trailing240 rank and prior34 breakout."""
    f=indicator(b)
    tr=pd.concat([b.high-b.low,(b.high-b.close.shift()).abs(),(b.low-b.close.shift()).abs()],axis=1).max(axis=1)
    f['atr']=smma(tr,14)
    f['volbin']=np.ceil((f.atr/b.close).rolling(240,min_periods=120).rank(pct=True)*5).clip(1,5)
    z=0; runs=[]
    for v in f.md:
        runs.append(z); z=z+1 if v==0 else 0
    f['zero_before']=runs
    f['departure']=np.where((f.md.shift()==0)&(f.md!=0),np.sign(f.md),0).astype(int)
    f['cross']=np.where(f.up,1,np.where(f.dn,-1,0))
    f['zone']=np.sign(f.md).astype(int)
    f['breakup']=b.close>b.high.shift().rolling(34).max()
    f['breakdown']=b.close<b.low.shift().rolling(34).min()
    e=b.close.ewm(span=120,adjust=False).mean()
    f['slow_slope']=(e-e.shift(9))/f.atr
    f['eligible']=np.arange(len(f))>=340
    return f


def exit_arrays(f):
    return {(side,mode):np.flatnonzero(mask.to_numpy()) for side in (1,-1) for mode,mask in (
        ('signal',f.dn if side==1 else f.up),
        ('neutral',f.md*side<=0),
        ('opposite',f.md*side<0))}


def outcome(b, exits, i, side, mode, last):
    """Decision i fills i+1 open. Exit decision strictly after i, then next open.

    Last is final available bar of this fold; unfinished requests are marked at
    its close, not dropped. MFE/MAE exclude the exit bar after an open fill.
    """
    if i+1>last: raise ValueError('no next entry open in fold')
    seq=exits[side,mode];k=np.searchsorted(seq,i,side='right')
    j=int(seq[k]) if k<len(seq) else last
    natural=j+1<=last
    x=j+1 if natural else last
    price=float(b.open.iloc[x] if natural else b.close.iloc[last])
    entry=float(b.open.iloc[i+1]); stop=x if natural else last+1
    path=b.iloc[i+1:stop]
    high=max(entry,price,float(path.high.max())); low=min(entry,price,float(path.low.min()))
    gross=side*(price/entry-1)*10000
    mfe=((high/entry-1) if side==1 else (1-low/entry))*10000
    mae=((low/entry-1) if side==1 else (1-high/entry))*10000
    return dict(entry_i=i+1,exit_i=x,entry_price=entry,exit_price=price,gross_bp=gross,net_bp=gross-COST_BP,
                mfe_bp=mfe,mae_bp=mae,hold_bars=stop-(i+1),exit_kind='natural' if natural else 'boundary_mark')


def match_controls(f, indexes, kind, rng):
    """Match without reading future outcome, no control reuse within entry family."""
    pools={}; used=set(); result={}
    for i in indexes:
        r=f.iloc[i]
        if r[kind]!=0:continue
        key=(str(f.index[i].tz_localize(None).to_period('M')),int(r.volbin),int(r.zone))
        pools.setdefault(key,[]).append(int(i))
    for key in pools:rng.shuffle(pools[key])
    for i in indexes:
        r=f.iloc[i]
        if r[kind]==0:continue
        key=(str(f.index[i].tz_localize(None).to_period('M')),int(r.volbin),int(r.zone))
        pool=pools.get(key,[])
        chosen=[]
        while pool and len(chosen)<3:
            c=pool.pop()
            if c not in used:chosen.append(c);used.add(c)
        result[int(i)]=chosen
    return result


def inference(values, months):
    """Month-cluster bootstrap and one-sided sign randomization; not an RCT."""
    a=pd.DataFrame({'v':values,'m':months}).dropna()
    if not len(a):return dict(excess_bp=None,p=None,ci_low=None,ci_high=None,months=0)
    if not np.isfinite(a.v.to_numpy()).all():raise ValueError('nonfinite inference input')
    g=a.groupby('m').v.agg(['sum','count']);s=g['sum'].to_numpy();n=g['count'].to_numpy();k=len(g)
    rng=np.random.default_rng(SEED)
    ix=rng.integers(0,k,size=(3999,k));boot=s[ix].sum(1)/n[ix].sum(1)
    if k<=12:
        signs=np.array(list(itertools.product([-1,1],repeat=k)))
        # Elementwise reduction avoids this host BLAS's spurious FP warnings.
        p=float(np.mean(np.sum(signs*s[None,:],axis=1)>=s.sum()-1e-10))
    else:
        signs=rng.choice([-1,1],size=(3999,k));p=float((1+np.sum(np.sum(signs*s[None,:],axis=1)>=s.sum()-1e-10))/4000)
    return dict(excess_bp=float(a.v.mean()),p=p,ci_low=float(np.quantile(boot,.025)),ci_high=float(np.quantile(boot,.975)),months=k)


def distribution(x):
    x=np.asarray(x,dtype=float)
    if not len(x):return dict(n=0)
    wins=x[x>0]; losses=x[x<0]; top=np.sort(x)[-max(1,int(np.ceil(len(x)*.1))):]
    return dict(n=len(x),mean_net_bp=float(x.mean()),median_net_bp=float(np.median(x)),win_pct=float((x>0).mean()*100),
                pf=float(wins.sum()/-losses.sum()) if len(losses) else None,p90_net_bp=float(np.quantile(x,.9)),
                best_net_bp=float(x.max()),worst_net_bp=float(x.min()),top_realized_decile_sum_bp=float(top.sum()),
                rest_realized_mean_bp=float(np.sort(x)[:-len(top)].mean()) if len(x)>len(top) else None,
                top5_positive_share_pct=float(np.sort(wins)[-5:].sum()/wins.sum()*100) if len(wins) else None)


def rank_baseline(events):
    """Predetermined signed md/ATR ranking, evaluated descriptively per fold."""
    y=events.net_bp>0;s=events.strength;n1=int(y.sum());n0=len(y)-n1
    auc=(float(s.rank().loc[y].sum())-n1*(n1+1)/2)/(n1*n0) if n1 and n0 else None
    ranked=events.sort_values(['strength','event_id'],ascending=[False,True]).head(max(1,int(np.ceil(len(events)*.1))))
    return dict(strength_auc=auc,score_top_decile_n=len(ranked),score_top_decile_gross_bp=float(ranked.gross_bp.mean()),
                score_top_decile_net_bp=float(ranked.net_bp.mean()),score_top_decile_win_pct=float((ranked.net_bp>0).mean()*100),
                score_top_decile_excess_bp=float(ranked.excess_bp.mean()) if ranked.excess_bp.notna().any() else None)


def single_position(b, events):
    """One position; fixed initial-capital notional, close-marked equity, no leverage engine."""
    accepted=[]; last=-1
    for r in events.sort_values('entry_i').to_dict('records'):
        if r['entry_i']>=last:
            accepted.append(r);last=r['exit_i']+(r['exit_kind']=='boundary_mark')
    if not accepted:return dict(trades=0,blocked=len(events)),[]
    eq=[1.];cash=1.
    for r in accepted:
        a=r['entry_i'];z=r['exit_i'];end=z if r['exit_kind']=='natural' else z+1
        marks=cash+r['side']*(b.close.iloc[a:end].to_numpy()/r['entry_price']-1)-COST_BP/20000
        eq.extend(marks.tolist());cash+=r['net_bp']/10000;eq.append(cash)
    eq=np.array(eq);peak=np.maximum.accumulate(eq)
    metrics=distribution([r['net_bp'] for r in accepted])
    metrics.update(trades=len(accepted),blocked=len(events)-len(accepted),fixed_notional_return_pct=(cash-1)*100,
                   close_marked_drawdown_pct=float(((peak-eq)/peak).max()*100))
    return metrics,accepted


def run_series(symbol,minutes,b,all_events,all_controls,summaries,groups,portfolios):
    expected=pd.Timedelta(minutes=minutes)
    if not b.index.to_series().diff().iloc[1:].eq(expected).all():raise ValueError('source gap; do not bridge')
    f=features(b);exits=exit_arrays(f)
    for fold,start,end in FOLDS:
        start=pd.Timestamp(start,tz='UTC');end=pd.Timestamp(end,tz='UTC')
        valid=np.flatnonzero(((b.index>=start)&(b.index+expected<=end)).astype(bool))
        if not len(valid):continue
        last=int(valid[-1]);indexes=np.array([i for i in valid if i<last and f.eligible.iloc[i] and pd.notna(f.volbin.iloc[i])])
        if not len(indexes):continue
        maps={kind:match_controls(f,indexes,kind,np.random.default_rng(SEED+(kind=='cross'))) for kind in ('cross','departure')}
        for policy,kind,mode in POLICIES:
            rows=[]
            for i,controls in maps[kind].items():
                side=int(f[kind].iloc[i]);eid=f'{symbol}_{minutes}_{fold}_{kind}_{i}'
                result=outcome(b,exits,i,side,mode,last)
                ctrl=[]
                for c in controls:
                    cr=outcome(b,exits,c,side,mode,last);ctrl.append(cr['net_bp'])
                    all_controls.append(dict(symbol=symbol,minutes=minutes,fold=fold,policy=policy,event_id=eid,control_i=c,side=side,**cr))
                r=dict(symbol=symbol,minutes=minutes,fold=fold,policy=policy,event_id=eid,signal_i=i,side=side,
                       signal_time=b.index[i].isoformat(),entry_time=b.index[i+1].isoformat(),
                       exit_bar_time=b.index[result['exit_i']].isoformat(),month=str(b.index[i].tz_localize(None).to_period('M')),
                       zero_before=int(f.zero_before.iloc[i]),breakout=bool(f.breakup.iloc[i] if side==1 else f.breakdown.iloc[i]),
                       trend_aligned=bool(side*f.slow_slope.iloc[i]>0),strength=float(side*f.md.iloc[i]/f.atr.iloc[i]),
                       control_n=len(ctrl),control_mean_net_bp=float(np.mean(ctrl)) if len(ctrl)==3 else np.nan,
                       **result)
                r['excess_bp']=r['net_bp']-r['control_mean_net_bp'];rows.append(r)
            if not rows:continue
            events=pd.DataFrame(rows);all_events.extend(rows)
            q=dict(symbol=symbol,minutes=minutes,fold=fold,policy=policy,observed_start=b.index[indexes[0]].isoformat(),observed_end_close=(b.index[last]+expected).isoformat(),**distribution(events.net_bp),**rank_baseline(events),
                   mean_gross_bp=float(events.gross_bp.mean()),median_hold_bars=float(events.hold_bars.median()),
                   mean_mfe_bp=float(events.mfe_bp.mean()),median_mae_bp=float(events.mae_bp.median()),
                   boundary_marks=int((events.exit_kind=='boundary_mark').sum()),matched_n=int(events.excess_bp.notna().sum()),
                   controls_mean_net_bp=float(events.control_mean_net_bp.mean()) if events.control_mean_net_bp.notna().any() else None,
                   **inference(events.excess_bp,events.month))
            pm,accepted=single_position(b,events);q.update({'portfolio_'+k:v for k,v in pm.items()})
            portfolios.extend(accepted);summaries.append(q)
            for feature,masks in [('side',{'long':events.side==1,'short':events.side==-1}),
                                  ('zero_before',{'0':events.zero_before==0,'1':events.zero_before==1,'2_to_8':events.zero_before.between(2,8),'9_plus':events.zero_before>=9}),
                                  ('breakout',{'yes':events.breakout,'no':~events.breakout}),
                                  ('trend_aligned',{'yes':events.trend_aligned,'no':~events.trend_aligned})]:
                for name,mask in masks.items():
                    g=events.loc[mask]
                    if len(g):groups.append(dict(symbol=symbol,minutes=minutes,fold=fold,policy=policy,feature=feature,group=name,
                                                 **distribution(g.net_bp),matched_n=int(g.excess_bp.notna().sum()),
                                                 controls_mean_net_bp=float(g.control_mean_net_bp.mean()) if g.control_mean_net_bp.notna().any() else None,
                                                 **inference(g.excess_bp,g.month)))


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    DATA_OUT.mkdir(parents=True,exist_ok=True)
    all_events=[];all_controls=[];summaries=[];groups=[];portfolios=[];sources=[]
    for symbol in ('BTC','ETH'):
        for base,glob,targets in [(15,f'data/kline_deep/okx_{symbol}_USDT_SWAP_15m_*.csv',[15,30,60,120,240,360,720,1440]),
                                  (3,f'data/kline_fetched/okx_{symbol}_USDT_SWAP_3m_*.csv',[3]),
                                  (5,f'data/kline_fetched/okx_{symbol}_USDT_SWAP_5m_*.csv',[5]),
                                  (1,f'data/kline_fetched/okx_{symbol}_USDT_SWAP_1m_*.csv',[1])]:
            paths=list(ROOT.glob(glob))
            if not paths:continue
            if len(paths)!=1:raise ValueError('ambiguous source')
            path=paths[0];data=path.read_bytes();sources.append(dict(path=str(path.relative_to(ROOT)),sha256=hashlib.sha256(data).hexdigest()))
            b=pd.read_csv(path);b.index=pd.DatetimeIndex(pd.to_datetime(b.open_time,utc=True));b=b[['open','high','low','close']].astype(float)
            if b.index.has_duplicates or not b.index.is_monotonic_increasing or not np.isfinite(b.to_numpy()).all():raise ValueError('invalid data')
            if (b<=0).any().any() or (b.low>b[['open','close']].min(axis=1)).any() or (b.high<b[['open','close']].max(axis=1)).any():raise ValueError('invalid OHLC geometry')
            if not b.index.equals(b.index.floor(f'{base}min')):raise ValueError('unaligned')
            b=b.loc[b.index+pd.Timedelta(minutes=base)<=pd.Timestamp('2026-07-01',tz='UTC')]
            for minutes in targets:
                run_series(symbol,minutes,aggregate(b,minutes,base),all_events,all_controls,summaries,groups,portfolios)
                print(f'{symbol} {minutes}m done',flush=True)
    summary=pd.DataFrame(summaries)
    # Predeclared family: every replication cell, with monotone Holm correction.
    sel=summary.loc[(summary.fold=='replication')&summary.p.notna()].sort_values('p')
    adj=np.maximum.accumulate(np.minimum(1,sel.p.to_numpy()*(len(sel)-np.arange(len(sel)))))
    summary.loc[sel.index,'p_holm_replication']=adj
    output_files=[]
    for name,frame in [('summary',summary),('events',pd.DataFrame(all_events)),('controls',pd.DataFrame(all_controls)),('groups',pd.DataFrame(groups)),('portfolio_trades',pd.DataFrame(portfolios))]:
        path=(OUT if name in ('summary','groups') else DATA_OUT)/(name+'.csv')
        frame.to_csv(path,index=False)
        content=path.read_bytes();output_files.append(dict(path=str(path.relative_to(ROOT)),rows=len(frame),sha256=hashlib.sha256(content).hexdigest(),size_bytes=len(content)))
    manifest=dict(configuration='profit_mechanism_v2',builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),sources=sources,
                  owner_authorization='Prior turn: OKX all periods and all dates allowed; current turn: discover profitable patterns.',
                  holdout_consumptions={p:1 for p,_,_ in POLICIES},cost_bp=COST_BP,seed=SEED,folds=FOLDS,
                  raw_data_written=False,training_eligible=False,production_eligible=False,summary_rows=len(summary),output_files=output_files,
                  limitations=['No funding ledger','Fixed20bp cost proxy','No protective stop or margin/liquidation engine','Boundary marks include forced closes','Exploratory reused history','Monthly dependence assumption'])
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')

def refresh_statistics():
    """Recompute inference from saved ledgers; retain original warned outputs."""
    events=pd.read_csv(DATA_OUT/'events.csv')
    summary=pd.read_csv(OUT/'summary.csv');groups=pd.read_csv(OUT/'groups.csv')
    keyed=dict(tuple(events.groupby(['symbol','minutes','fold','policy'])))
    before={name:hashlib.sha256((DATA_OUT/name).read_bytes()).hexdigest() for name in ('events.csv','controls.csv','portfolio_trades.csv')}
    for frame in (summary,groups):
        for i,r in frame.iterrows():
            g=keyed[(r.symbol,r.minutes,r.fold,r.policy)]
            if frame is groups:
                name=r['group'];feature=r.feature
                if feature=='side':g=g.loc[g.side==(1 if name=='long' else -1)]
                elif feature=='zero_before':
                    mask=g.zero_before>=9 if name=='9_plus' else g.zero_before.between(2,8) if name=='2_to_8' else g.zero_before==int(name)
                    g=g.loc[mask]
                else:g=g.loc[g[feature]==(name=='yes')]
            for key,value in inference(g.excess_bp,g.month).items():frame.loc[i,key]=value
            frame.loc[i,'matched_case_mean_net_bp']=g.loc[g.excess_bp.notna(),'net_bp'].mean()
    sel=summary.loc[(summary.fold=='replication')&summary.p.notna()].sort_values('p')
    summary.loc[sel.index,'p_holm_replication']=np.maximum.accumulate(np.minimum(1,sel.p.to_numpy()*(len(sel)-np.arange(len(sel)))))
    original={}
    for name in ('summary','groups','manifest'):
        path=OUT/(name+('.json' if name=='manifest' else '.csv'))
        target=path.with_name(path.stem+'_first_run'+path.suffix)
        if target.exists():raise ValueError('first-run evidence already archived')
        original[name]=dict(path=str(target.relative_to(ROOT)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        target.write_bytes(path.read_bytes())
    summary.to_csv(OUT/'summary.csv',index=False);groups.to_csv(OUT/'groups.csv',index=False)
    m=json.loads((OUT/'manifest.json').read_text());m['first_run_outputs']=original
    m['statistics_refreshed_commit']=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    m['statistics_refresh_reason']='Host BLAS emitted spurious FP warnings on finite input; explicit product-and-sum kernel independently checked. No price replay.'
    for file in m['output_files']:
        path=ROOT/file['path'];file['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();file['size_bytes']=path.stat().st_size
    (OUT/'manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
    after={name:hashlib.sha256((DATA_OUT/name).read_bytes()).hexdigest() for name in before}
    assert before==after
    (OUT/'statistics_refresh_qa.json').write_text(json.dumps(dict(ledgers_unchanged=before==after,ledger_hashes=after,
        max_p_difference=float((summary.p-pd.read_csv(OUT/'summary_first_run.csv').p).abs().max())),indent=2)+'\n')
    print('Inference refreshed; every saved trade/control byte unchanged.')


if __name__=='__main__':
    import sys
    refresh_statistics() if '--refresh-statistics' in sys.argv else main()

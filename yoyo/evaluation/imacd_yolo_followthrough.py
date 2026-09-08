"""Frozen post-signal path audit, separate from causal YOLO model inputs.

Source: exp-imacd-yolo-followthrough-20260908-v1/PROJECT_PLAN.md. Reuses
saved decisions; no model inference, threshold change, training or live writes.
Features use OHLCV only through each closed anchor; ATR/close quintiles use a
trailing 240-bar percentile including that anchor. Future OHLC is used only
for forward-return/excursion labels and physically separate review contexts.
"""
from __future__ import annotations
import hashlib
import itertools
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
from .imacd_formation_research import aggregate, read_prefix
from .imacd_startup_quality import build_features, MA_COLUMNS

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-imacd-yolo-followthrough-20260908-v1'
DATA=ROOT/'data/imacd_yolo_followthrough_20260908_v1'
PRIOR=ROOT/'experiments/active/exp-imacd-yolo-expanded-20260908-v1'
OLD_DATA=ROOT/'data/imacd_yolo_expanded_20260908_v1'
HORIZONS=(6,24,72)
SEED=20260908
COST_BP=20.0
BOUNDS={'pre_holdout':(pd.Timestamp('2026-01-01',tz='UTC'),pd.Timestamp('2026-05-04',tz='UTC')),
        'holdout_review':(pd.Timestamp('2026-05-04',tz='UTC'),pd.Timestamp('2026-07-01',tz='UTC'))}


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def observe(bars, anchor, side, horizon, end, atr):
    """Next open at anchor+1 to close at anchor+horizon, including all H bars.

    Labels alone read those future open/high/low/close columns. MFE and MAE
    include the entry reference (zero); MAE is a nonnegative adverse magnitude.
    No barrier order, intrabar execution or hypothetical leveraged R is inferred.
    """
    entry_i=int(anchor)+1; end_i=int(anchor)+int(horizon)
    out=dict(anchor_i=int(anchor),entry_i=entry_i,end_i=end_i,atr_anchor=float(atr),
             complete=False,observed_bars=max(0,min(horizon,len(bars)-entry_i)),
             entry_at=None,exit_at=None,entry_price=None,exit_price=None,
             gross_bp=None,net_bp=None,mfe_bp=None,mae_bp=None,mfe_atr=None,mae_atr=None)
    if entry_i >= len(bars): return out
    step=bars.index[1]-bars.index[0]
    allowed=(bars.index[entry_i:entry_i+horizon]+step<=end)
    out['observed_bars']=int(allowed.sum())
    if end_i>=len(bars) or out['observed_bars']!=horizon: return out
    future=bars.iloc[entry_i:end_i+1]
    entry=float(future.open.iloc[0]); last=float(future.close.iloc[-1])
    favorable=max(0.,float(future.high.max())-entry) if side==1 else max(0.,entry-float(future.low.min()))
    adverse=max(0.,entry-float(future.low.min())) if side==1 else max(0.,float(future.high.max())-entry)
    gross=side*(last/entry-1)*10000
    out.update(complete=True,entry_at=bars.index[entry_i].isoformat(),exit_at=(bars.index[end_i]+step).isoformat(),
        entry_price=entry,exit_price=last,gross_bp=gross,net_bp=gross-COST_BP,
        mfe_bp=favorable/entry*10000,mae_bp=adverse/entry*10000,
        mfe_atr=favorable/atr if np.isfinite(atr) and atr>0 else None,
        mae_atr=adverse/atr if np.isfinite(atr) and atr>0 else None)
    return out


def clustered_null(values, clocks):
    """Exploratory ISO-week block sign test, not an RCT or profit certification.

    All symbols and reused controls within a week share a sign. Label overlap
    across neighboring weeks remains; report that limitation and block count.
    Only the 24-bar holdout four-group family receives primary Holm adjustment.
    """
    a=pd.DataFrame({'value':values,'clock':pd.to_datetime(clocks,utc=True)}).dropna()
    if a.empty: return dict(p=None,blocks=0,method='no matched samples')
    iso=a.clock.dt.isocalendar(); a['block']=iso.year.astype(str)+'-'+iso.week.astype(str)
    totals=a.groupby('block').value.sum().to_numpy(float); observed=totals.sum(); n=len(totals)
    if n<=18:
        signs=np.asarray(list(itertools.product((-1.,1.),repeat=n)))
        p=float(((signs@totals)>=observed-1e-10).mean()); method=f'exact {2**n} ISO-week sign patterns'
    else:
        sims=np.random.default_rng(SEED).choice((-1.,1.),size=(10000,n))@totals
        p=float((1+(sims>=observed-1e-10).sum())/10001); method='10000 ISO-week sign draws; plus-one p'
    return dict(p=p,blocks=n,method=method)


def summarize(outcomes):
    rows=[]
    for keys,g in outcomes.groupby(['fold','timeframe_min','anchor_kind','mode','horizon'],sort=True):
        q=g.loc[g.complete]; matched=q.loc[q.control_n.eq(3)]
        def mean(name,frame=q): return float(frame[name].mean()) if len(frame) else None
        row=dict(zip(['fold','timeframe_min','anchor_kind','mode','horizon'],keys))
        row.update(total=len(g),complete=len(q),censored=len(g)-len(q),symbols=int(q.symbol.nunique()),
            gross_mean_bp=mean('gross_bp'),net_mean_bp=mean('net_bp'),
            gross_median_bp=float(q.gross_bp.median()) if len(q) else None,
            direction_rate_pct=float(q.gross_bp.gt(0).mean()*100) if len(q) else None,
            net_positive_rate_pct=float(q.net_bp.gt(0).mean()*100) if len(q) else None,
            mfe_median_bp=float(q.mfe_bp.median()) if len(q) else None,
            mae_median_bp=float(q.mae_bp.median()) if len(q) else None,
            mfe_median_atr=float(q.mfe_atr.median()) if len(q) else None,
            mae_median_atr=float(q.mae_atr.median()) if len(q) else None,
            matched=len(matched),matched_case_net_bp=mean('net_bp',matched),
            control_net_bp=mean('control_mean_net_bp',matched),excess_mean_bp=mean('matched_excess_bp',matched))
        if keys[0]=='holdout_review' and keys[2]=='confirmation' and keys[4]==24:
            row.update(clustered_null(matched.matched_excess_bp,matched.entry_at))
        else: row.update(p=None,blocks=None,method='descriptive; not another primary test')
        row['p_holm']=None; rows.append(row)
    family=[r for r in rows if r['p'] is not None]
    family.sort(key=lambda r:r['p']); running=0.
    # Four hypotheses remain the family even if a group has no matched data.
    for rank,row in enumerate(family):
        running=max(running,min(1.,(4-rank)*row['p'])); row['p_holm']=running
    return rows


def run():
    source_paths=['yoyo/evaluation/imacd_yolo_followthrough.py',str((EXP/'PROJECT_PLAN.md').relative_to(ROOT))]
    for source in source_paths:
        subprocess.run(['git','ls-files','--error-unmatch',source],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
        if subprocess.check_output(['git','status','--porcelain','--',source],cwd=ROOT,text=True).strip():
            raise ValueError('Commit source and plan before outcome evaluation')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    prior=json.loads((PRIOR/'results/summary.json').read_text()); universe=json.loads((PRIOR/'universe.json').read_text())
    dp=OLD_DATA/'decisions.csv'
    if sha(dp)!=prior['files'][str(dp.relative_to(ROOT))]: raise ValueError('Changed decisions')
    d=pd.read_csv(dp)
    if len(d)!=1341 or d.event_id.duplicated().any(): raise ValueError('Unexpected frozen event universe')
    if (EXP/'results/summary.json').exists() or DATA.exists(): raise ValueError('Refuse overwrite/re-evaluation')
    (DATA/'contexts').mkdir(parents=True); (EXP/'results').mkdir(parents=True,exist_ok=True)
    inputs={str(dp.relative_to(ROOT)):sha(dp),str((PRIOR/'results/summary.json').relative_to(ROOT)):sha(PRIOR/'results/summary.json'),
            str((PRIOR/'universe.json').relative_to(ROOT)):sha(PRIOR/'universe.json')}
    for dependency in [*source_paths, 'yoyo/evaluation/imacd_formation_research.py', 'yoyo/evaluation/imacd_startup_quality.py', 'yoyo/monitor/signals.py']:
        inputs[dependency]=sha(ROOT/dependency)
    rows=[]; controls=[]; coverage=[]; context_paths=[]
    for number,source in enumerate(universe['sources']):
        path=ROOT/source['path']
        if sha(path)!=source['sha256']: raise ValueError('Raw source identity changed '+source['symbol'])
        raw=read_prefix(path,end=BOUNDS['holdout_review'][1]); inputs[source['path']]=source['sha256']
        for minutes in (60,240):
            symbol=source['symbol']; bars=aggregate(raw,minutes)
            info=prior['inputs'][f'{symbol}_{minutes}_holdout_review']
            if hashlib.sha256(bars.to_csv().encode()).hexdigest()!=info['bounded_ohlcv_sha256']:
                raise ValueError('Aggregate mismatch '+symbol)
            features=build_features(bars)
            volatility=(features.atr/bars.close).rolling(240,min_periods=240).rank(pct=True)
            volbin=np.ceil(volatility*5).clip(1,5).fillna(-1).to_numpy(int)
            recent_release=features.release_side.ne(0).rolling(10,min_periods=1).max().to_numpy(bool)
            group=d.loc[d.symbol.eq(symbol)&d.timeframe_min.eq(minutes)]
            keep_start=max(0,min(int(group.signal_i.min())-160,int(group.setup_start_i.min())-20)) if len(group) else 0
            first_control=int(bars.index.searchsorted(BOUNDS['pre_holdout'][0]-pd.Timedelta(minutes=minutes)))
            keep_start=max(0,min(keep_start,first_control-240))
            ctx=bars.copy(); ctx['bar_i']=np.arange(len(bars)); ctx['open_time']=bars.index.astype(str)
            for name in ['atr','md','sb','release_side','ready',*MA_COLUMNS]: ctx[name]=features[name].to_numpy()
            ctx['volbin']=volbin
            cp=DATA/'contexts'/f'{symbol}_{minutes}.csv.gz'; ctx.iloc[keep_start:].to_csv(cp,index=False); context_paths.append(cp)
            availability=bars.index+pd.Timedelta(minutes=minutes)
            month=availability.strftime('%Y-%m').to_numpy(); atr=features.atr.to_numpy()
            for fold,(start,end) in BOUNDS.items():
                pools={}
                for i in np.flatnonzero((availability>=start)&(availability<end)&(volbin>0)&~recent_release):
                    pools.setdefault((month[i],volbin[i]),[]).append(int(i))
                events=group.loc[group.fold.eq(fold)].sort_values(['signal_i','event_id'])
                for event in events.to_dict('records'):
                    p=int(event['signal_i']); side=int(event['side'])
                    if bars.index[p]!=pd.Timestamp(event['signal_open_at']): raise ValueError('Signal clock mismatch')
                    if int(features.release_side.iloc[p])!=side: raise ValueError('Frozen release parity mismatch')
                    mode=('immediate' if event['delay_bars']==0 else 'delayed') if event['status']=='confirmed' else ('censored' if event['status']=='censored_end' else 'unconfirmed')
                    anchors=[('imacd',p)]
                    if event['status']=='confirmed':
                        e=int(event['confirmation_i'])
                        if availability[e]!=pd.Timestamp(event['confirmation_available_at']): raise ValueError('Confirmation clock mismatch')
                        anchors.append(('confirmation',e))
                    for anchor_kind,anchor in anchors:
                        pool=pools.get((month[anchor],volbin[anchor]),[])
                        seed=int.from_bytes(hashlib.sha256(f'{SEED}|{event["event_id"]}|{anchor_kind}'.encode()).digest()[:8],'big')
                        chosen=np.random.default_rng(seed).choice(pool,size=min(3,len(pool)),replace=False) if pool else []
                        for horizon in HORIZONS:
                            obs=observe(bars,anchor,side,horizon,end,atr[anchor])
                            row=dict(event,mode=mode,anchor_kind=anchor_kind,horizon=horizon,**obs)
                            row.update(control_selected_n=len(chosen),control_anchor_ids=';'.join(str(int(c)) for c in chosen),control_n=0,control_mean_net_bp=None,matched_excess_bp=None,same_exit_imacd_gross_bp=None)
                            if obs['complete']:
                                if anchor_kind=='confirmation':
                                    row['same_exit_imacd_gross_bp']=side*(obs['exit_price']/float(bars.open.iloc[p+1])-1)*10000
                                values=[]
                                for c in chosen:
                                    co=observe(bars,int(c),side,horizon,end,atr[c])
                                    controls.append(dict(event_id=event['event_id'],symbol=symbol,timeframe_min=minutes,fold=fold,
                                        anchor_kind=anchor_kind,mode=mode,horizon=horizon,side=side,volbin=int(volbin[c]),
                                        case_anchor_i=anchor,control_anchor_i=int(c),**co))
                                    if co['complete']: values.append(co['net_bp'])
                                row['control_n']=len(values)
                                if len(values)==3:
                                    row['control_mean_net_bp']=float(np.mean(values)); row['matched_excess_bp']=obs['net_bp']-np.mean(values)
                            rows.append(row)
                coverage.append(dict(symbol=symbol,timeframe_min=minutes,fold=fold,events=len(events),bars=len(bars)))
        print(json.dumps(dict(completed_symbols=number+1,total_symbols=54,rows=len(rows))),flush=True)
    outcomes=pd.DataFrame(rows); control_frame=pd.DataFrame(controls)
    outcomes.to_csv(DATA/'outcomes.csv',index=False); control_frame.to_csv(DATA/'controls.csv',index=False)
    reused=control_frame.groupby(['symbol','timeframe_min','control_anchor_i']).size()
    result=dict(source_commit=commit,source_inputs=inputs,source_sha256=sha(Path(__file__)),model_inference_runs=0,
        holdout_consumptions={'60':3,'240':3},authorization='Owner explicitly requests post-signal paths; unrestricted-date authorization persists.',
        rules=dict(horizons=list(HORIZONS),primary_horizon=24,cost_bp=COST_BP,features='Through anchor only; rolling240 ATR/close quintiles',
            controls='Same symbol/timeframe/anchor-close UTC month/volatility quintile; same side,H,cost; no release in prior10 inclusive. 3 unique per event/anchor, frozen across horizons; incomplete controls not replaced; reuse across cases allowed.',
            interpretation='Fixed-horizon paths and static cost sensitivity, not portfolio PnL; post-hoc unconfirmed grouping is not a tradable policy.',
            inference='4 holdout24 confirmation TF/mode tests; ISO-week exploratory sign test, Holm4. Adjacent-week label dependence remains.',
            future='Human-review outputs only; no original model inputs or predictions modified.'),
        summary_rows=summarize(outcomes),coverage=coverage,
        control_reuse=dict(unique_symbol_tf_anchors=len(reused),anchors_used_more_than_once=int(reused.gt(1).sum()),
            note='Includes intentional reuse across horizons, anchor kinds and cases; not independent observations.'),
        files={str(p.relative_to(ROOT)):sha(p) for p in [DATA/'outcomes.csv',DATA/'controls.csv',*context_paths]},
        training_eligible=False,production_eligible=False)
    def default(x):
        if isinstance(x,np.generic): return x.item()
        raise TypeError(type(x).__name__)
    (EXP/'results/summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=default,allow_nan=False)+'\n')
    print(json.dumps(dict(outcomes=len(outcomes),controls=len(control_frame),summary=str(EXP/'results/summary.json'))))

if __name__=='__main__': run()

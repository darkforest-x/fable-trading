"""Fresh serial queues and pre2025-only state strata for V13.1 rule research.

Paths are shared, not occupancy. Signal/control eligibility is determined before
outcomes; unknown outcomes stay unknown. Feature gates use each random anchor's
own causal feature under the same frozen training-period cutpoints. All metrics
are equal-risk R statistics, never a shared-capital account simulation.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v131_rule_facts import ARMS,FEATURES
from yoyo.evaluation.spike_v131_rule_study import CONFIG,BASE
from yoyo.evaluation.spike_v131_entry_stages import read,dump,known
from yoyo.evaluation.spike_v131_excursions import provenance
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python

POLICIES=(*ARMS,*(f'{feature}_{band}' for feature in FEATURES for band in ('known','low','mid','high')))
FIELDS=('target','random','baseline_target','baseline_random','delta','excess','incremental_excess')


def epoch(values):
    return pd.to_datetime(values,utc=True).astype('datetime64[ns, UTC]').astype('int64').to_numpy()//1_000_000_000


def cutpoints(paths,cfg):
    """Fit only on baseline anchor features before the time split; no labels."""
    output={};a=cfg['analysis_start'];b=cfg['split']
    for m in cfg['timeframes']:
        samples=[]
        for path in paths:
            f=read(path/f'{m}m.csv.gz',['cohort','members','anchor_time',*FEATURES])
            if not len(f):continue
            t=pd.to_datetime(f.anchor_time,utc=True)
            samples.append(f.loc[f.cohort.eq('signal') & ((f.members.to_numpy(int)&1)!=0) &
                (t>=pd.to_datetime(a,utc=True)) & (t<pd.to_datetime(b,utc=True)),list(FEATURES)])
        pool=pd.concat(samples,ignore_index=True) if samples else pd.DataFrame(columns=FEATURES)
        output[str(m)]={}
        for name in FEATURES:
            values=pool[name].to_numpy(float);values=values[np.isfinite(values)]
            q=np.quantile(values,cfg['feature_quantiles']) if len(values) else [None,None]
            output[str(m)][name]=dict(n=len(values),lower=q[0],upper=q[1])
    return output


def eligibility(f,thresholds):
    """Independent one-feature bands on baseline anchors; unknown has no band."""
    result={arm:(f.members.to_numpy(np.int64)&(1<<i))!=0 for i,arm in enumerate(ARMS)}
    for name in FEATURES:
        v=f[name].to_numpy(float);t=thresholds[name]
        valid=result['baseline'] & np.isfinite(v)
        result[name+'_known']=valid
        if t['lower'] is None:
            for band in ('low','mid','high'):result[name+'_'+band]=np.zeros(len(f),bool)
            continue
        result[name+'_low']=valid & (v<=t['lower'])
        result[name+'_mid']=valid & (v>t['lower']) & (v<=t['upper'])
        result[name+'_high']=valid & (v>t['upper'])
    return result


def serial_flags(f,eligible):
    """Order by closed decision, original anchor, stable ID; exit close frees slot."""
    order=f.sort_values(['decision_i','anchor_i','trade_key']).index.to_numpy()
    take=np.zeros(len(f),bool);flat=-1
    fill=f.filled.to_numpy(bool);di=f.decision_i.to_numpy(int)
    xi=f.exit_i.fillna(-1).to_numpy(int);status=f.status.to_numpy()
    for i in order:
        if eligible[i] and fill[i] and di[i]>=flat:
            take[i]=True;flat=np.iinfo(np.int64).max if status[i]=='censored_boundary' else xi[i]
    return take


def effective(f,eligible,taken):
    """Unknown retained only where this queue actually requires that outcome."""
    filled=f.filled.to_numpy(bool);closed=f.status.eq('closed').to_numpy()
    known_a=(~eligible | (filled & ~taken) | (taken & closed)
             | (~filled & f.status.map(known).to_numpy(bool)))
    value=np.where(taken,f.net_r.to_numpy(float),0.)
    mature=np.where(taken,epoch(f.mature_time),np.where(eligible,epoch(f.decision_time),epoch(f.anchor_time)))
    return value,known_a,mature


def accumulate_metric(bucket,net,gross,cost,exit_time,ids,keep_events=True):
    v=np.asarray(net,float);assert np.isfinite(v).all()
    bucket['n']+=len(v);bucket['sum']+=float(v.sum());bucket['gross_r']+=float(np.sum(gross));bucket['cost_r']+=float(np.sum(cost))
    bucket['wins']+=int((v>0).sum());bucket['profit']+=float(v[v>0].sum());bucket['loss']+=float(-v[v<0].sum())
    bucket['large_wins_5r']+=int((v>=5).sum());bucket['large_wins_r']+=float(v[v>=5].sum())
    if keep_events and len(v):bucket['events'].extend(zip(exit_time,ids,v))


def new_metric():
    return dict(n=0,sum=0.,gross_r=0.,cost_r=0.,wins=0,profit=0.,loss=0.,large_wins_5r=0,large_wins_r=0.,events=[])


def finish_metric(x):
    result={k:v for k,v in x.items() if k!='events'}
    result.update(mean=x['sum']/x['n'] if x['n'] else None,win_rate=x['wins']/x['n'] if x['n'] else None,
        pf=x['profit']/x['loss'] if x['loss'] else None)
    e=sorted(x['events']);v=np.array([z[2] for z in e]);equity=np.cumsum(v)
    result['realized_r_drawdown']=float((np.maximum.accumulate(np.r_[0.,equity])[1:]-equity).max()) if len(v) else None
    return result


def infer(blocks,field,cfg):
    """Weekly paired resampling and sign flips; finite products, no BLAS call."""
    a=np.array([b[field] for b in blocks.values()],float);n=np.array([b['n'] for b in blocks.values()],float)
    if len(a)<2 or not n.sum():return dict(mean95=[None,None],p=None,weeks=len(a))
    rng=np.random.default_rng(cfg['seed']);means=[]
    for _ in range(cfg['bootstrap']//100):
        j=rng.integers(0,len(a),size=(100,len(a)))
        means.extend((a[j].sum(axis=1)/n[j].sum(axis=1)).tolist())
    rng=np.random.default_rng(cfg['seed']);count=0;observed=abs(a.sum())
    for _ in range(20):
        signs=rng.integers(0,2,size=(500,len(a)),dtype=np.int8)*2-1
        simulated=(signs*a[None,:]).sum(axis=1);assert np.isfinite(simulated).all()
        count+=int((np.abs(simulated)>=observed-1e-12).sum())
    return dict(mean95=np.quantile(means,[.025,.975]).tolist(),p=(count+1)/10001,weeks=len(a))


def four_week_blocks(blocks):
    """Fixed consecutive four-week calendar blocks, retaining empty-week gaps."""
    result={}
    origin=pd.Timestamp('2022-10-31',tz='UTC')
    for week,fields in blocks.items():
        monday=pd.Timestamp(datetime.strptime(week+'-1','%G-W%V-%u'),tz='UTC')
        key=int((monday-origin).days//28)
        if key not in result:result[key]={field:0. for field in fields}
        for field,value in fields.items():result[key][field]+=value
    return result


def baseline_for(policy):
    return policy.rsplit('_',1)[0]+'_known' if policy not in ARMS else 'baseline'


def run(root,name='analysis_v1'):
    root=Path(root);cfg=json.loads(CONFIG.read_text())
    receipt=json.loads((root/'receipt.json').read_text());assert receipt['complete'] and not receipt['errors']
    identity=json.loads((root/'identity.json').read_text())
    paths=[root/'streams'/s for s in identity['symbols']]
    out=root/name;out.mkdir(exist_ok=False)
    sources=sorted(set([Path(__file__),CONFIG,*_local_transitive_python((Path(__file__),))]),key=str)
    for p in sources:subprocess.run(['git','ls-files','--error-unmatch',str(p)],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['git','diff','--exit-code','HEAD','--',*map(str,sources)],check=True)
    dump(out/'identity.json',dict(builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        code=[provenance(p) for p in sources],path_identity=provenance(root/'identity.json')))
    thresholds=cutpoints(paths,cfg);dump(out/'thresholds.json',thresholds)
    week_names=list(dict.fromkeys(pd.date_range(cfg['warmup_start'],cfg['end'],freq='D').strftime('%G-W%V')))
    full=defaultdict(new_metric);block_arrays=defaultdict(lambda:np.zeros((len(week_names),len(FIELDS)+1)))
    coverage=defaultdict(lambda:defaultdict(int));lineage=0
    start=int(pd.Timestamp(cfg['analysis_start']).timestamp());cut=int(pd.Timestamp(cfg['split']).timestamp())
    for index,path in enumerate(paths):
        for m in cfg['timeframes']:
            f=read(path/f'{m}m.csv.gz')
            if not len(f):continue
            f=f.reset_index(drop=True)
            for k in ['exit_i','net_r','gross_r','cost_r']:
                if k not in f:f[k]=np.nan
            allowed=eligibility(f,thresholds[str(m)])
            target=f.cohort.eq('signal').to_numpy();pair=f.pair_eligible.to_numpy(bool)
            et=epoch(f.entry_time);xt=epoch(f.mature_time);side_all=f.side.to_numpy(int)
            net_all=f.net_r.to_numpy(float);gross_all=f.gross_r.to_numpy(float);cost_all=f.cost_r.to_numpy(float)
            exits_all=f.exit_time.to_numpy();ids_all=f.trade_key.to_numpy();closed_all=f.status.eq('closed').to_numpy()
            flags={};flagframe=f[['trade_key','cohort']].copy()
            for field in ['eligible_bits','full_bits','pair_bits']:flagframe[field]=np.zeros(len(f),np.int64)
            for ai,policy in enumerate(POLICIES):
                e=allowed[policy]
                all_taken=serial_flags(f,e & target)
                pair_taken=serial_flags(f,e & target & pair)|serial_flags(f,e & ~target & pair)
                flags[policy]=(all_taken,pair_taken)
                flagframe['eligible_bits']+=e.astype(np.int64)*(1<<ai)
                flagframe['full_bits']+=all_taken.astype(np.int64)*(1<<ai)
                flagframe['pair_bits']+=pair_taken.astype(np.int64)*(1<<ai)
                take=all_taken & closed_all
                take &= et>=start
                side=side_all
                scopes={'all':take,'early':take&(et<cut)&(xt<cut),'later':take&(et>=cut)}
                for year in [2023,2024,2025,2026]:
                    lo=int(pd.Timestamp(f'{year}-01-01',tz='UTC').timestamp());hi=int(pd.Timestamp(f'{year+1}-01-01',tz='UTC').timestamp())
                    scopes[str(year)]=take&(et>=lo)&(et<hi)
                for tf in [m,0]:
                    for s in [0,1,-1]:
                        for period,mask in scopes.items():
                            use=mask if s==0 else mask&(side==s)
                            accumulate_metric(full[tf,s,period,policy],net_all[use],gross_all[use],cost_all[use],exits_all[use],ids_all[use],
                                              keep_events=s==0 and period in ('all','early','later'))
            # Existing complete serial baseline identity, including censoring.
            old=read(BASE/'streams'/path.name/f'{m}m.csv.gz')
            if len(old):
                old=old[old.policy.eq('rebreak') & old.cohort.eq('signal') & old.serial_all]
                now=f.loc[flags['baseline'][0]]
                assert set(now.trade_key)==set(old.trade_key),(path.name,m,'fresh occupancy baseline')
                lineage+=len(now)
            destination=out/'decisions'/path.name;destination.mkdir(parents=True,exist_ok=True)
            flagframe.to_csv(destination/f'{m}m.csv.gz',index=False)
            si=f.index[target&pair].to_numpy()
            control_index=dict(zip(f.loc[~target&pair,'trade_key'],f.index[~target&pair]))
            ci=np.array([control_index[k] for k in f.loc[si,'trade_key']],int)
            assert np.array_equal(f.loc[si,'trade_key'].to_numpy(),f.loc[ci,'trade_key'].to_numpy())
            anchor=epoch(f.anchor_time);first=np.minimum(anchor[si],anchor[ci]);side=f.side.to_numpy(int)[si]
            weeks=pd.to_datetime(f.loc[si,'anchor_time'],utc=True).dt.strftime('%G-W%V').to_numpy()
            weekcodes=pd.Categorical(weeks,categories=week_names).codes
            assert (weekcodes>=0).all()
            values={p:effective(f,allowed[p],flags[p][1]) for p in POLICIES}
            for policy in POLICIES:
                base=baseline_for(policy);v,k,mat=values[policy];b,bk,bmat=values[base]
                relevant=allowed[policy][si]|allowed[policy][ci]|allowed[base][si]|allowed[base][ci]
                valid=relevant & k[si]&k[ci]&bk[si]&bk[ci] & (first>=start)
                last=np.maximum.reduce([mat[si],mat[ci],bmat[si],bmat[ci]])
                scopes={'all':valid,'early':valid&(last<cut),'later':valid&(first>=cut)}
                data=dict(target=v[si],random=v[ci],baseline_target=b[si],baseline_random=b[ci],
                    delta=v[si]-b[si],excess=v[si]-v[ci],incremental_excess=(v[si]-v[ci])-(b[si]-b[ci]))
                for tf in [m,0]:
                    cov=coverage[tf,policy];cov['relevant_available']+=int((relevant&(first>=start)).sum());cov['mature']+=int(valid.sum())
                for s in [0,1,-1]:
                    for period,mask in scopes.items():
                        use=mask if s==0 else mask&(side==s)
                        if not use.any():continue
                        wc=weekcodes[use]
                        sums=np.column_stack([np.bincount(wc,minlength=len(week_names)),
                            *[np.bincount(wc,weights=data[key][use],minlength=len(week_names)) for key in FIELDS]])
                        for tf in [m,0]:block_arrays[tf,s,period,policy]+=sums
        if index==0 or (index+1)%25==0 or index+1==len(paths):print(json.dumps(dict(aggregated=index+1,total=len(paths))),flush=True)
    blocks={key:{week_names[i]:dict(zip(('n',*FIELDS),row.tolist())) for i,row in enumerate(a) if row[0]>0}
            for key,a in block_arrays.items()}
    serial=[];paired=[]
    for (m,s,period,policy),v in full.items():serial.append(dict(timeframe_min=m,side=s,period=period,policy=policy)|finish_metric(v))
    for (m,s,period,policy),b in blocks.items():
        n=sum(x['n'] for x in b.values());sums={key:sum(x[key] for x in b.values()) for key in FIELDS}
        row=dict(timeframe_min=m,side=s,period=period,policy=policy,baseline=baseline_for(policy),n=int(n),sums=sums,
            means={key:value/n for key,value in sums.items()})
        if s==0:
            for field in ['excess','incremental_excess']:row[field+'_inference']=infer(b,field,cfg)
            row['incremental_excess_4week_inference']=infer(four_week_blocks(b),'incremental_excess',cfg)
        paired.append(row)
    for family in ['rules','states']:
        candidates=[r for r in paired if r['side']==0 and r['period']=='later' and r['timeframe_min']!=0
            and ((r['policy'] in ARMS[1:] and (r['policy']!='no_h1' or r['timeframe_min']==15)) if family=='rules'
                 else (r['policy'] not in ARMS and not r['policy'].endswith('_known')))]
        ranked=sorted(candidates,key=lambda r:r['incremental_excess_inference']['p'] if r['incremental_excess_inference']['p'] is not None else 1.)
        previous=0.
        for i,row in enumerate(ranked):
            p=row['incremental_excess_inference']['p'];previous=max(previous,min(1.,(1. if p is None else p)*(len(ranked)-i)))
            row['holm_family']=family;row['holm_family_n']=len(ranked);row['holm_p']=previous
    selection=[]
    lookup={(r['timeframe_min'],r['side'],r['period'],r['policy']):r for r in paired}
    trades={(r['timeframe_min'],r['side'],r['period'],r['policy']):r for r in serial}
    for m in cfg['timeframes']:
        for feature in FEATURES:
            chosen=feature+'_known';best=0.;options=[]
            for band in ['low','mid','high']:
                p=feature+'_'+band;early=lookup.get((m,0,'early',p));n=trades.get((m,0,'early',p),{}).get('n',0)
                score=early['sums']['incremental_excess'] if early else None
                options.append(dict(policy=p,early_closed=n,early_incremental_excess=score))
                if n>=cfg['feature_min_early_closed'] and score is not None and score>best:chosen=p;best=score
            selection.append(dict(timeframe_min=m,feature=feature,chosen=chosen,early_options=options,
                early_score=best,later_paired=lookup.get((m,0,'later',chosen)),later_serial=trades.get((m,0,'later',chosen)),
                later_baseline_serial=trades.get((m,0,'later',feature+'_known'))))
    dump(out/'weekly_blocks.json',[dict(timeframe_min=k[0],side=k[1],period=k[2],policy=k[3],blocks=v) for k,v in blocks.items()])
    result=dict(generated_at=datetime.now(timezone.utc).isoformat(),config=cfg,serial=serial,paired=paired,selection=selection,
        coverage=[dict(timeframe_min=k[0],policy=k[1],**v) for k,v in coverage.items()],thresholds=thresholds,
        baseline_serial_identity_checks=lineage,path_audits={key:sum(s[key] for r in receipt['receipts'] for s in r['streams']) for key in ['baseline_scalar_checks','draw_identity_checks','prefix_checks']},
        definitions={'baseline':'Original rebreak/fresh occupancy; fixed original raw opposite exits in all ablations.',
            'simple_breakout':'Minimal12-bar anchor plus SAME rebreak wrapper; combined benchmark, not a single-rule removal.',
            'state':'Each cohort uses its own anchor feature and pre2025 signal-derived cutpoints. Unknown excluded explicitly.',
            'paired':'Common mature variant/baseline outcomes on available matched opportunities; no-fill/skipped/filtered zero, required unknown excluded.',
            'R':'Equal-risk arithmetic; realized closedR drawdown is not account or intratrade drawdown.',
            'statistics':'Weekly clustered exploratory historical estimates; rule/state Holm families separate, no forward claim.',
            'AUC_top_decile':'Not applicable to these fixed categorical entry rules: no classifier, fitted ranking or top-decile policy.'},
        limitations=['638 archived Binance identities and ticks are not point-in-time.','No funding, additional slippage, queue or shared-capital model.',
            'Stateful removals can replace anchors; this is an entry-only ablation, not a complete changed exit detector.',
            'Known/mature pair coverage differs across comparisons; never compare a full signal sum with its paired random sum.',
            'History reused; pre2025 selection/post2025 evaluation is chronological diagnosis, not unseen validation.'],
        training_eligible=False,production_eligible=False)
    dump(out/'result.json',result)
    print(json.dumps(dict(output=str(out),serial_identity=lineage,policies=len(POLICIES))),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--name',default='analysis_v1')
    a=p.parse_args();run(a.root,a.name)

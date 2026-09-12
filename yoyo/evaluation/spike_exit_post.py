"""Streamed capital/exit summaries for the prespecified SPIKE policy research.

Reads authenticated simulation fills and frozen source closes. Development
selection is based only on calendar development account wealth; validation
results are reported without retroactively changing selected policies.
"""
from __future__ import annotations
import argparse,hashlib,json,time
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_exit_accounts import marked_account_grid
from yoyo.evaluation.spike_exit_policy_study import COHORTS,POLICIES,load_verified_stream

EXP=Path('experiments/active/exp-spike-exit-policy-20260912-v1')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def event_stats(part):
    closed=part.loc[~part.censored.astype(bool)] if len(part) else part
    ret=closed.net_return.to_numpy(float);r=closed.net_r.to_numpy(float)
    pos=float(ret[ret>0].sum());neg=float(-ret[ret<0].sum())
    return dict(entries=len(part),closed=len(closed),censored=len(part)-len(closed),wins=int((ret>0).sum()),positive_return_sum=pos,negative_return_sum=neg,net_r_sum=float(r.sum()),net_ge_10r=int((r>=10).sum()),mfe_ge_10r=int((closed.mfe_r>=10).sum()) if len(closed) else 0)

def describe_grid(ix,nav,meta):
    rows=[]
    periods=[('full','2024-09-10T00:00:00Z','2026-09-10T00:00:00Z'),('development','2024-09-10T00:00:00Z','2025-09-10T00:00:00Z'),('validation','2025-09-10T00:00:00Z','2026-09-10T00:00:00Z')]
    for label,a,b in periods:
        a,b=pd.Timestamp(a),pd.Timestamp(b)
        before=np.flatnonzero(ix<=a);opening=nav[before[-1],:] if len(before) else np.full(len(meta),10000.)
        vals=np.vstack([opening,nav[(ix>a)&(ix<=b),:]])
        valid=np.isfinite(vals).all(axis=0)&(opening>0)
        peaks=np.maximum.accumulate(vals,axis=0)
        dd=1-np.divide(vals,peaks,out=np.ones_like(vals),where=peaks>0)
        mdd=np.max(dd,axis=0)
        for j,m in enumerate(meta):
            rows.append({**m,'period':label,'valid':bool(valid[j]),'opening_equity':float(opening[j]),'ending_equity':float(vals[-1,j]),'net_return':float(vals[-1,j]/opening[j]-1) if valid[j] else np.nan,'max_close_drawdown':float(mdd[j]) if valid[j] else np.nan})
    return rows

def process_stream(folder,engine,output):
    key=folder.name;receipt_path=engine/'streams'/f'{key}.completion.json';receipt=json.loads(receipt_path.read_text())
    for name,digest in receipt['output_sha256'].items():
        if sha(engine/'streams'/name)!=digest:raise ValueError('engine file changed: '+name)
    c=load_verified_stream(folder)
    trades=pd.read_csv(engine/'streams'/f'{key}.trades.csv.gz')
    fills=pd.read_csv(engine/'streams'/f'{key}.fills.csv.gz')
    if len(trades):trades['entry_time']=pd.to_datetime(trades.entry_time,utc=True)
    accounts=[];events=[]
    for cohort in COHORTS:
        for policy in POLICIES:
            part=trades.loc[trades.cohort.eq(cohort)&trades.policy.eq(policy)]
            f=fills.loc[fills.cohort.eq(cohort)&fills.policy.eq(policy)]
            ident=dict(stream_key=key,**c.identity,cohort=cohort,policy=policy)
            ix,nav,meta=marked_account_grid(c.cache['bars'],part,f,minutes=c.minutes)
            accounts.extend({**ident,**row} for row in describe_grid(ix,nav,meta))
            for period,a,b in [('full','2024-09-10T00:00:00Z','2026-09-10T00:00:00Z'),('development','2024-09-10T00:00:00Z','2025-09-10T00:00:00Z'),('validation','2025-09-10T00:00:00Z','2026-09-10T00:00:00Z')]:
                selected=part.loc[part.entry_time.ge(pd.Timestamp(a))&part.entry_time.lt(pd.Timestamp(b))] if len(part) else part
                events.append({**ident,'period':period,**event_stats(selected)})
    files={}
    for suffix,rows in [('accounts',accounts),('events',events)]:
        p=output/'streams'/f'{key}.{suffix}.csv.gz';temp=p.with_suffix('.tmp')
        pd.DataFrame(rows).to_csv(temp,index=False,compression={'method':'gzip','compresslevel':1,'mtime':0});temp.replace(p);files[p.name]=sha(p)
    (output/'streams'/f'{key}.json').write_text(json.dumps({'engine_receipt_sha256':sha(receipt_path),'files':files},indent=2))


def aggregate(output,expected):
    paths=sorted((output/'streams').glob('*.json'))
    if len(paths)!=expected:raise ValueError('aggregation requires complete configured scope')
    pieces=[];ep=[]
    for p in paths:
        record=json.loads(p.read_text())
        for name,digest in record['files'].items():
            file=output/'streams'/name
            if sha(file)!=digest:raise ValueError('post stream changed')
            df=pd.read_csv(file)
            (pieces if '.accounts.' in name else ep).append(df)
    account=pd.concat(pieces,ignore_index=True);del pieces
    event=pd.concat(ep,ignore_index=True);del ep
    keys=['timeframe_min','cohort','policy','risk_fraction','notional_cap','period']
    rows=[]
    for scope,part in [('all',account),*[(v,x) for v,x in account.groupby('venue',sort=False)]]:
        for key,g in part.groupby(keys,sort=False,dropna=False):
            good=g.loc[g.valid.astype(bool)]
            row=dict(zip(keys,key));row['venue_scope']=scope
            row.update(streams=len(g),valid_accounts=len(good),invalid_accounts=len(g)-len(good),ruined_accounts=int(g.ruined.sum()),zero_entry_accounts=int(g.allocated_trades.eq(0).sum()))
            for column,prefix in [('net_return','return'),('max_close_drawdown','drawdown')]:
                values=good[column].to_numpy(float)
                row.update({prefix+'_mean':float(values.mean()) if len(values) else np.nan,prefix+'_median':float(np.median(values)) if len(values) else np.nan,prefix+'_p10':float(np.quantile(values,.1)) if len(values) else np.nan,prefix+'_p90':float(np.quantile(values,.9)) if len(values) else np.nan,prefix+'_max':float(values.max()) if len(values) else np.nan})
            row.update(effective_risk_mean=float(g.mean_effective_stop_risk.mean()),max_observed_close_leverage=float(g.max_observed_close_leverage.max()))
            rows.append(row)
    summary=pd.DataFrame(rows);summary.to_csv(output/'account_summary.csv',index=False)
    ekeys=['venue','timeframe_min','cohort','policy','period']
    numeric=['entries','closed','censored','wins','positive_return_sum','negative_return_sum','net_r_sum','net_ge_10r','mfe_ge_10r']
    e=event.groupby(ekeys,as_index=False)[numeric].sum()
    all_e=event.groupby(ekeys[1:],as_index=False)[numeric].sum();all_e['venue']='all'
    e=pd.concat([e,all_e],ignore_index=True)
    e['win_rate']=e.wins/e.closed.replace(0,np.nan);e['event_pf']=e.positive_return_sum/e.negative_return_sum.replace(0,np.nan);e['mean_net_r']=e.net_r_sum/e.closed.replace(0,np.nan)
    e.to_csv(output/'event_summary.csv',index=False)
    # Freeze the development decision before joining any validation rows.
    dev=summary.loc[summary.venue_scope.eq('all')&summary.period.eq('development')&summary.risk_fraction.eq(.01)&summary.notional_cap.astype(str).eq('1.0')].copy()
    dev['policy_order']=dev.policy.map({p:i for i,p in enumerate(POLICIES)})
    selection=dev.sort_values(['return_mean','drawdown_mean','policy_order'],ascending=[False,True,True]).groupby(['timeframe_min','cohort'],sort=False).head(1)
    selection=selection[['timeframe_min','cohort','policy','return_mean','drawdown_mean','valid_accounts','invalid_accounts']]
    selection.to_csv(output/'development_selection.csv',index=False)
    joined=summary.merge(selection[['timeframe_min','cohort','policy']],on=['timeframe_min','cohort','policy'],how='inner')
    joined.to_csv(output/'selected_policy_capital.csv',index=False)
    # Paired policy-minus-baseline independent account outcomes, same stream.
    pairing=account.loc[account.risk_fraction.eq(.01)&account.notional_cap.astype(str).eq('1.0')&account.valid]
    base=pairing.loc[pairing.policy.eq('baseline'),['stream_key','cohort','period','net_return']].rename(columns={'net_return':'baseline_return'})
    pair=pairing.merge(base,on=['stream_key','cohort','period']);pair['paired_return_difference']=pair.net_return-pair.baseline_return
    paired=pair.groupby(['timeframe_min','cohort','policy','period']).paired_return_difference.agg(['count','mean','median']).reset_index()
    paired.to_csv(output/'paired_exit_effect.csv',index=False)
    # Preserve full source distributions as a local compressed artifact.
    account.to_csv(output/'independent_account_rows.csv.gz',index=False,compression={'method':'gzip','compresslevel':1,'mtime':0})
    event.to_csv(output/'stream_event_rows.csv.gz',index=False,compression={'method':'gzip','compresslevel':1,'mtime':0})
    return len(account),len(event)


def run(engine,output,follow=False,limit=None):
    config=json.loads((EXP/'config.json').read_text());raw=Path(config['raw'])
    identity={'post_sha256':sha(__file__),'accounts_sha256':sha(Path(__file__).with_name('spike_exit_accounts.py')),'config_sha256':sha(EXP/'config.json'),'engine_identity_sha256':sha(engine/'engine_identity.json')}
    output.mkdir(parents=True,exist_ok=True);(output/'streams').mkdir(exist_ok=True)
    ip=output/'post_identity.json'
    if ip.exists() and json.loads(ip.read_text())!=identity:raise ValueError('post identity changed')
    ip.write_text(json.dumps(identity,indent=2))
    processed={p.stem for p in (output/'streams').glob('*.json')};last=time.monotonic()
    while len(processed)<(limit or config['expected_streams']):
        available=sorted(p.name.removesuffix('.completion.json') for p in (engine/'streams').glob('*.completion.json'))
        new=[key for key in available if key not in processed]
        if limit:new=new[:max(0,limit-len(processed))]
        if not new:
            if not follow:break
            if time.monotonic()-last>600:raise RuntimeError('no new engine stream for10min')
            time.sleep(10);continue
        for key in new:
            process_stream(raw/'streams'/key,engine,output);processed.add(key);last=time.monotonic()
            if len(processed)%25==0:print(json.dumps({'post_streams':len(processed),'expected':config['expected_streams']}),flush=True)
    n=ne=0
    if len(processed)==config['expected_streams']:n,ne=aggregate(output,config['expected_streams'])
    outputs={p.name:sha(p) for p in output.iterdir() if p.is_file() and p.name!='post_manifest.json'}
    (output/'post_manifest.json').write_text(json.dumps({**identity,'complete':len(processed)==config['expected_streams'],'streams':len(processed),'account_rows':n,'event_rows':ne,'outputs':outputs},indent=2))
    print(json.dumps({'post_finished':len(processed),'complete':len(processed)==config['expected_streams']}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--engine',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--follow',action='store_true');p.add_argument('--limit',type=int)
    a=p.parse_args();run(a.engine,a.output,a.follow,a.limit)

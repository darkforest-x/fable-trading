"""Frozen factorial summaries and chronological selection for Task62 V4.

Only causal K windows, original side/confirmation/structure and current entry
prices determine admissions. Exit labels come from the audited native ledger.
Every entry configuration gets the same gated random null, shared by all26
exits. The grid and minimum development sample were fixed before its results.
"""
import argparse
import itertools
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.eth_stoch_confirm_exits import weekly_test
from yoyo.evaluation.eth_stoch_extreme_stop import (
    EXP, FIELDS, REASONS, filter_mask, stops, matched_indices,
)


def entry_configs(cfg):
    filters=[dict(lower=0.,upper=100.,lookback=-1)]
    filters += [dict(upper=u,lower=l,lookback=lb) for u,l,lb in
                itertools.product(cfg['upper_values'],cfg['lower_values'],cfg['lookbacks'])]
    return [dict(config_id=f'e{i:04d}',buffer=b,cap=c,**f) for i,(b,c,f) in enumerate(
        itertools.product(cfg['stop_buffers'],cfg['risk_caps'],filters))]


def load_arrays(paths,buffer,events,cost):
    raw={k:np.load(paths/f'buffer_{buffer:g}'/f'{k}.npy',mmap_mode='r') for k in FIELDS}
    stop,risk=stops(events,buffer)
    gross=events.side.to_numpy()[:,None]*(raw['exit_price']-events.entry.to_numpy()[:,None])
    net=gross-events.entry.to_numpy()[:,None]*cost
    closed=(raw['reason']>0)&(raw['reason']<255)
    return dict(raw=raw,risk=risk,stop=stop,gross=gross,net=net,
                net_r=np.divide(net,risk[:,None],out=np.full_like(net,np.nan),where=risk[:,None]>0),
                closed=closed,target=(raw['reason']==3)|(raw['reason']==4),
                protected=(raw['be_activated']>0)&((raw['reason']==1)|(raw['reason']==2)))


def safe_rate(a,b):
    return np.divide(a,b,out=np.full_like(a,np.nan,dtype=float),where=b>0)


def metrics(a,idx):
    closed=a['closed'][idx]; net=a['net'][idx]; target=a['target'][idx]
    n=closed.sum(axis=0); wins=(closed&(net>1e-8)).sum(axis=0)
    hit=(closed&target).sum(axis=0)
    return dict(orders=np.full_like(n,len(idx)),closed=n,censored=len(idx)-n,
        wins=wins,win_rate=safe_rate(wins,n),target_hits=hit,target_rate=safe_rate(hit,n),
        protected_net_wins=(closed&a['protected'][idx]&(net>1e-8)).sum(axis=0),
        gross_points=np.where(closed,a['gross'][idx],0).sum(axis=0),
        net_points=np.where(closed,net,0).sum(axis=0),
        net_r=np.where(closed,a['net_r'][idx],0).sum(axis=0),
        mean_net_points=safe_rate(np.where(closed,net,0).sum(axis=0),n),
        censored_mtm_points=np.where(~closed,net,0).sum(axis=0))


def paired_metrics(a,left,right):
    closed=a['closed'][left]&a['closed'][right]
    n=closed.sum(axis=0)
    x=np.where(closed,a['net'][left],0);y=np.where(closed,a['net'][right],0)
    return dict(paired_n=n,paired_signal_net_points=x.sum(axis=0),
        paired_random_net_points=y.sum(axis=0),paired_excess_points=(x-y).sum(axis=0),
        paired_signal_win_rate=safe_rate((closed&(a['net'][left]>1e-8)).sum(axis=0),n),
        paired_random_win_rate=safe_rate((closed&(a['net'][right]>1e-8)).sum(axis=0),n),
        paired_random_target_hits=(closed&a['target'][right]).sum(axis=0),
        paired_random_net_r=np.where(closed,a['net_r'][right],0).sum(axis=0))


def indices(events,conf,seed):
    _,risk=stops(events,conf['buffer'])
    gate=filter_mask(events,conf['lower'],conf['upper'],conf['lookback'])
    gate &= (risk>0)&(risk<=conf['cap']+1e-8)
    return matched_indices(events,gate,seed)


def summarize(events,paths,cfg,configs,policies,out):
    records=[]; admissions=[]
    segments=events.segment.to_numpy()
    for buffer in cfg['stop_buffers']:
        a=load_arrays(paths,buffer,events,cfg['round_trip_cost'])
        for conf in [c for c in configs if c['buffer']==buffer]:
            sig,left,right,missing=indices(events,conf,cfg['control_seed'])
            admissions.append(dict(**conf,signals=len(sig),controls=len(right),unmatched=len(missing)))
            for segment in ('all','development','validation','examples'):
                s=sig if segment=='all' else sig[segments[sig]==segment]
                keep=np.ones(len(left),dtype=bool) if segment=='all' else segments[left]==segment
                l,r=left[keep],right[keep]
                columns=dict(**metrics(a,s),**paired_metrics(a,l,r))
                median_risk=float(np.median(a['risk'][s])) if len(s) else None
                median_fee_r=float(np.median(events.entry.to_numpy()[s]*cfg['round_trip_cost']/a['risk'][s])) if len(s) else None
                for j,p in enumerate(policies):
                    records.append(dict(**conf,policy=p['name'],segment=segment,median_risk=median_risk,
                        median_fee_r=median_fee_r,unmatched_controls=len(s)-len(l),
                        **{key:values[j] for key,values in columns.items()}))
        print('summary buffer',buffer,'rows',len(records),flush=True)
        del a
    summary=pd.DataFrame(records)
    summary.to_csv(out/'grid_summary.csv.gz',index=False)
    pd.DataFrame(admissions).to_csv(out/'admissions.csv',index=False)
    return summary


def pick(summary,out):
    chosen=[]; ranks=[]
    for segment in ('development','all'):
        pool=summary[(summary.segment==segment)&(summary.closed>=100)]
        for metric in ('net_points','net_r','win_rate','target_rate','target_hits'):
            z=pool.sort_values([metric,'closed','config_id','policy'],ascending=[False,False,True,True])
            chosen.append(dict(selection_period=segment,selection_metric=metric,**z.iloc[0].to_dict()))
            ranks.append(z.head(20).assign(rank_metric=metric,rank_segment=segment))
    pd.concat(ranks).to_csv(out/'top20_descriptive.csv',index=False)
    selection=pd.DataFrame(chosen)
    selection.to_csv(out/'selection.csv',index=False)
    return selection


def scalar_summary_audit(events,a,sig,left,right,rows,policies):
    """Independent scalar reducer for every policy/segment of selected gates."""
    checks=0
    for row in rows.itertuples():
        j=next(i for i,p in enumerate(policies) if p['name']==row.policy)
        wins=targets=closed=0;net=0.
        for i in sig:
            if row.segment!='all' and events.iloc[i].segment!=row.segment: continue
            if 0<int(a['raw']['reason'][i,j])<255:
                closed+=1
                value=int(events.iloc[i].side)*(float(a['raw']['exit_price'][i,j])-float(events.iloc[i].entry))-.002*float(events.iloc[i].entry)
                wins+=value>1e-8;net+=value
                targets+=int(a['raw']['reason'][i,j]) in (3,4)
        assert closed==row.closed and wins==row.wins and targets==row.target_hits
        assert abs(net-row.net_points)<1e-7,(row.config_id,row.policy,row.segment,net,row.net_points)
        excess=0.;pairs=0
        for l,r in zip(left,right):
            if row.segment!='all' and events.iloc[l].segment!=row.segment: continue
            if 0<int(a['raw']['reason'][l,j])<255 and 0<int(a['raw']['reason'][r,j])<255:
                pairs+=1
                x=int(events.iloc[l].side)*(float(a['raw']['exit_price'][l,j])-float(events.iloc[l].entry))-.002*float(events.iloc[l].entry)
                y=int(events.iloc[r].side)*(float(a['raw']['exit_price'][r,j])-float(events.iloc[r].entry))-.002*float(events.iloc[r].entry)
                excess+=x-y
        assert pairs==row.paired_n and abs(excess-row.paired_excess_points)<1e-7
        checks+=1
    return checks


def diagnose(events,paths,cfg,configs,policies,summary,selected,out):
    owner=next(c for c in configs if all(c[k]==v for k,v in cfg['owner_rule'].items()))
    base=next(c for c in configs if c['buffer']==1 and c['cap']==8 and c['lookback']==-1)
    # The exact owner filter changes only eligibility relative to the full V3
    # baseline. Both remain present for every exit, including failures.
    baseline=summary[summary.config_id.eq(base['config_id'])]
    exact=summary[summary.config_id.eq(owner['config_id'])]
    compare=exact.merge(baseline,on=['policy','segment'],suffixes=('_owner','_base'),validate='one_to_one')
    compare['delta_net_points']=compare.net_points_owner-compare.net_points_base
    compare.to_csv(out/'owner_exact_vs_original.csv',index=False)
    # Save one-variable slices around the owner proposal for attribution.
    c=summary
    fixed_k=c.upper.eq(96.19)&c.lower.eq(4)&c.lookback.eq(3)
    fixed_stop=c.buffer.eq(1)&c.cap.eq(8)
    ablation=c[(fixed_k&(c.buffer.eq(1)|c.cap.eq(8))) | (fixed_stop &
        ((c.lower.eq(4)&c.lookback.eq(3))|(c.upper.eq(96.19)&c.lookback.eq(3))|
         (c.upper.eq(96.19)&c.lower.eq(4))|c.lookback.eq(-1)))]
    ablation.to_csv(out/'one_variable_slices.csv',index=False)
    keys=set(zip(selected[selected.selection_period.eq('development')].config_id,
                 selected[selected.selection_period.eq('development')].policy))
    keys.add((owner['config_id'],'tp20_price_5r'))
    # Full-history champions are retained for diagnosis, but not added to the
    # chronological candidate hypothesis family.
    audit_keys=keys | set(zip(selected.config_id,selected.policy)) | {(base['config_id'],'tp20_price_5r')}
    details=[];yearly=[];tests=[];audits=0
    years=pd.to_datetime(events.entry_time,utc=True).dt.tz_convert('Asia/Shanghai').dt.year.to_numpy()
    base_arrays=load_arrays(paths,1,events,.002)
    base_sig,_,_,_=indices(events,base,cfg['control_seed'])
    for cid in sorted({x[0] for x in audit_keys}):
        conf=next(c for c in configs if c['config_id']==cid)
        a=load_arrays(paths,conf['buffer'],events,.002)
        sig,left,right,missing=indices(events,conf,cfg['control_seed'])
        names=sorted(p for k,p in audit_keys if k==cid)
        rows=summary[summary.config_id.eq(cid)&summary.policy.isin(names)]
        audits+=scalar_summary_audit(events,a,sig,left,right,rows,policies)
        for name in names:
            j=next(i for i,p in enumerate(policies) if p['name']==name)
            for year in np.unique(years):
                ss=sig[years[sig]==year]
                keep=years[left]==year
                cols=dict(**metrics(a,ss),**paired_metrics(a,left[keep],right[keep]))
                yearly.append(dict(config_id=cid,policy=name,year=int(year),**{k:v[j] for k,v in cols.items()}))
            for role,idx in [('signal',sig),('random',right)]:
                for i in idx:
                    details.append(dict(config_id=cid,policy=name,population=role,anchor_i=int(events.iloc[i].anchor_i),
                        entry_time=events.iloc[i].entry_time,side=int(events.iloc[i].side),entry=float(events.iloc[i].entry),
                        stop=float(a['stop'][i]),risk=float(a['risk'][i]),segment=events.iloc[i].segment,
                        exit_i=int(a['raw']['exit_i'][i,j]),exit_price=float(a['raw']['exit_price'][i,j]),
                        reason=REASONS[int(a['raw']['reason'][i,j])],net_points=float(a['net'][i,j]),
                        net_r=float(a['net_r'][i,j]),target_hit=bool(a['target'][i,j]),
                        be_activated=bool(a['raw']['be_activated'][i,j])))
            if (cid,name) in keys:
                for segment in ('development','validation','all'):
                    keep=(a['closed'][left,j]&a['closed'][right,j])
                    if segment!='all': keep &= events.segment.to_numpy()[left]==segment
                    l,r=left[keep],right[keep]
                    v=pd.Series(a['net'][l,j]-a['net'][r,j]).groupby(events.week.to_numpy()[l]).sum()
                    test=weekly_test(v)
                    tests.append(dict(config_id=cid,policy=name,segment=segment,comparison='matched_random',pairs=len(l),
                        delta_points=float(v.sum()),**test))
                    # Difference of complete independent opportunity totals by
                    # week; skipped signals contribute zero, not fictitious wins.
                    s=sig[a['closed'][sig,j]];b=base_sig[base_arrays['closed'][base_sig,j]]
                    if segment!='all':
                        s=s[events.segment.to_numpy()[s]==segment];b=b[events.segment.to_numpy()[b]==segment]
                    x=pd.Series(a['net'][s,j]).groupby(events.week.to_numpy()[s]).sum()
                    y=pd.Series(base_arrays['net'][b,j]).groupby(events.week.to_numpy()[b]).sum()
                    v=x.subtract(y,fill_value=0)
                    tests.append(dict(config_id=cid,policy=name,segment=segment,comparison='original_policy_total',pairs=None,
                        delta_points=float(v.sum()),**weekly_test(v)))
        del a
    tests=pd.DataFrame(tests)
    for seg,part in tests.groupby('segment'):
        previous=0.
        ordered=part.dropna(subset=['p']).sort_values('p')
        for rank,(i,r) in enumerate(ordered.iterrows()):
            previous=max(previous,min(1.,r.p*(len(ordered)-rank)))
            tests.loc[i,'holm_p']=previous
    tests.to_csv(out/'selected_inference.csv',index=False)
    pd.DataFrame(details).to_csv(out/'selected_trades.csv.gz',index=False)
    pd.DataFrame(yearly).to_csv(out/'selected_years.csv',index=False)
    # Original unfiltered signal, random and all split totals must reproduce V3.
    prior=pd.read_csv(Path('experiments/active/exp-eth5m-stoch-target-be-20261019-v3/results/summary.csv'))
    joined=baseline.merge(prior,on=['policy','segment'],suffixes=('','_prior'),validate='one_to_one')
    for col in ['orders','closed','target_hits','paired_n']:
        assert np.array_equal(joined[col],joined[f'{col}_prior']),col
    for col in ['net_points','net_r','paired_signal_net_points','paired_random_net_points']:
        assert np.allclose(joined[col],joined[f'{col}_prior'],rtol=0,atol=1e-7),col
    return dict(owner_config=owner,baseline_config=base,independent_scalar_summary_groups=audits,
                baseline_v3_summary_groups=len(joined),hypotheses=len(tests),selected_rows=len(details))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--paths',default='paths_v1');ap.add_argument('--out',default='analysis_v1')
    ap.add_argument('--diagnose-only',action='store_true')
    args=ap.parse_args();out=EXP/args.out;paths=EXP/args.paths
    if not args.diagnose_only: out.mkdir(exist_ok=False)
    cfg=json.loads((EXP/'config.json').read_text())
    events=pd.read_csv(paths/'events.csv.gz');policies=json.loads((paths/'policies.json').read_text())
    configs=entry_configs(cfg)
    if args.diagnose_only:
        summary=pd.read_csv(out/'grid_summary.csv.gz');selected=pd.read_csv(out/'selection.csv')
    else:
        summary=summarize(events,paths,cfg,configs,policies,out)
        selected=pick(summary,out)
    facts=diagnose(events,paths,cfg,configs,policies,summary,selected,out)
    facts.update(analysis_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        entry_configs=len(configs),exit_policies=len(policies),grid_arms=len(configs)*len(policies),
        positive_arms_by_segment=summary.groupby('segment').net_points.apply(lambda s:int(s.gt(1e-8).sum())).to_dict(),
        training_eligible=False,production_eligible=False,
        caveats=['All data previously exposed; chronological selection is not forward validation.',
            'Full-history leaders are descriptive and chosen among31512arms.',
            'Equal-risk and fixed1ETH profits differ; tighter stops increase feeR and change the point value of10R20R.',
            'Random draws share symbols,side,week,volatility and exact causal admission filters; replacement allowed.',
            'No classifier:AUC/top-decile scores undefined. Economic null uses matched random,weekly blocks and selected-family Holm.'])
    (out/'receipt.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n')
    print(selected[['selection_period','selection_metric','config_id','upper','lower','lookback','buffer','cap','policy','closed','win_rate','target_hits','net_points','net_r']].to_string(index=False))
    print(json.dumps(facts,ensure_ascii=False),flush=True)


if __name__=='__main__':main()

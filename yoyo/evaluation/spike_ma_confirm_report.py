"""Predeclared exit ablation tables, reference-risk tails and block evidence.

No training or parameter selection. Trade cohorts use signal-close time;
earlier exits/controls crossing the split are excluded. All symbols share
monthly blocks; structural stops do not promise a bounded initial-R loss.
"""
from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_ma_confirm_study as study
from yoyo.evaluation import spike_ma_stop_report as prior
from yoyo.evaluation import spike_v10_4_study as source


def uncertainty(a,b,col):
    """Joint UTC-month bootstrap and exact small-sample sign permutation.

    Exact monthly sign flips expose attainable p resolution; no synthetic
    replication of months can manufacture additional independent evidence.
    """
    months=sorted(set(a.month)|set(b.month));n=len(months)
    if n<2 or not len(a) or not len(b):
        return {'difference':np.nan,'ci_low':np.nan,'ci_high':np.nan,'p':np.nan,'months':n,
                'p_resolution':np.nan,'permutation_method':'unavailable'}
    def blocks(t):
        return t.groupby('month')[col].agg(['sum','count']).reindex(months,fill_value=0).to_numpy(float)
    x,y=blocks(a),blocks(b);rng=np.random.default_rng(study.SEED)
    draw=rng.integers(0,n,size=(study.REPS,n));xx,yy=x[draw].sum(axis=1),y[draw].sum(axis=1)
    valid=(xx[:,1]>0)&(yy[:,1]>0);boot=xx[valid,0]/xx[valid,1]-yy[valid,0]/yy[valid,1]
    contributions=x[:,0]/x[:,1].sum()-y[:,0]/y[:,1].sum()
    effect=float(contributions.sum())
    if n<=16:
        bits=(np.arange(1<<n,dtype=np.uint64)[:,None]>>np.arange(n,dtype=np.uint64))&1
        signs=bits.astype(float)*2-1;perm=(signs*contributions).sum(axis=1)
        # Include the identity pattern even with floating-point summation noise.
        pvalue=float(np.mean(perm>=effect-1e-15));resolution=1/len(perm);method='exact_month_signs'
    else:
        signs=rng.choice([-1.,1.],size=(study.REPS,n));perm=(signs*contributions).sum(axis=1)
        pvalue=(1+int((perm>=effect-1e-15).sum()))/(study.REPS+1)
        resolution=1/(study.REPS+1);method='monte_carlo_month_signs'
    return {'difference':effect,'ci_low':np.quantile(boot,.025),'ci_high':np.quantile(boot,.975),
            'p':pvalue,'months':n,'p_resolution':resolution,'permutation_method':method}


def stats(t):
    r=prior.stats(t);net=t.net_r.to_numpy(float);gross=t.gross_r.to_numpy(float)
    q=np.quantile(net,.05) if len(net) else np.nan
    holding=(pd.to_datetime(t.exit_time,utc=True)-pd.to_datetime(t.entry_time,utc=True)).dt.total_seconds()/3600
    return r|{'worst_net_r':np.min(net) if len(net) else np.nan,
        'worst_gross_r':np.min(gross) if len(gross) else np.nan,
        'net_r_q05':q,'net_r_cvar05':np.mean(net[net<=q]) if len(net) else np.nan,
        'gross_loss_beyond_1r':int((gross < -1.-1e-8).sum()),'net_loss_beyond_2r':int((net < -2).sum()),
        'net_loss_beyond_2r_pct':100*np.mean(net < -2) if len(net) else np.nan,
        'mean_holding_hours':holding.mean(),'tp2_exits':int(t.exit_reason.astype(str).str.startswith('tp2').sum()),
        'ma_confirm_exits':int(t.exit_reason.astype(str).str.startswith('ma_').sum())}


def report(run):
    done=json.loads((run/'completion.json').read_text())
    for name,digest in done['files'].items(): assert source.digest(run/name)==digest
    t=pd.read_csv(run/'trades.csv.gz');c=pd.read_csv(run/'controls.csv.gz');f=pd.read_csv(run/'fixed.csv.gz')
    t=t.merge(c,on=['event_key','arm'],validate='one_to_one')
    t['month']=(pd.to_datetime(t.signal_bar_open,utc=True)+pd.Timedelta(minutes=15)).dt.strftime('%Y-%m')
    summary=[];effects=[];attribution=[];reasons=[]
    for side in ('long','short','all'):
        for period in ('full','earlier','later'):
            sample=prior.subset(t,period,side);original=sample.loc[sample.arm.eq(study.BASELINE)]
            for arm in study.ARMS:
                a=sample.loc[sample.arm.eq(arm)];paired=a.loc[a.matched].copy()
                if period=='earlier': paired=paired.loc[pd.to_datetime(paired.control_exit_time,utc=True)<source.SPLIT]
                summary.append({'side':side,'period':period,'arm':arm,**stats(a),'matched':len(paired),
                  'coverage_pct':100*len(paired)/len(a) if len(a) else np.nan,
                  'paired_actual_r':paired.net_r.mean(),'random_r':paired.control_net_r.mean(),
                  'excess_r':(paired.net_r-paired.control_net_r).mean(),'random_bp':paired.control_net_return.mean()*1e4,
                  'paired_actual_bp':paired.net_return.mean()*1e4,
                  'excess_bp':(paired.net_return-paired.control_net_return).mean()*1e4})
                for metric,control_col in [('net_r','control_net_r'),('net_return','control_net_return')]:
                    if arm in study.COMPARATORS:
                        comparator=study.COMPARATORS[arm];b=sample.loc[sample.arm.eq(comparator)]
                        effects.append({'side':side,'period':period,'arm':arm,'comparison':comparator,'metric':metric,
                                        **uncertainty(a,b,metric)})
                    control=paired.copy();control[metric]=control[control_col]
                    effects.append({'side':side,'period':period,'arm':arm,'comparison':'random','metric':metric,
                                    **uncertainty(paired,control,metric)})
                for reason,part in a.groupby('exit_reason'):
                    reasons.append({'side':side,'period':period,'arm':arm,'exit_reason':reason,'count':len(part),
                                    'net_r':part.net_r.sum(),'mean_net_bp':part.net_return.mean()*1e4})
            # Fixed original entries separate exit-policy effects from occupancy.
            fixed=f.loc[f.event_key.isin(original.event_key)]
            for arm in study.ARMS[1:]:
                a=fixed.loc[fixed.arm.eq(arm)&fixed.status.eq('closed')&~fixed.baseline_censored].copy()
                if period=='earlier': a=a.loc[pd.to_datetime(a.exit_time,utc=True)<source.SPLIT]
                curr=sample.loc[sample.arm.eq(arm)];common=set(original.event_key)&set(curr.event_key)
                attribution.append({'side':side,'period':period,'arm':arm,'fixed_comparable':len(a),
                  'rescued_loss':int(((a.baseline_net_return<=0)&(a.net_return>0)).sum()),
                  'harmed_winner':int(((a.baseline_net_return>0)&(a.net_return<=0)).sum()),
                  'fixed_mean_delta_r':(a.net_r-a.baseline_net_r).mean(),
                  'fixed_mean_delta_original_r':(a.net_original_r-a.baseline_net_r).mean(),
                  'fixed_mean_delta_bp':(a.net_return-a.baseline_net_return).mean()*1e4,
                  'serial_common_closed':len(common),'serial_new_closed':len(set(curr.event_key)-set(original.event_key)),
                  'serial_lost_closed':len(set(original.event_key)-set(curr.event_key))})
    s=pd.DataFrame(summary);e=pd.DataFrame(effects)
    primary=e.side.eq('long')&e.period.eq('later');assert primary.sum()==30
    e['p_adjusted_primary30']=np.nan;e.loc[primary,'p_adjusted_primary30']=(e.loc[primary,'p']*30).clip(upper=1)
    gates=[]
    for arm,comparator in study.COMPARATORS.items():
        row=s.query("side=='long' and period=='later' and arm==@arm").iloc[0]
        tests=e.loc[primary&e.arm.eq(arm)]
        checks={'positive_mean_r_bp':bool(row.mean_net_r>0 and row.mean_net_bp>0),
            'all_primary_ci_positive':bool(tests.ci_low.gt(0).all()),
            'all_primary_p_pass':bool(tests.p_adjusted_primary30.lt(.01).all()),
            'coverage_pass':bool(row.coverage_pct>=90),
            'p_resolution_sufficient':bool((tests.p_resolution*30).lt(.01).all())}
        gates.append({'arm':arm,'comparator':comparator,**checks,'passed':all(checks.values()),
            'status':'inconclusive_resolution' if not checks['p_resolution_sufficient'] else 'passed' if all(checks.values()) else 'not_passed',
            'minimum_adjusted_p':min(1.,float(tests.p_resolution.max()*30))})
    symbol=[]
    for (sym,arm,side),part in t.loc[~t.censored].groupby(['symbol','arm','side']):
        symbol.append({'symbol':sym,'arm':arm,'side':side,**stats(part),
          'random_r':part.loc[part.matched,'control_net_r'].mean(),'random_bp':part.loc[part.matched,'control_net_return'].mean()*1e4})
    out=run.parent/'statistics';out.mkdir(exist_ok=True)
    tables={'summary':s,'effects':e,'attribution':pd.DataFrame(attribution),'gates':pd.DataFrame(gates),
      'exit_reasons':pd.DataFrame(reasons),'by_symbol':pd.DataFrame(symbol),
      'censored':t.loc[t.censored,['symbol','arm','event_key','signal_bar_open','entry_time','exit_time','exit_reason']]}
    for name,table in tables.items(): table.to_csv(out/f'{name}.csv',index=False)
    (out/'receipt.json').write_text(json.dumps({'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
      'input_completion_sha256':source.digest(run/'completion.json'),'report_source_sha256':source.digest(Path(__file__)),
      'files':{p.name:source.digest(p) for p in out.glob('*.csv')},'training_eligible':False,'production_eligible':False},indent=2)+'\n')
    print(s.query("side=='long' and period in ['full','later']")[[
      'period','arm','closed','win_rate_pct','total_net_r','mean_net_bp','worst_net_r','net_loss_beyond_2r','random_r']].to_string(index=False))
    print(pd.DataFrame(gates).to_string(index=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,default=study.EXP/'run_v1');report(p.parse_args().run)

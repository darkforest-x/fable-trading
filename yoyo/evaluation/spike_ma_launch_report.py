"""Receipt-bound statistics for the preregistered SPIKE morphology experiment.

All trade outcomes remain separate from entry features. Fixed filters are not
ranking models; no fabricated AUC or top-decile statistic is produced. The
primary effect unit is bp, with month-cluster bootstrap and same-side matched
random week-cluster sign flips. Sum R is explicitly not an account return.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_ma_launch_study import ARMS,EXP,source,validate_receipt,shared


def clean(value):
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    if isinstance(value,np.generic):return clean(value.item())
    if isinstance(value,float) and not math.isfinite(value):return None
    return value


def load_run(directory):
    identity=json.loads((directory/'identity.json').read_text())
    manifest=json.loads((directory/'manifest.json').read_text())
    run_hash=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    if not manifest['complete'] or manifest['run_identity']!=run_hash:
        raise ValueError('incomplete or mismatched run')
    if set(manifest['receipts'])!=set(identity['symbols']):
        raise ValueError('missing stream receipts')
    parts={name:[] for name in ('trades','controls','decisions')};receipts=[]
    for symbol in identity['symbols']:
        folder=directory/'streams'/symbol
        if source.digest(folder/'receipt.json')!=manifest['receipts'][symbol]:
            raise ValueError('receipt hash drift')
        r=validate_receipt(folder,run_hash,identity['inputs'][symbol]);receipts.append(r)
        for name in parts:
            frame=pd.read_csv(folder/f'{name}.csv.gz')
            if len(frame):parts[name].append(frame)
    return identity,{k:pd.concat(v,ignore_index=True) if v else pd.DataFrame() for k,v in parts.items()},receipts


def add_period(trades,split):
    out=trades.copy()
    for name in ('entry_time','exit_time','signal_bar_open'):
        out[name]=pd.to_datetime(out[name],utc=True)
    out['period']=np.where(out.entry_time>=split,'later',np.where(out.exit_time<split,'earlier','cross_split'))
    out['month']=out.entry_time.dt.strftime('%Y-%m')
    week=out.entry_time.dt.normalize()-pd.to_timedelta(out.entry_time.dt.dayofweek,unit='D')
    out['week']=week.dt.strftime('%Y-%m-%d')
    out['net_bp']=out.net_return*1e4
    out['gross_bp']=out.gross_return*1e4
    return out


def metrics(g):
    c=g.loc[~g.censored.astype(bool)]
    loss=-c.loc[c.net_r<0,'net_r'].sum()
    return {'entries':len(g),'n':len(c),'censored':int(g.censored.sum()),
            'win_rate':float((c.net_r>0).mean()),'mean_gross_bp':float(c.gross_bp.mean()),
            'mean_net_bp':float(c.net_bp.mean()),'sum_net_r':float(c.net_r.sum()),
            'mean_net_r':float(c.net_r.mean()),'pf_r':float(c.loc[c.net_r>0,'net_r'].sum()/loss) if loss else math.nan,
            'net_ge3r':int((c.net_r>=3).sum()),'net_gt5r':int((c.net_r>5).sum()),'net_gt10r':int((c.net_r>10).sum())}


def matched(g,controls,split,period):
    out=g.loc[~g.censored.astype(bool)].merge(controls,on='event_key',validate='many_to_one')
    good=out.matched.fillna(False).astype(bool)&~out.control_censored.fillna(True).astype(bool)
    if period=='earlier':good &= pd.to_datetime(out.control_exit_time,utc=True)<split
    if period=='later':good &= pd.to_datetime(out.control_entry_time,utc=True)>=split
    out=out.loc[good].copy()
    out['control_net_bp']=out.control_net_return*1e4
    out['excess_bp']=out.net_bp-out.control_net_bp
    return out


def month_delta(base,treatment,cfg,period='later'):
    """Resample the same calendar months for both arms, preserving unequal counts."""
    lo=pd.Timestamp(cfg['start'] if period=='full' else cfg['split'])
    hi=pd.Timestamp(cfg['end'])-pd.Timedelta(nanoseconds=1)
    calendar=pd.period_range(lo.tz_localize(None).to_period('M'),hi.tz_localize(None).to_period('M'),freq='M').astype(str)
    def blocks(g):
        return g.groupby('month').net_bp.agg(['sum','count']).reindex(calendar,fill_value=0).to_numpy(float)
    b,t=blocks(base),blocks(treatment)
    if not len(base) or not len(treatment):
        return {'estimate_bp':None,'low95':None,'high95':None,'valid_draws':0,'calendar_months':len(calendar)}
    draws=np.random.default_rng(cfg['seed']).integers(0,len(calendar),size=(cfg['bootstrap_reps'],len(calendar)))
    bs,ts=b[draws].sum(axis=1),t[draws].sum(axis=1)
    valid=(bs[:,1]>0)&(ts[:,1]>0)
    differences=ts[valid,0]/ts[valid,1]-bs[valid,0]/bs[valid,1]
    interval=np.quantile(differences,[.025,.975]) if len(differences) else [math.nan,math.nan]
    return {'estimate_bp':float(treatment.net_bp.mean()-base.net_bp.mean()),'low95':float(interval[0]),
            'high95':float(interval[1]),'valid_draws':int(valid.sum()),'calendar_months':len(calendar)}


def random_test(pairs,cfg):
    if pairs.empty:return {'pairs':0,'estimate_bp':None,'p_raw':1.,'weeks':0}
    values=pairs.groupby('week').excess_bp.sum().to_numpy(float)/len(pairs)
    observed=float(values.sum());rng=np.random.default_rng(cfg['seed']+1)
    signs=rng.choice([-1.,1.],size=(cfg['permutation_reps'],len(values)))
    p=(1+int(((signs*values).sum(axis=1)>=observed-1e-12).sum()))/(cfg['permutation_reps']+1)
    return {'pairs':len(pairs),'estimate_bp':observed,'p_raw':p,'weeks':len(values),
            'method':'one-sided approximate weekly clustered paired sign-flip; not a randomized trial'}


def holm_two(rows):
    if len(rows)!=2:raise ValueError('fixed two-arm comparison family')
    running=0.
    for rank,i in enumerate(sorted(range(2),key=lambda i:rows[i]['p_raw'])):
        running=max(running,min(1.,(2-rank)*rows[i]['p_raw']))
        rows[i]['p_holm']=running


def build(directory,output):
    if output.exists():raise FileExistsError('preserve prior statistics')
    declared=(Path(__file__),Path('tests/evaluation/test_spike_ma_launch_report.py'),EXP/'INTERPRETATION.md')
    if not shared.engine._committed(declared):
        raise ValueError('commit statistical builder and its checks before summarizing outcomes')
    identity,parts,receipts=load_run(directory);cfg=identity['config'];split=pd.Timestamp(cfg['split'])
    trades=add_period(parts['trades'],split);controls=parts['controls']
    if controls.event_key.duplicated().any():raise ValueError('duplicate shared control event')
    if trades.duplicated(['arm','event_key']).any():raise ValueError('duplicate trade event in arm')
    decisions=parts['decisions'];rows=[];tails=[]
    for period in ('full','earlier','later','cross_split'):
        period_trades=trades if period=='full' else trades.loc[trades.period.eq(period)]
        for arm in ARMS:
            a=period_trades.loc[period_trades.arm.eq(arm)]
            for direction,sub in [('both',a),('long',a.loc[a.side.eq(1)]),('short',a.loc[a.side.eq(-1)])]:
                pairs=matched(sub,controls,split,period)
                rows.append({'arm':arm,'period':period,'direction':direction,**metrics(sub),
                             'random_pairs':len(pairs),'random_mean_net_bp':float(pairs.control_net_bp.mean()),
                             'paired_excess_bp':float(pairs.excess_bp.mean()),
                             'paired_excess_r':float((pairs.net_r-pairs.control_net_r).mean())})
            if arm!='baseline':
                b=period_trades.loc[period_trades.arm.eq('baseline')&~period_trades.censored.astype(bool)]
                a=a.loc[~a.censored.astype(bool)]
                common=b.merge(a,on='event_key',suffixes=('_b','_t'))
                for column in ('entry_price','initial_stop','exit_price','net_r'):
                    if not np.allclose(common[column+'_b'],common[column+'_t'],rtol=0,atol=1e-10):
                        raise ValueError('same-entry outcome changed: '+column)
                for label,value,strict in [('ge3',3,False),('gt5',5,True),('gt10',10,True)]:
                    original=set(b.loc[b.net_r.gt(value) if strict else b.net_r.ge(value),'event_key'])
                    now=set(a.loc[a.net_r.gt(value) if strict else a.net_r.ge(value),'event_key'])
                    tails.append({'arm':arm,'period':period,'threshold':label,'baseline_winners':len(original),
                                  'retained':len(original&now),'lost':len(original-now),'new':len(now-original),
                                  'common_closed':len(common)})
    metric_frame=pd.DataFrame(rows);inference=[]
    closed=trades.loc[~trades.censored.astype(bool)]
    later=closed.loc[closed.period.eq('later')]
    base=later.loc[later.arm.eq('baseline')]
    for arm in cfg['primary_arms']:
        t=later.loc[later.arm.eq(arm)]
        test=random_test(matched(t,controls,split,'later'),cfg)
        inference.append({'arm':arm,**test,'delta_vs_baseline':month_delta(base,t,cfg),
                          'later_n':len(t),'mean_net_bp':float(t.net_bp.mean())})
    holm_two(inference)
    for row in inference:
        ci=row['delta_vs_baseline']['low95']
        row['passed']=bool(row['later_n']>=cfg['minimum_later_closed'] and row['mean_net_bp']>0
                           and row['p_holm']<.01 and ci is not None and ci>0)
    summary={'experiment_id':cfg['experiment_id'],'complete':True,'symbols':len(receipts),'subset':identity['subset'],
             'candidates':sum(r['summary']['candidates'] for r in receipts),
             'passed_candidates':{a:sum(r['summary']['passed'][a] for r in receipts) for a in ARMS},
             'ma_unknown':sum(r['summary']['unknown_ma'] for r in receipts),
             'inference':inference,'training_eligible':False,'production_eligible':False,
             'native_pine_parity':False,'risk_notice':'Retrospective rules selected in2026; no unseen forward claim. Fixed filters, AUC/top-decile not applicable.',
             'code_sha256':source.digest(Path(__file__)),
             'statistics_source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
             'statistics_sources':{str(p):source.digest(p) for p in declared},
             'input_manifest_sha256':source.digest(directory/'manifest.json')}
    output.mkdir(parents=True)
    metric_frame.to_csv(output/'metrics.csv',index=False);pd.DataFrame(tails).to_csv(output/'tail_retention.csv',index=False)
    (output/'summary.json').write_text(json.dumps(clean(summary),ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    closed[['arm','symbol','event_key','entry_time','exit_time','side','month','net_bp','net_r']].to_csv(output/'closed_trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    print(json.dumps(clean(summary),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,default=EXP/'run_v1');p.add_argument('--output',type=Path,default=EXP/'statistics_v1')
    a=p.parse_args();build(a.input,a.output)

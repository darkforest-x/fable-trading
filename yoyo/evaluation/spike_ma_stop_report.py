"""Summarize the fixed MA-stop experiment without selecting new parameters.

Outcomes are grouped by signal-close time; development exits and controls
crossing the split are excluded. Month blocks include all symbols together.
Net R uses actual initial risk; original-risk R and net bp expose denominator
and fixed-notional differences. No pooled event sum is an account return.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_ma_stop_study as study
from yoyo.evaluation import spike_v10_4_study as source


def subset(t,period,side):
    signal=pd.to_datetime(t.signal_bar_open,utc=True)+pd.Timedelta(minutes=15)
    exit_=pd.to_datetime(t.exit_time,utc=True)
    keep=~t.censored
    if period=='earlier': keep &= (signal<source.SPLIT)&(exit_<source.SPLIT)
    elif period=='later': keep &= signal>=source.SPLIT
    if side!='all': keep &= t.side.eq(1 if side=='long' else -1)
    return t.loc[keep].copy()


def stats(t):
    r=t.net_r.to_numpy(float)
    loss=-r[r<0].sum()
    return {'closed':len(t),'win_rate_pct':100*np.mean(r>0) if len(r) else np.nan,
            'total_net_r':r.sum(),'mean_net_r':np.mean(r) if len(r) else np.nan,
            'mean_original_r':t.net_original_r.mean(),'mean_gross_bp':t.gross_return.mean()*1e4,
            'mean_net_bp':t.net_return.mean()*1e4,'total_net_bp':t.net_return.sum()*1e4,
            'pf_r':r[r>0].sum()/loss if loss>0 else np.nan,
            'median_risk_pct':100*t.initial_risk_frac.median(),
            'median_position_multiplier':t.position_multiplier.median(),
            'widened_pct':100*t.position_multiplier.lt(1-1e-8).mean(),
            'realized_gt5r':int((r>5).sum()),'realized_gt10r':int((r>10).sum())}


def uncertainty(a,b,col):
    """Weighted monthly bootstrap of difference of per-trade means, not iid bars."""
    months=sorted(set(a.month)|set(b.month))
    if len(months)<2 or not len(a) or not len(b):
        return {'difference':np.nan,'ci_low':np.nan,'ci_high':np.nan,'p':np.nan,'months':len(months)}
    def blocks(t):
        return t.groupby('month')[col].agg(['sum','count']).reindex(months,fill_value=0).to_numpy(float)
    x,y=blocks(a),blocks(b); rng=np.random.default_rng(study.SEED)
    draw=rng.integers(0,len(months),size=(study.REPS,len(months)))
    xx,yy=x[draw].sum(axis=1),y[draw].sum(axis=1)
    valid=(xx[:,1]>0)&(yy[:,1]>0)
    boot=xx[valid,0]/xx[valid,1]-yy[valid,0]/yy[valid,1]
    effect=a[col].mean()-b[col].mean()
    # Joint month blocks are sign-flipped, preserving cross-symbol dependence.
    contributions=x[:,0]/x[:,1].sum()-y[:,0]/y[:,1].sum()
    signs=rng.choice([-1.,1.],size=(study.REPS,len(months)))
    perm=(signs*contributions).sum(axis=1)
    return {'difference':effect,'ci_low':np.quantile(boot,.025),'ci_high':np.quantile(boot,.975),
            'p':(1+int((perm>=effect).sum()))/(study.REPS+1),'months':len(months)}


def report(run):
    done=json.loads((run/'completion.json').read_text())
    for name,digest in done['files'].items(): assert source.digest(run/name)==digest
    t=pd.read_csv(run/'trades.csv.gz');c=pd.read_csv(run/'controls.csv.gz');f=pd.read_csv(run/'fixed.csv.gz')
    t=t.merge(c,on=['event_key','arm'],validate='one_to_one')
    t['month']=(pd.to_datetime(t.signal_bar_open,utc=True)+pd.Timedelta(minutes=15)).dt.strftime('%Y-%m')
    summary=[];effects=[];attributions=[]
    for side in ('long','short','all'):
        for period in ('full','earlier','later'):
            sample=subset(t,period,side)
            original=sample.loc[sample.arm.eq('baseline')]
            for arm in study.ARMS:
                a=sample.loc[sample.arm.eq(arm)]
                paired=a.loc[a.matched].copy()
                if period=='earlier':
                    paired=paired.loc[pd.to_datetime(paired.control_exit_time,utc=True)<source.SPLIT]
                row={'side':side,'period':period,'arm':arm,**stats(a),
                     'matched':len(paired),'coverage_pct':100*len(paired)/len(a) if len(a) else np.nan,
                     'paired_actual_r':paired.net_r.mean(),'random_r':paired.control_net_r.mean(),
                     'excess_r':(paired.net_r-paired.control_net_r).mean(),
                     'random_bp':paired.control_net_return.mean()*1e4,
                     'paired_actual_bp':paired.net_return.mean()*1e4,
                     'excess_bp':(paired.net_return-paired.control_net_return).mean()*1e4}
                signal=pd.to_datetime(t.signal_bar_open,utc=True)+pd.Timedelta(minutes=15)
                cross=t.arm.eq(arm)&~t.censored&(signal<source.SPLIT)&(pd.to_datetime(t.exit_time,utc=True)>=source.SPLIT)
                if side!='all': cross &= t.side.eq(1 if side=='long' else -1)
                row['cross_split_closed']=int(cross.sum())
                summary.append(row)
                for metric,control_col in [('net_r','control_net_r'),('net_return','control_net_return')]:
                    if arm!='baseline':
                        effect=uncertainty(a,original,metric)
                        effects.append({'side':side,'period':period,'arm':arm,'comparison':'baseline','metric':metric,**effect})
                    control=paired.copy(); control[metric]=control[control_col]
                    effect=uncertainty(paired,control,metric)
                    effects.append({'side':side,'period':period,'arm':arm,'comparison':'random','metric':metric,**effect})
            # Same original entries: outcomes only, independent of later occupancy.
            fixed=f.loc[f.event_key.isin(original.event_key)]
            for arm in study.ARMS[1:]:
                a=fixed.loc[fixed.arm.eq(arm)&fixed.status.eq('closed')&~fixed.baseline_censored].copy()
                if period=='earlier': a=a.loc[pd.to_datetime(a.exit_time,utc=True)<source.SPLIT]
                curr=sample.loc[sample.arm.eq(arm)]
                common=set(original.event_key)&set(curr.event_key)
                attributions.append({'side':side,'period':period,'arm':arm,'fixed_comparable':len(a),
                    'rescued_loss':int(((a.baseline_net_return<=0)&(a.net_return>0)).sum()),
                    'harmed_winner':int(((a.baseline_net_return>0)&(a.net_return<=0)).sum()),
                    'fixed_mean_delta_r':(a.net_r-a.baseline_net_r).mean(),
                    'fixed_mean_delta_original_r':(a.net_original_r-a.baseline_net_r).mean(),
                    'fixed_mean_delta_bp':(a.net_return-a.baseline_net_return).mean()*1e4,
                    'serial_common_closed':len(common),'serial_new_closed':len(set(curr.event_key)-set(original.event_key)),
                    'serial_lost_closed':len(set(original.event_key)-set(curr.event_key))})
    s=pd.DataFrame(summary);e=pd.DataFrame(effects);a=pd.DataFrame(attributions)
    e['p_adjusted_six']=(e.p*6).clip(upper=1)
    gates=[]
    for arm in study.ARMS[1:]:
        row=s.query("side=='long' and period=='later' and arm==@arm").iloc[0]
        delta=e.query("side=='long' and period=='later' and arm==@arm and metric=='net_r' and comparison=='baseline'").iloc[0]
        null=e.query("side=='long' and period=='later' and arm==@arm and metric=='net_r' and comparison=='random'").iloc[0]
        checks={'profitable_r_and_bp':bool(row.mean_net_r>0 and row.mean_net_bp>0),
                'delta_lower_positive':bool(delta.ci_low>0),'random_lower_positive':bool(null.ci_low>0),
                'delta_p_pass':bool(delta.p_adjusted_six<.01),'random_p_pass':bool(null.p_adjusted_six<.01),
                'coverage_pass':bool(row.coverage_pct>=90)}
        gates.append({'arm':arm,**checks,'passed':all(checks.values())})
    out=run.parent/'statistics';out.mkdir(exist_ok=True)
    for name,table in [('summary',s),('effects',e),('attribution',a),('gates',pd.DataFrame(gates))]:
        table.to_csv(out/f'{name}.csv',index=False)
    # Full fixed-entry evidence remains available for each symbol, including losers.
    symbol_rows=[]
    for (symbol,arm,side),part in t.loc[~t.censored].groupby(['symbol','arm','side']):
        symbol_rows.append({'symbol':symbol,'arm':arm,'side':side,**stats(part),
            'random_r':part.loc[part.matched,'control_net_r'].mean(),
            'random_bp':part.loc[part.matched,'control_net_return'].mean()*1e4})
    pd.DataFrame(symbol_rows).to_csv(out/'by_symbol.csv',index=False)
    (out/'receipt.json').write_text(json.dumps({'source_commit':__import__('subprocess').check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'input_completion_sha256':source.digest(run/'completion.json'),'report_source_sha256':source.digest(Path(__file__)),
        'files':{p.name:source.digest(p) for p in out.glob('*.csv')},'training_eligible':False,'production_eligible':False},indent=2)+'\n')
    print(s.query("side=='long' and period in ['full','later']")[[
        'period','arm','closed','win_rate_pct','total_net_r','mean_net_r','mean_net_bp','random_r','excess_r','median_position_multiplier']].to_string(index=False))
    print(pd.DataFrame(gates).to_string(index=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,default=study.EXP/'run_v1')
    report(p.parse_args().run)

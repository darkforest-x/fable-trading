"""Select executable exit rules in historical order; tag any September-constrained choice explicitly."""
import argparse
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_selection as search


def metrics(frame):
    """Input contains only admitted and fully priced independent basket or reference trades."""
    good=frame.dropna(subset=['net_pct']).sort_values('decision')
    stats=search.describe(good)
    peaks=good.peak_pct.sum()
    stats.update(scheduled=len(frame), retained_pct=100*good.net_pct.sum()/peaks if peaks>0 else np.nan,
                 upside_ge10_half_pct=100*good.gain_half.sum()/good.up10.sum() if good.up10.sum()>0 else np.nan,
                 avg_peak=good.peak_pct.mean(), member_win=100*good.win.mean())
    return stats


def fold_grid(frame):
    """One row per parameter and fold. Use only pre-2026 data for the primary and retention ranks."""
    keys=['candidate_id','asset','policy_id','hours','epoch']
    f=frame.dropna(subset=['net_pct']).sort_values('decision').copy()
    f['clipped']=f.net_pct.clip(-20,20)
    f['won']=f.net_pct>0
    f['month']=f.decision.dt.strftime('%Y-%m')
    f['capital']=(1+f.net_pct/100).groupby([f[k] for k in keys]).cumprod()
    high=f.groupby(keys).capital.cummax().clip(lower=1)
    f['dd']=100*(1-f.capital/high)
    g=f.groupby(keys,observed=True)
    summary=g.agg(n=('net_pct','count'),mean=('net_pct','mean'),median=('net_pct','median'),months=('month','nunique'),
                  win=('won','mean'),clipped=('clipped','mean'),sd=('clipped','std'),dd=('dd','max'),
                  total=('net_pct','sum'),peak_total=('peak_pct','sum'),strong=('up10','sum'),
                  kept=('gain_half','sum'),member_win=('win','mean')).reset_index()
    summary['lcb']=summary.clipped-1.28*summary.sd/np.sqrt(summary.n)
    summary['retained_pct']=100*summary.total/summary.peak_total.replace(0,np.nan)
    summary['half_keep_pct']=100*summary.kept/summary.strong.replace(0,np.nan)
    summary['win']*=100
    summary['member_win']*=100
    return summary


def candidates(train):
    keys=['candidate_id','asset','policy_id','hours']
    wet=train.pivot(index=keys,columns='epoch',values=['n','mean','win','lcb','dd','months','retained_pct',
                                                     'half_keep_pct','strong','member_win']).copy()
    wet.columns=[f'{k}{i}' for k,i in wet.columns]
    for i in (0,1,2):
        for k in ('n','mean','win','lcb','dd','months','retained_pct','half_keep_pct','strong','member_win'):
            if f'{k}{i}' not in wet:wet[f'{k}{i}']=np.nan
    wet=wet.reset_index()
    wet['eligible']=((wet.n0>=12)&(wet.n1>=12)&(wet.n0+wet.n1>=30)&(wet.mean0>0)&(wet.mean1>0)
           &(wet.n0*wet.win0+wet.n1*wet.win1>=50*(wet.n0+wet.n1))&(wet.n2>=10)&(wet.months2>=4)
           &(wet.win2>=50)&(wet.mean2>0)&(wet.dd2<=35))
    wet['floor']=wet[['lcb0','lcb1','lcb2']].min(axis=1,skipna=False)
    wet['win_floor']=wet[['win0','win1','win2']].min(axis=1,skipna=False)
    wet['retain_floor']=wet[['retained_pct0','retained_pct1','retained_pct2']].min(axis=1,skipna=False)
    wet['strong_floor']=wet[['strong0','strong1','strong2']].min(axis=1,skipna=False)
    return wet


def choose(wide,cols,role):
    # If all groups fail the preregistered gates retain a diagnostic, with eligible=False.
    q=wide.dropna(subset=cols)
    if q.empty:return q
    q=q.sort_values(['eligible',*cols,'complexity','hours','candidate_id','policy_id'],
                    ascending=[False]*(1+len(cols))+[True]*4,kind='stable')
    return q.head(1).assign(role=role)


def run(stage):
    out=study.OUT
    entries=pd.read_csv(out/(stage+'_entries.csv'))
    policies=pd.read_csv(out/'policies.csv')
    keys=['candidate_id','policy_id','hours']
    selected=[]; eventsets=[]; stats=[]; all_development=[]; case_rows=[]
    for asset in search.ASSETS:
        frame=pd.read_parquet(out/(stage+'_event_fills')/(asset+'.parquet'))
        folds=fold_grid(frame[frame.epoch<3])
        all_development.append(folds)
        wide=candidates(folds).merge(policies,on='policy_id',validate='many_to_one',suffixes=('','_pol'))
        if not wide.hours.eq(wide.hours_pol).all():raise ValueError('Inconsistent holding horizons')
        wide['complexity']=((wide.kind!='time').astype(int)+(wide.stop_pct!=0).astype(int)+
                            (wide.take_fraction>0).astype(int)+(wide.take_fraction.between(.01,.99)).astype(int))
        picks=[choose(wide,['floor','win_floor'],'dynamic_primary')]
        if asset=='ALT':
            picks.append(choose(wide[wide.strong_floor>=10],['retain_floor','floor','win_floor'],'retention'))
        if asset=='BTC':
            # Deliberately case-aware. Only count the exact named alarm and original entry clock,
            # when the candidate genuinely admits it after applying its own prior-position schedule.
            case=frame[(frame.known==study.CASE_ENTRY-pd.Timedelta(minutes=30))&(frame.decision==study.CASE_ENTRY)].copy()
            case=case.merge(entries[['candidate_id','minutes']],on='candidate_id')
            case=case[case.minutes==5]
            case_rows.append(case)
            qualifies=case[case.points>=10000-1e-7][keys+['points']]
            choices=wide.merge(qualifies,on=keys,validate='one_to_one')
            picks.append(choose(choices,['floor','win_floor'],'case_target'))
        for pick in picks:
            if pick.empty:continue
            r=pick.iloc[0]; orig=r.candidate_id; ident=f'{r.role}_{asset}'
            params=entries[entries.candidate_id==orig].iloc[0].to_dict();params['entry_id']=orig
            params.update(r.drop(labels=['candidate_id']).to_dict())
            params.update(candidate_id=ident,hold_hours=int(r.hours))
            selected.append(params)
            group=frame[(frame.candidate_id==orig)&(frame.policy_id==r.policy_id)].copy()
            group['candidate_id']=ident
            eventsets.append(group)
            for name,sub in [(str(i),group[group.epoch==i]) for i in range(4)]+[
                ('2026_ex_september',group[(group.epoch==3)&(group.decision.dt.month!=9)]),
                ('all',group)]:
                stats.append(dict(candidate_id=ident,asset=asset,period=name,**metrics(sub)))
    out=out if stage=='all' else out/'anchor_check'
    out.mkdir(parents=True,exist_ok=True)
    pd.concat(all_development).to_parquet(out/'exit_development.parquet',index=False)
    pd.concat(case_rows).to_csv(out/'sept16_admitted_btc.csv',index=False)
    pd.DataFrame(selected).to_csv(out/'locked_rules.csv',index=False)
    pd.concat(eventsets).to_parquet(out/'locked_events.parquet',index=False)
    pd.DataFrame(stats).to_csv(out/'locked_evaluation.csv',index=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--stage',default='all',choices=['anchor','all'])
    run(parser.parse_args().stage)

"""Expanding annual audit of the seven prespecified breadth entries and common exit families.

Test-year selections use two completed historical blocks. Retrospectively chosen
entry finalists and the owner-specific September target are deliberately omitted.
"""
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_exit_rank as rank


def run():
    out=study.OUT; info=pd.read_csv(out/'policies.csv')
    audit=[]; details=[]
    for a in study.search.ASSETS:
        raw=pd.read_parquet(out/'anchor_event_fills'/(a+'.parquet'))
        raw=raw[raw.policy_id<187].copy()
        finish=raw.decision+pd.to_timedelta(raw.hours,unit='h')
        for year in (2023,2024,2025,2026):
            end=pd.Timestamp(year,1,1,tz='UTC')
            cut=pd.Timestamp(year-2,1,1,tz='UTC')
            history=raw[(finish<end)&(~((raw.decision<cut)&(finish>=cut)))].copy()
            history['epoch']=history.decision.ge(cut).astype(int)
            by=rank.fold_grid(history)
            if by.epoch.nunique()<2:
                audit.append(dict(asset=a,test_year=year,selected=False));continue
            wide=by.pivot(index=['candidate_id','policy_id','hours'],columns='epoch',values=['n','mean','win','lcb','dd'])
            wide.columns=[f'{k}{i}' for k,i in wide.columns]
            eligible=wide[(wide.n0>=12)&(wide.n1>=12)&(wide.n0+wide.n1>=30)&(wide.mean0>0)&(wide.mean1>0)
                          &(wide.win0*wide.n0+wide.win1*wide.n1>=50*(wide.n0+wide.n1))&(wide.dd1<=35)].reset_index()
            if eligible.empty:
                audit.append(dict(asset=a,test_year=year,selected=False));continue
            eligible['score']=eligible[['lcb0','lcb1']].min(axis=1)
            r=eligible.sort_values(['score','hours','candidate_id','policy_id'],ascending=[False,True,True,True]).iloc[0]
            future=raw[(raw.candidate_id==r.candidate_id)&(raw.policy_id==r.policy_id)&(raw.decision>=end)&(finish<end+pd.DateOffset(years=1))].copy()
            future['test_year']=year; details.append(future)
            audit.append(dict(asset=a,test_year=year,selected=True,candidate_id=r.candidate_id,
                policy_id=r.policy_id,policy_key=info.set_index('policy_id').loc[r.policy_id,'key'],training_floor=r.score,
                **rank.metrics(future)))
    pd.DataFrame(audit).to_csv(out/'annual_rule_selection.csv',index=False)
    if details:pd.concat(details).to_parquet(out/'annual_rule_selection_events.parquet',index=False)


if __name__=='__main__':run()

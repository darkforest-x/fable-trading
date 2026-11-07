"""Rank BTC exits actually meeting the owner's case target after settled funding and transaction fees."""
from dataclasses import asdict
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_exit_rank as rank
from yoyo.evaluation import monday_breadth_opt_funding as fund


def run():
    out=study.OUT; entries=pd.read_csv(out/'all_entries.csv')
    events=pd.read_parquet(out/'all_event_fills/BTC.parquet')
    anchors=events[events.policy_id==5].drop(columns=['policy_id','net_pct','peak_pct','win','points','legs','ambiguous','up10','gain_half','observed']).copy()
    entries=pd.concat([entries,pd.read_csv(out/'case_grid_entries.csv')],ignore_index=True)
    anchors=pd.concat([anchors,pd.read_parquet(out/'case_grid_admissions.parquet')],ignore_index=True)
    f=pd.read_parquet(fund.FUNDED_CACHE)
    frame=anchors.merge(f,left_on='decision',right_on='entry_time',how='left',validate='many_to_many')
    frame=frame.dropna(subset=['policy_id']).copy()
    frame['policy_id']=frame.policy_id.astype(int)+1000
    frame['points']=frame.funded_points
    frame['net_pct']=frame.funded_pct
    frame['win']=frame.net_pct>0;frame['up10']=frame.peak_pct>=10
    frame['gain_half']=frame.up10&(frame.net_pct>=.5*frame.peak_pct)
    frame['observed']=1;frame['legs']=frame.exit_legs
    defs=fund.funded_policies()
    pre=rank.fold_grid(frame[frame.epoch<3])
    eligible=rank.candidates(pre)
    params=pd.DataFrame([dict(policy_id=1000+i,key=p.key,**asdict(p)) for i,p in enumerate(defs)])
    eligible=eligible.merge(params,on=['policy_id','hours'],validate='many_to_one')
    eligible['complexity']=1+(eligible.take_fraction.between(.01,.99)).astype(int)+(eligible.width>0).astype(int)
    event=frame[(frame.decision==study.CASE_ENTRY)&(frame.known==study.CASE_ENTRY-pd.Timedelta(minutes=30))&(frame.minutes==5)&(frame.funded_points>=10000-1e-7)]
    eligible=eligible.merge(event[['candidate_id','policy_id','hours','funded_points']],on=['candidate_id','policy_id','hours'],validate='one_to_one')
    eligible.to_csv(out/'btc_funding_case_development.csv',index=False)
    # Keep the best qualifying pre-2026 score and the best partial-runner alternative under the same gates.
    selected=[];returns=[];evaluated=[]
    for name,sub in [('case_target',eligible),('case_runner',eligible[eligible.kind=='btc_funded_partial'])]:
        pick=rank.choose(sub,['floor','win_floor'],name)
        if not len(pick):continue
        r=pick.iloc[0]; original=r.candidate_id
        ident=name+'_funded_BTC'; rule=entries[entries.candidate_id==original].iloc[0].to_dict()
        rule.update(r.to_dict());rule.update(candidate_id=ident,entry_id=original,hold_hours=int(r.hours))
        selected.append(rule)
        g=frame[(frame.candidate_id==original)&(frame.policy_id==r.policy_id)].copy()
        g['candidate_id']=ident;returns.append(g)
        for label,part in [(str(i),g[g.epoch==i]) for i in range(4)]+[
             ('2026_ex_september',g[(g.epoch==3)&(g.decision.dt.month!=9)]),('all',g)]:
            evaluated.append(dict(candidate_id=ident,asset='BTC',period=label,**rank.metrics(part)))
    base=pd.read_csv(out/'locked_rules.csv')
    base=base[~base.candidate_id.str.contains('_funded_BTC')].copy()
    base.loc[base.role.eq('case_target'),'role']='fee_only_case'
    for name,value,extra in [('locked_rules.csv',base, pd.DataFrame(selected)),
          ('locked_events.parquet',pd.read_parquet(out/'locked_events.parquet'),pd.concat(returns) if returns else pd.DataFrame()),
          ('locked_evaluation.csv',pd.read_csv(out/'locked_evaluation.csv'),pd.DataFrame(evaluated))]:
        original=value[~value.candidate_id.str.contains('_funded_BTC')]
        combined=pd.concat([original,extra.reindex(columns=original.columns)],ignore_index=True)
        if name.endswith('.parquet'):combined.to_parquet(out/name,index=False)
        else:combined.to_csv(out/name,index=False)


if __name__=='__main__':run()

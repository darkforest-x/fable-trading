"""Freeze separate historical objectives for win rate and return, then measure the later-year tradeoff."""
from types import SimpleNamespace
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_rank as rank
from yoyo.evaluation import monday_breadth_opt_selection as s
from yoyo.evaluation import monday_breadth_opt_exit_study as study


def run():
    vs=pd.read_csv(s.SEARCH/'validation.csv')
    eligible=vs[vs.validation_pass].copy()
    eligible['wins']=eligible[['a_win','b_win','v_win']].min(axis=1)
    eligible['returns']=eligible[['a_clipped','b_clipped','v_clipped']].min(axis=1)
    rows=[];paths=[]
    for asset,z in eligible.groupby('asset'):
        for objective in ('wins','returns'):
            r=z.sort_values([objective,'joint_score','complexity'],ascending=[False,False,True]).iloc[0]
            rule=SimpleNamespace(**r.to_dict())
            fr,ret=s.inputs.cached_partition(rule.minutes,rule.wait_min)
            e=s.evaluate_rule(rule,fr,ret)
            eligible_2026=e[e.epoch==3].copy()
            key=f'fixed_{asset}_{objective}';eligible_2026['candidate_id']=key;paths.append(eligible_2026)
            rows.append(dict(candidate_id=key,origin='fixed',asset=asset,objective=objective,minutes=r.minutes,wait_min=r.wait_min,
                breadth=r.breadth,diffusion=r.diffusion,confirmation=r.confirmation,hours=r.hold_hours,
                training_floor_for_objective=r[objective],**s.describe(eligible_2026)))
    pre=pd.read_parquet(study.OUT/'exit_development.parquet')
    joint=rank.candidates(pre)
    joint=joint[joint.eligible].copy()
    # Use unclipped means only as displayed statistics. Rank return by the worst clipped chronological mean.
    capped=pre.groupby(['asset','candidate_id','policy_id','hours']).clipped.min().rename('returns').reset_index()
    joint=joint.merge(capped,on=['asset','candidate_id','policy_id','hours'],validate='one_to_one')
    joint['wins']=joint.win_floor
    policies=pd.read_csv(study.OUT/'policies.csv').set_index('policy_id')
    entries=pd.read_csv(study.OUT/'all_entries.csv').set_index('candidate_id')
    for asset,z in joint.groupby('asset'):
        frame=pd.read_parquet(study.OUT/'all_event_fills'/(asset+'.parquet'))
        for objective in ('wins','returns'):
            r=z.sort_values([objective,'floor','policy_id'],ascending=[False,False,True]).iloc[0]
            sub=frame[(frame.epoch==3)&(frame.candidate_id==r.candidate_id)&(frame.policy_id==r.policy_id)].copy()
            key=f'adaptive_{asset}_{objective}';sub['candidate_id']=key;paths.append(sub)
            setup=entries.loc[r.candidate_id]
            rows.append(dict(candidate_id=key,origin='adaptive',asset=asset,objective=objective,minutes=setup.minutes,
                wait_min=setup.wait_min,breadth=setup.breadth,diffusion=setup.diffusion,confirmation=setup.confirmation,
                hours=r.hours,policy_id=r.policy_id,exit=policies.loc[r.policy_id,'key'],training_floor_for_objective=r[objective],
                **s.describe(sub)))
    pd.DataFrame(rows).to_csv(s.OUT/'objective_tradeoffs_2026.csv',index=False)
    pd.concat(paths,ignore_index=True).to_parquet(s.OUT/'objective_tradeoff_events_2026.parquet',index=False)


if __name__=='__main__':run()

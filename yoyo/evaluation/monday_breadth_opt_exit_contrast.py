"""Compare exit design at identical entry timestamps and common pricing coverage."""
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_exits as sim
from yoyo.evaluation.monday_breadth_opt_match_exits import paired_month_p


def run():
    out=study.OUT
    locked=pd.read_csv(out/'locked_rules.csv')
    summary=[];checks=[]
    for asset,group in locked[locked.role.isin(['dynamic_primary','retention','fee_only_case'])].groupby('asset'):
        events=pd.read_parquet(out/'all_event_fills'/(asset+'.parquet'))
        policies=sim.policies(asset=='BTC')
        for r in group.itertuples():
            same=events[(events.candidate_id==r.entry_id)&(events.hours==r.hold_hours)]
            winner=same[same.policy_id==r.policy_id]
            comparisons=[(i,p.key) for i,p in enumerate(policies)
                 if p.hours==r.hold_hours and ((p.kind=='time' and (p.stop_pct==0 or (p.stop_pct==6 and p.take_pct in (0,5,10))))
                 or (p.kind=='percent_trail' and p.take_fraction==0 and p.stop_pct==6 and p.activate_pct==5 and p.width==8))]
            for i,title in comparisons:
                base=same[same.policy_id==i][['decision','net_pct']].rename(columns={'net_pct':'base_pct'})
                pair=winner.merge(base,on='decision',validate='one_to_one').dropna(subset=['net_pct','base_pct'])
                for label,g in [('all',pair),('2026',pair[pair.epoch==3]),('2026_ex_september',pair[(pair.epoch==3)&(pair.decision.dt.month!=9)])]:
                    if g.empty:continue
                    d=g.net_pct-g.base_pct
                    up=g.peak_pct>=10;down=~up
                    summary.append(dict(candidate_id=r.candidate_id,asset=asset,period=label,n=len(g),reference=title,
                       reference_policy_id=i,policy_id=r.policy_id,winner_pct=g.net_pct.mean(),base_pct=g.base_pct.mean(),
                       mean_difference=d.mean(),better_share=100*d.gt(1e-7).mean(),worse_share=100*d.lt(-1e-7).mean(),
                       win_pct=100*g.net_pct.gt(0).mean(),base_win_pct=100*g.base_pct.gt(0).mean(),mfe10_events=int(up.sum()),
                       winner_on_mfe10=g.loc[up,'net_pct'].mean(),base_on_mfe10=g.loc[up,'base_pct'].mean(),
                       winner_on_small_rebound=g.loc[down,'net_pct'].mean(),base_on_small_rebound=g.loc[down,'base_pct'].mean(),
                       descriptive_p=paired_month_p(pd.Series(d.to_numpy(),index=g.decision))))
                if policies[i].stop_pct==0 and policies[i].kind=='time':
                    select=pair[pair.epoch==3].copy()
                    select['saved_or_surrendered_pp']=select.net_pct-select.base_pct
                    select['candidate_id']=r.candidate_id
                    checks.append(select[['candidate_id','decision','initial','new_coins','net_pct','peak_pct','base_pct','saved_or_surrendered_pp']])
    pd.DataFrame(summary).to_csv(out/'same_entry_exit_comparisons.csv',index=False)
    if checks:pd.concat(checks).to_csv(out/'same_entry_examples_2026.csv',index=False)


if __name__=='__main__':run()

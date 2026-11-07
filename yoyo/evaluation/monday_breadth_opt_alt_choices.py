"""Compare causal altcoin selections against the full equal-weight basket on identical event dates."""
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_rank as rank
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_exits as sim
from yoyo.evaluation import monday_breadth_opt_payoffs as quotes
from yoyo.evaluation.monday_breadth_opt_match_exits import paired_month_p


def run():
    out=study.OUT
    e=pd.read_parquet(out/'anchor_event_fills/ALT.parquet')
    cohorts=['anchor_ALT_5_30_N25_100000_any','anchor_ALT_5_30_N10_100000_at_most_four','anchor_ALT_5_30_N35_99_zero']
    e=e[(e.policy_id==5)&e.candidate_id.isin(cohorts)&(e.expected>=8)]
    prices=study.member_bridge()
    g=e[['candidate_id','known','decision','expected','epoch','initial','new_coins']].merge(prices[prices.minutes==5],on='known')
    policies=sim.policies()
    labels=['time_144_0_0_0_0_0','time_144_12_0_0_0_0','profit_lock_144_12_10_0.5_0_0',
            'atr_4h_144_12_10_4_0_0','percent_trail_144_12_8_8_10_0.25']
    pids=[i for i,p in enumerate(policies) if p.key in labels]
    if len(labels)!=len(pids):raise ValueError('Cannot identify all five preregistered exits')
    pieces=[]
    for symbol,sub in g.groupby('symbol'):
        parts=list((quotes.RESEARCH/'constituents/minutes=5'/('symbol='+symbol)).glob('part-*.parquet'))
        if not parts:continue
        f=pd.concat([pd.read_parquet(p,columns=['known','reclaimed','reclaim_eligible','observed_return_pct'],
                filters=[('wait_min','=',30),('hold_hours','=',144)]) for p in parts],ignore_index=True)
        cache=study.CACHE/(symbol+'.parquet')
        if not cache.exists():continue
        fills=pd.read_parquet(cache,columns=['entry_time','policy_id','net_pct','peak_pct'],filters=[('policy_id','in',pids)])
        s=sub.merge(f,on='known',validate='many_to_one').merge(fills,left_on='decision',right_on='entry_time')
        pieces.append(s)
    rows=[]
    for (entry,t,pid),v in pd.concat(pieces).groupby(['candidate_id','decision','policy_id']):
        if v.symbol.nunique()!=v.expected.iloc[0] or not v.reclaim_eligible.all() or v.observed_return_pct.isna().any():continue
        if v.symbol.duplicated().any():raise ValueError('Duplicate position within basket')
        move=v.observed_return_pct
        for name,mask in [('all',move.notna()),('fastest_quarter',move>=move.quantile(.75)),
                ('slowest_quarter',move<=move.quantile(.25)),('reclaimed_only',v.reclaimed.eq(True)),
                ('unreclaimed_only',v.reclaimed.eq(False))]:
            z=v[mask]
            if z.empty:continue
            rows.append(dict(entry_id=entry,decision=t,epoch=v.epoch.iloc[0],initial=v.initial.iloc[0],new=v.new_coins.iloc[0],
                policy_id=pid,rule=policies[pid].key,selection=name,selected_coins=len(z),total_coins=len(v),
                net_pct=z.net_pct.mean(),peak_pct=z.peak_pct.mean(),member_win_pct=100*z.net_pct.gt(0).mean(),
                all_same_exit_pct=v.net_pct.mean(),edge=z.net_pct.mean()-v.net_pct.mean()))
    result=pd.DataFrame(rows)
    result.to_parquet(out/'alt_selection_events.parquet',index=False)
    records=[]
    for (entry,pid,name),g in result.groupby(['entry_id','policy_id','selection']):
        for ep,z in g.groupby('epoch'):
            stat=study.search.describe(z)
            records.append(dict(entry_id=entry,policy_id=pid,rule=policies[pid].key,selection=name,epoch=ep,
                avg_member_win=z.member_win_pct.mean(),avg_coins=z.selected_coins.mean(),avg_peak=z.peak_pct.mean(),
                whole_basket=z.all_same_exit_pct.mean(),edge=z.edge.mean(),p_month=paired_month_p(pd.Series(z.edge.to_numpy(),index=z.decision)),**stat))
    stats=pd.DataFrame(records);stats.to_csv(out/'alt_selection_folds.csv',index=False)
    grid=stats.pivot(index=['entry_id','policy_id','rule','selection'],columns='epoch',values=['n','mean','win','lcb','dd','months','edge']).reset_index()
    grid.columns=[x if y=='' else f'{x}{y}' for x,y in grid.columns]
    q=grid[(grid.selection!='all')&(grid.n0>=12)&(grid.n1>=12)&(grid.n0+grid.n1>=30)&(grid.n2>=10)&(grid.months2>=4)
           &(grid.mean0>0)&(grid.mean1>0)&(grid.mean2>0)&(grid.win2>=50)&(grid.dd2<=35)
           &(grid.n0*grid.win0+grid.n1*grid.win1>=50*(grid.n0+grid.n1))].copy()
    q['floor']=q[['lcb0','lcb1','lcb2']].min(axis=1)
    q['edge_floor']=q[['edge0','edge1','edge2']].min(axis=1)
    q.sort_values(['edge_floor','floor'],ascending=False).head(1).to_csv(out/'alt_selection_chosen.csv',index=False)


if __name__=='__main__':run()

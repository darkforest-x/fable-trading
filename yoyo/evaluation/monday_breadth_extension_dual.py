"""Use the original BTC price target and frequent BTC alerts with one unlevered position.

Each source keeps its frozen causal alerts. Pool admission reserves six days at
entry independent of future prices. Market and funding-adjusted limit orders use
the prior VectorBT implementation. Controls replay the actual exit policy at the
existing matched random times; case-source observations with no matches stay visible.
"""
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_exits as sim
from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation.monday_breadth_extension_pairs import EXP, PARENT
from yoyo.evaluation.monday_breadth_frequency_btc_target import cached_btc_frame
from yoyo.evaluation.monday_breadth_frequency_controls import _holm
from yoyo.evaluation.monday_breadth_opt_match_exits import paired_month_p
from yoyo.evaluation.monday_breadth_opt_selection import describe

OUT=EXP/'dual'
HOLD=pd.Timedelta(days=6)
CASE=study.CASE_ENTRY
FILL=['entry_time','exit_time','entry_open','net_pct','exit_vwap','net_points','reason','ambiguous']


def schedules(medium,target):
    med=medium[['decision','epoch']].assign(source='medium')
    case=target[['decision','epoch']].assign(source='case')
    both=pd.concat([case,med]).sort_values(['decision','source'],kind='stable')
    picked=[]
    after=pd.Timestamp.min.tz_localize('UTC')
    for row in both.itertuples():
        take=row.decision>=after
        picked.append(take)
        if take:
            after=row.decision+HOLD
    both['admitted']=picked
    both.to_csv(OUT/'admission.csv',index=False)
    return {'medium':med,'dual':both[both.admitted]}


def source_returns(events,targets,draws):
    base=[]
    for row in [(720,10,events),(5,30,targets)]:
        w,x,e=row
        raw=pd.read_csv(study.search.OUT/'payoffs'/f'event_outcomes_minutes{w}_wait{x}.csv.gz',
                        usecols=['decision','hold_hours','btc_entry_open','btc_exit_open','btc_net_return_pct'],
                        parse_dates=['decision'])
        raw=raw[raw.hold_hours.eq(144)&raw.decision.isin(e.decision)]
        base.append(raw.rename(columns={'decision':'entry_time','btc_entry_open':'entry_open',
                                      'btc_exit_open':'exit_vwap','btc_net_return_pct':'net_pct'}))
    if len(draws):
        base.append(draws.rename(columns={'random_entry':'entry_time_key','entry_price':'entry_open','exit_price':'exit_vwap'})
                    [['entry_time_key','entry_open','exit_vwap','net_pct']].rename(columns={'entry_time_key':'entry_time'}))
    b=pd.concat(base,ignore_index=True).drop_duplicates('entry_time')
    b['exit_time']=b.entry_time+HOLD
    b['net_points']=b.net_pct*b.entry_open/100
    b['reason']=1
    b['ambiguous']=0
    return b[FILL]


def all_fills(keys,base,frame,history):
    stop=sim.Policy('time',144,6)
    target=funding.funded_policies()[0]
    policy_id=sim.policies(True).index(stop)
    stop_fills=pd.read_parquet(study.CACHE/'BTCUSDT.parquet',filters=[('policy_id','=',policy_id)])
    take_fills=pd.read_parquet(funding.FUNDED_CACHE,filters=[('policy_id','=',0)])
    inc=pd.read_parquet(PARENT/'btc_target/incremental_fills/btc_funded_limit_incremental.parquet')
    take_fills=pd.concat([take_fills[FILL],inc[FILL]],ignore_index=True)
    all_returns=[]
    for name,policy,stored in [('time',sim.Policy('time',144,0),base),('stop',stop,stop_fills),('target',target,take_fills)]:
        known=stored[stored.entry_time.isin(keys)][FILL].drop_duplicates('entry_time')
        missing=keys.difference(known.entry_time)
        if len(missing):
            new=sim.simulate(frame,missing,[policy],batch_size=64)[FILL]
            known=pd.concat([known,new],ignore_index=True)
        if len(known)!=len(keys):
            raise ValueError(f'Missing BTC native fills for {name}: {keys.difference(known.entry_time)}')
        known=known.assign(policy_id=0,first_exit_time=known.exit_time,exit_legs=1)
        known['funding_points']=funding.charged(known,history,[policy])
        known['funded_points']=known.net_points-known.funding_points
        known['funded_pct']=known.funded_points/known.entry_open*100
        if not np.isfinite(known.funded_pct).all():
            raise ValueError(f'Missing funded payoff for {name}')
        known['policy']=name
        all_returns.append(known)
    return pd.concat(all_returns,ignore_index=True)


def marked_dd(group,frame):
    equity=high=1.
    worst=0.
    for row in group.sort_values('decision').itertuples():
        times=pd.date_range(row.decision,row.exit_time,freq='5min',inclusive='left') if row.exit_time>row.decision else []
        closes=frame.close.reindex(times).to_numpy()
        steps=np.r_[1.,1-.0005,closes/row.entry_open-.0005,1+row.net_pct/100]*equity
        worst=max(worst,np.max(1-steps/np.maximum.accumulate(np.maximum(steps,high))))
        high=max(high,steps.max())
        equity=steps[-1]
    return 100*worst


def run():
    OUT.mkdir(exist_ok=True,parents=True)
    book=pd.read_parquet(PARENT/'audit/fixed/all_rule_and_account_events.parquet')
    medium=book[book.candidate_id.eq('freq36_BTC')]
    target=pd.read_parquet(PARENT/'btc_target/threshold_events.parquet').query('threshold_pct==5')
    clocks=schedules(medium,target)
    raw=pd.read_parquet(PARENT/'controls/fixed_matches.parquet',filters=[('candidate_id','=','freq36_BTC')])
    raw=raw.dropna(subset=['random_entry','net_pct']).copy()
    mm=raw[['decision','random_entry']].assign(source='medium')
    cm=pd.read_parquet(PARENT/'btc_target/matched_draws_2026.parquet')
    cm=cm[cm.decision.isin(target[target.epoch.eq(3)].decision)][['decision','random_entry']].assign(source='case')
    matches=pd.concat([mm,cm],ignore_index=True)
    keys=pd.DatetimeIndex(pd.concat([medium.decision,target.decision,matches.random_entry]).unique()).sort_values()
    frame,history,_=cached_btc_frame(keys)
    ledger=all_fills(keys,source_returns(medium,target,raw),frame,history)
    ledger.to_parquet(OUT/'native_fills.parquet',index=False)
    time_set=ledger[ledger.policy.eq('time')].set_index('entry_time')
    test=medium[medium.epoch.eq(3)].set_index('decision')
    np.testing.assert_allclose(time_set.loc[test.index].funded_pct,test.funded_pct,atol=1e-8)
    target_set=ledger[ledger.policy.eq('target')].set_index('entry_time')
    np.testing.assert_allclose(target_set.loc[target.decision].funded_points,target.funded_points,atol=1e-7)
    stats=[]; controls=[]; rows=[]; pairs=[]
    for clock,template in clocks.items():
        for policy in (['time','stop','target','source_specific'] if clock=='dual' else ['time','stop','target']):
            e=template[['decision','source','epoch']].copy()
            name=f'{clock}_{policy}'
            e['policy']=np.where(e.source.eq('case'),'target','time') if policy=='source_specific' else policy
            e=e.merge(ledger,left_on=['decision','policy'],right_on=['entry_time','policy'],validate='many_to_one')
            if len(e)!=len(template):raise ValueError('Incomplete schedule')
            e['strategy']=name
            rows.append(e)
            for period,g in [('all',e),*[(str(k),h) for k,h in e.groupby('epoch')]]:
                s=describe(g.assign(net_pct=g.funded_pct))
                g2=g[g.decision.dt.strftime('%Y-%m').ne('2026-09')]
                stats.append(dict(strategy=name,period=period,**s,marked_dd=marked_dd(g,frame) if period=='3' else np.nan,
                                  without_september_mean=g2.funded_pct.mean(),
                                  stress_growth=100*((1+(g.funded_pct-.2)/100).prod()-1),
                                  target_hits=int((g.funded_points>=10000).sum())))
            t=e[e.epoch.eq(3)]
            joined=t[['decision','source','policy','funded_pct']].merge(matches,on=['source','decision'])
            joined=joined.merge(ledger[['entry_time','policy','funded_pct']].rename(columns={'funded_pct':'random'}),
                                left_on=['random_entry','policy'],right_on=['entry_time','policy'],validate='many_to_one')
            paired=joined.groupby('decision').agg(actual=('funded_pct','first'),random=('random','mean'),draws=('random','size'))
            paired['edge']=paired.actual-paired.random
            paired['strategy']=name
            pairs.append(paired.reset_index())
            controls.append(dict(strategy=name,n=len(t),pairs=len(paired),matched_months=paired.index.strftime('%Y-%m').nunique(),
                                 actual=paired.actual.mean(),random=paired.random.mean(),edge=paired.edge.mean(),p=paired_month_p(paired.edge)))
    ctrl=pd.DataFrame(controls)
    ctrl['holm']=_holm(ctrl.p,family_size=len(ctrl))
    ctrl.to_csv(OUT/'controls.csv',index=False)
    pd.concat(rows,ignore_index=True).to_parquet(OUT/'events.parquet',index=False)
    pd.concat(pairs,ignore_index=True).to_csv(OUT/'matched_2026.csv',index=False)
    pd.DataFrame(stats).to_csv(OUT/'periods.csv',index=False)


if __name__=='__main__':
    run()

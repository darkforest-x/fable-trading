"""Change only target distance on frozen first hourly reclaims and their paired controls."""
import json
import numpy as np
import pandas as pd

from yoyo.evaluation.month_first_week_alt_entries import DEST, ROOT, prices, universe
from yoyo.evaluation.month_first_week_alt_trades import run_month_first_week_alt_trades
from yoyo.evaluation.month_first_week_alt_summary import group_stats, month_interval

OUT=ROOT/'experiments/active/exp-alt-month-first-week-near-target-20261110-v1'


def main():
    config=json.loads((OUT/'config.json').read_text())
    baseline=pd.read_csv(DEST/'position_ledger.csv.gz',parse_dates=['entry_time','touch'])
    baseline=baseline[baseline.entry_rule.eq('reclaim_1h')].copy()
    columns=['trade_id','symbol','side','entry_time','touch','high','low','level','entry_rule',
             'month','cohort','event_id','paired_id','is_control','entry_fill']
    grouped=baseline[columns].groupby('symbol')
    changed=[]
    for spec in universe().itertuples():
        if spec.symbol not in grouped.groups:
            continue
        base=grouped.get_group(spec.symbol)
        batch=[]
        for cap in config['target_distance_caps_pct']:
            x=base.copy()
            x['cap']=str(cap)
            x['trade_id']=x.trade_id+'|cap'+str(cap)
            x['paired_id']=x.paired_id+'|cap'+str(cap)
            x['low']=np.minimum(x.low,x.entry_fill*(1+cap/100))
            x['high']=np.maximum(x.high,x.entry_fill*(1-cap/100))
            batch.append(x)
        changed.append(run_month_first_week_alt_trades({spec.symbol:prices(spec)},pd.concat(batch)))
    baseline['cap']='near_edge'
    trades=pd.concat([baseline,pd.concat(changed)],ignore_index=True)
    trades['exit_time']=pd.to_datetime(trades.exit_time,utc=True,format='mixed')
    assert trades.trade_id.is_unique and len(trades)==3*len(baseline) and trades.status.eq('closed').all()
    trades['period']=np.where(trades.entry_time>=pd.Timestamp('2025-01-01T00:00Z'),'later',
        np.where(trades.exit_time<pd.Timestamp('2025-01-01T00:00Z'),'early','cross_split'))
    trades['stress_net_pct']=trades.net_return_pct-.05*(1+(trades.exit_reason!='target')*trades.exit_price/trades.entry_fill)
    trades[['trade_id','paired_id','symbol','month','side','cap','is_control','entry_time','entry_fill',
        'target_price','stop_price','exit_time','exit_reason','exit_price','net_return_pct','fees_pct',
        'mfe_pct','mfe_time','mae_pct','mae_time','mfe_pct_optimistic_upper_bound','ambiguous_sl_tp',
        'period','stress_net_pct']].to_csv(OUT/'trades.csv.gz',index=False)
    signal=trades[~trades.is_control]
    paired=signal.merge(trades[trades.is_control][['paired_id','period','net_return_pct']],on='paired_id',
                        suffixes=('','_random'),validate='1:1')
    paired['delta']=paired.net_return_pct-paired.net_return_pct_random
    rows=[]
    for period in ['early','later','all']:
        for (cap,side),g in pd.concat([paired,paired.assign(side='both')]).groupby(['cap','side']):
            if period!='all':
                g=g[g.period==period]
            ctl=g if period=='all' else g[g.period_random==period]
            lo,hi=month_interval(ctl)
            rows.append(dict(period=period,cap=cap,side=side,**group_stats(g),
                random_n=len(ctl),random_mean_pct=ctl.net_return_pct_random.mean(),
                excess_pp=ctl.delta.mean(),excess_ci_low=lo,excess_ci_high=hi))
    stats=pd.DataFrame(rows)
    stats.to_csv(OUT/'target_metrics.csv',index=False)
    pick=stats.query("period=='early' and side=='both'").nlargest(1,'mean_net_pct').iloc[0].cap
    result=dict(selected_on_early=pick,comparisons=stats[(stats.period=='later')&(stats.side=='both')].to_dict('records'))
    (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()

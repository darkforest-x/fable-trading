"""Test the original funded BTC exit and its three share-threshold sensitivities.

Each random entry uses the identical prior-month/time/volatility matcher as the
frequency study, then the same funding-adjusted native VectorBT limit and stop.
Repeated entry times across thresholds are simulated and charged only once.
"""
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_frequency_controls as controls
from yoyo.evaluation import monday_breadth_opt_controls as matcher
from yoyo.evaluation import monday_breadth_opt_exits as native
from yoyo.evaluation import monday_breadth_opt_funding as fund
from yoyo.evaluation.monday_breadth_frequency_long import CACHE, EXP
from yoyo.evaluation.monday_breadth_opt_match_exits import paired_month_p


def payoff(times,incremental):
    policy = fund.funded_policies()[0]
    if policy.kind!='btc_funded_limit' or policy.stop_pct!=6 or policy.hours!=144:
        raise ValueError('Changed BTC case policy')
    keys = pd.DatetimeIndex(times).unique().sort_values()
    path = CACHE.parent/'btc_target_control_fills.parquet'
    stored = pd.read_parquet(fund.FUNDED_CACHE,filters=[('policy_id','=',0)])
    reuse = [stored[['entry_time','funded_pct']], incremental[['decision','funded_pct']].rename(columns={'decision':'entry_time'})]
    if path.is_file():
        reuse.append(pd.read_parquet(path))
    old = pd.concat(reuse,ignore_index=True).drop_duplicates('entry_time')
    missing = keys.difference(pd.DatetimeIndex(old.entry_time))
    if len(missing):
        frame, history = fund.btc_frame(missing)
        fills = native.simulate(frame,missing,[policy],batch_size=32)
        fills['funded_pct'] = 100*(fills.net_points-fund.charged(fills,history,[policy]))/fills.entry_open
        local = fills[['entry_time','funded_pct']]
        if path.is_file():
            local = pd.concat([pd.read_parquet(path),local],ignore_index=True).drop_duplicates('entry_time')
        local.to_parquet(path,index=False)
        old = pd.concat([old,local],ignore_index=True).drop_duplicates('entry_time')
    return old.set_index('entry_time').funded_pct.reindex(keys)


def run():
    target = EXP/'btc_target'
    source = pd.read_parquet(target/'threshold_events.parquet')
    s = source[source.epoch.eq(3)].copy()
    # Matching coordinates are independent of the threshold, which is evaluated
    # on the actual breadth signal. Share overlapping control draws across rules.
    unique = s.sort_values('decision').drop_duplicates('decision').copy()
    unique['candidate_id']='funded_BTC'
    unique['asset']='BTC'
    unique['role']='case_target'
    unique['source']='exits'
    unique['policy_id']=1000
    unique['rule_event_id']=unique.decision.astype(str)
    unique['actual_trade_id']=unique.rule_event_id
    unique['minutes']=5
    unique['wait_min']=30
    unique['hold_hours']=144
    unique['epoch_rank']=3
    tasks, meta = matcher._build_task_rows(unique,unique,matcher._signal_lookup(),{})
    matches, coverage, _ = controls._collect_matches(tasks,'btc_target')
    base = s[['threshold_pct','decision','funded_pct']].copy()
    if len(matches):
        random = payoff(matches.random_entry,source)
        matches['random_funded_pct']=matches.random_entry.map(random)
        matches[['decision','random_entry','random_funded_pct']].to_parquet(target/'matched_draws_2026.parquet',index=False)
        available = matches.groupby('decision').random_funded_pct.agg(['mean','count'])
        base = base.join(available.rename(columns={'mean':'random_funded_pct','count':'draws'}),on='decision')
    else:
        base['random_funded_pct']=np.nan
        base['draws']=0
    if len(coverage):
        base = base.merge(coverage[['decision','pool_n','symbol_status']],on='decision',validate='many_to_one')
    base['draws']=base.draws.fillna(0).astype(int)
    base['difference_pp']=base.funded_pct-base.random_funded_pct
    if len(base)!=len(s) or base.duplicated(['threshold_pct','decision']).any():
        raise ValueError('BTC control coverage does not match locked events')
    base.to_csv(target/'paired_events_2026.csv',index=False)
    rows=[]
    for threshold,g in base.groupby('threshold_pct',sort=False):
        paired=g.dropna(subset=['difference_pp']).set_index('decision')
        rows.append(dict(threshold_pct=threshold,total=len(g),paired=len(paired),months=paired.index.strftime('%Y-%m').nunique(),
                         matched_actual_funded_pct=paired.funded_pct.mean(),matched_random_funded_pct=paired.random_funded_pct.mean(),
                         edge_pp=paired.difference_pp.mean(),month_p=paired_month_p(paired.difference_pp)))
    result=pd.DataFrame(rows)
    result['holm_four']=controls._holm(result.month_p,family_size=4)
    result.to_csv(target/'matched_2026.csv',index=False)


if __name__=='__main__':
    run()

"""Descriptive capital split for the two owner objectives, without changing either signal.

At the beginning of the 2026 comparison allocate 50% to the original funded BTC
target and 50% to the unmodified single-position 36/year account. Each reinvests
its own equity independently. Net history is used only to price completed fills.
"""
from functools import lru_cache
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_funding as fund
from yoyo.evaluation import monday_breadth_opt_payoffs as quotes
from yoyo.evaluation.monday_breadth_frequency_long import EXP

SPLIT = pd.Timestamp('2026-01-01',tz='UTC')
BAR = pd.Timedelta(minutes=5)


def legs():
    a=pd.read_parquet(EXP/'audit/fixed/all_rule_and_account_events.parquet')
    a=a[a.candidate_id.eq('account_36')].copy()
    original=pd.read_parquet(study.OUT/'locked_events.parquet')
    original=original[original.candidate_id.eq('case_target_funded_BTC')]
    fills=pd.read_parquet(fund.FUNDED_CACHE,filters=[('policy_id','=',0)])
    b=original.drop(columns='net_pct').merge(fills,left_on='decision',right_on='entry_time',validate='one_to_one')
    if len(b)!=len(original) or not np.allclose(b.funded_pct,original.set_index('decision').loc[b.decision].net_pct):
        raise ValueError('Original funded BTC events were not reproduced')
    a['sleeve']='frequency'
    b['sleeve']='btc_target'
    b['source_id']=b.candidate_id
    return pd.concat([a,b],ignore_index=True)


def path_for_funding(group):
    # Each sleeve stays flat between positions. The last quote at each time is
    # authoritative, including exit/next-entry coincidences within that sleeve.
    vals={}
    fee_equity=funded_equity=1.
    for row in group.sort_values('decision').itertuples():
        vals[row.exit_time]=(fee_equity*(1+row.net_pct/100),funded_equity*(1+row.funded_pct/100))
        fee_equity,funded_equity=vals[row.exit_time]
    return pd.DataFrame.from_dict(vals,orient='index',columns=['fee','funded']).sort_index()


def close_mark_path(group,bridge,begin,end):
    @lru_cache(None)
    def prices(symbol):
        return quotes.load_full_ohlc(symbol,begin,end,repair_missing=False)[0][['open','close']]

    marks=pd.Series(np.nan,index=pd.date_range(begin,end,freq='5min'),dtype=float)
    resolved=1.
    marks.iloc[0]=1.
    for row in group.sort_values('decision').itertuples():
        members=bridge[(row.minutes,row.known)] if row.asset=='ALT' else [row.asset+'USDT']
        times=pd.date_range(row.decision,row.exit_time,freq='5min',inclusive='left')
        path=np.zeros(len(times))
        for sym in members:
            quote=prices(sym)
            path+=quote.close.reindex(times).to_numpy()/quote.open.loc[row.decision]/len(members)
        if not np.isfinite(path).all():raise ValueError('Missing quote in sleeve marking path')
        # Treat each 5m close as observed at the next candle boundary. The exit
        # fill supersedes the same-time pre-exit close in this snapshot series.
        marks.loc[times+BAR]=resolved*(path-.0005)
        resolved*=1+row.net_pct/100
        marks.loc[row.exit_time]=resolved
    return marks.ffill()


def run():
    all_legs=legs()
    event_cols=['sleeve','source_id','candidate_id','asset','decision','exit_time','epoch','net_pct','funded_pct','funded_points']
    all_legs[event_cols].sort_values('decision').to_parquet(EXP/'two_sleeve_events.parquet',index=False)
    periods=[]
    for year,cohort in all_legs.groupby(all_legs.decision.dt.year):
        overlaps=0
        for r in cohort.itertuples():
            other=all_legs[all_legs.sleeve.ne(r.sleeve)]
            overlaps+=int(((other.decision<=r.decision)&(other.exit_time>r.decision)).any())
        row=dict(year=year,frequency_entries=int(cohort.sleeve.eq('frequency').sum()),
                 target_entries=int(cohort.sleeve.eq('btc_target').sum()),
                 distinct_entry_times=cohort.decision.nunique(),entries_while_other_sleeve_open=overlaps)
        if year==2026:
            if cohort.funded_pct.isna().any():raise ValueError('Complete 2026 funded fills required')
            end=cohort.exit_time.max()
            bridge=study.member_bridge().groupby(['minutes','known']).symbol.agg(list).to_dict()
            cash=pd.concat({name:path_for_funding(g) for name,g in cohort.groupby('sleeve')},axis=1).sort_index().ffill().fillna(1.)
            value=cash.xs('funded',axis=1,level=1).mean(axis=1)
            row['two_sleeve_terminal_return_funded_pct']=100*(value.iloc[-1]-1)
            value=pd.concat([pd.Series([1.],index=[SPLIT]),value])
            row['settled_dd_funded_pct']=100*(1-value/value.cummax()).max()
            close=pd.concat({name:close_mark_path(g,bridge,SPLIT,end) for name,g in cohort.groupby('sleeve')},axis=1)
            combined=close.mean(axis=1)
            row['five_min_close_dd_before_funding_pct']=100*(1-combined/combined.cummax()).max()
            if not np.isclose(combined.iloc[-1],cash.xs('fee',axis=1,level=1).iloc[-1].mean()):
                raise ValueError('Marked end value differs from both settled sleeves')
            for sleeve,v in cash.xs('funded',axis=1,level=1).iloc[-1].items():
                row[sleeve+'_terminal_funded_pct']=100*(v-1)
            target=cohort[cohort.sleeve.eq('btc_target') & cohort.decision.eq(study.CASE_ENTRY)].iloc[0]
            row['sept16_per_btc_funded_points']=target.funded_points
        periods.append(row)
    pd.DataFrame(periods).to_csv(EXP/'two_sleeve_counts.csv',index=False)


if __name__=='__main__':
    run()

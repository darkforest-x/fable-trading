"""Allocate one account to pairs of frozen medium-frequency VectorBT signals.

No trades are repriced. Admission only sees signal time and the preset holding
period. Equal-sleeve equity uses contemporaneous 5m closes of the original ALT
members and BTC, never their future highs or an average of individual drawdowns.
"""
from itertools import combinations

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_frequency_audit as audit
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_payoffs as quotes
from yoyo.evaluation.monday_breadth_frequency_long import EXP as PARENT
from yoyo.evaluation.monday_breadth_frequency_controls import _holm
from yoyo.evaluation.monday_breadth_opt_match_exits import paired_month_p
from yoyo.evaluation.monday_breadth_opt_selection import describe

EXP = PARENT.parent/'exp-monday-breadth-extension-20261108-v1'
OUT = EXP/'pairs'
ASSETS = ['BTC','ETH','SOL','ALT']


def summarize(group):
    row = audit.stats(group)
    if group.funded_pct.notna().all():
        funded = group.funded_pct
        row['funded_growth'] = 100*((1+funded/100).prod()-1)
        row['funded_growth_stress'] = 100*((1+(funded-.2)/100).prod()-1)
        row['funded_ex_best'] = funded.drop(funded.idxmax()).mean()
    return row


def evidence(book, controls):
    test = book[book.epoch.eq(3)][['pair','source_id','decision']]
    events = test.merge(controls, on=['source_id','decision'],validate='many_to_one')
    if len(events)!=len(test):
        raise ValueError('Matched-control ledger lost an event')
    results = []
    for name,group in events.groupby('pair'):
        matched = group[group.eligible_coverage].set_index('decision')
        delta = matched.matched_actual_pct-matched.matched_random_pct
        results.append(dict(pair=name,total=len(group),matched=len(matched),
                            months=matched.index.strftime('%Y-%m').nunique(),
                            actual=matched.matched_actual_pct.mean(),random=matched.matched_random_pct.mean(),
                            edge=delta.mean(),p=paired_month_p(delta)))
    return events,pd.DataFrame(results)


def marked_legs(singles):
    """Mark steps with member open/close only in each already accepted holding interval."""
    bridge = study.member_bridge()
    alt = singles[singles.asset.eq('ALT')].merge(bridge,on=['minutes','known'])
    btc = singles[singles.asset.eq('BTC')].assign(symbol='BTCUSDT')
    legs = pd.concat([alt,btc],ignore_index=True)
    counts = legs.groupby(['decision','asset']).symbol.nunique()
    values = {}
    for symbol,g in legs.groupby('symbol'):
        q,_ = quotes.load_full_ohlc(symbol,g.decision.min(),g.exit_time.max(),repair_missing=False)
        for row in g.itertuples():
            key = (row.decision,row.asset)
            times = pd.date_range(row.decision,row.exit_time,freq='5min',inclusive='left')
            unit = q.close.reindex(times).to_numpy()/q.open.loc[row.decision]
            if not np.isfinite(unit).all():
                raise ValueError(f'Missing mark for {symbol} {row.decision}')
            if key not in values:
                values[key] = pd.Series(0.,index=times+pd.Timedelta(minutes=5))
            values[key] += unit/counts.loc[key]
    return values


def split_account(singles):
    test = singles[singles.epoch.eq(3)].copy()
    groups = test.groupby('asset')
    begin = pd.Timestamp('2026-01-01',tz='UTC')
    grid = pd.date_range(begin,test.exit_time.max(),freq='5min')
    paths = marked_legs(test)
    curves = pd.DataFrame(index=grid)
    for asset,g in groups:
        fee_equity = 1.
        values = pd.Series(np.nan,index=grid)
        values.iloc[0]=1.
        for row in g.sort_values('decision').itertuples():
            marks = paths[(row.decision,asset)]
            values.loc[marks.index] = fee_equity*(marks-.0005)
            fee_equity *= 1+row.net_pct/100
            values.loc[row.exit_time] = fee_equity
        curves[asset]=values.ffill()
        if not np.isclose(curves[asset].iloc[-1],(1+g.net_pct/100).prod()):
            raise ValueError('Sleeve end value disagrees with native fills')
    curves['equal_capital']=curves.mean(axis=1)
    funded = groups.funded_pct.agg(lambda z:(1+z/100).prod())
    stress = groups.funded_pct.agg(lambda z:(1+(z-.2)/100).prod())
    monthly = test.assign(month=test.decision.dt.strftime('%Y-%m')).groupby('month').decision.nunique()
    out = dict(distinct=test.decision.nunique(),legs=len(test),months=len(monthly),min_month=int(monthly.min()),
               max_month=int(monthly.max()),funded_growth=100*(funded.mean()-1),
               funded_growth_stress=100*(stress.mean()-1),marked_close_dd=100*(1-curves.equal_capital/curves.equal_capital.cummax()).max())
    curves.resample('1D').last().to_csv(OUT/'equal_sleeves_daily_close.csv',index_label='utc_day')
    pd.DataFrame([out]).to_csv(OUT/'equal_sleeves_2026.csv',index=False)


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    rules = pd.read_csv(PARENT/'fixed/locked_rules.csv')
    original = pd.read_parquet(PARENT/'audit/fixed/all_rule_and_account_events.parquet')
    streams = original[original.candidate_id.str.startswith('freq36_')].copy().reset_index(drop=True)
    results=[]
    summaries=[]
    for pair in combinations(ASSETS,2):
        ids = ['freq36_'+asset for asset in pair]
        subset = rules[rules.candidate_id.isin(ids)]
        book,_ = audit.shared_account(subset,streams)
        book['pair'] = '+'.join(pair)
        if (book.decision.diff().dropna()<pd.Timedelta(hours=24)).any() or (book.decision<book.exit_time.shift()).any():
            raise ValueError('Overlapping account admissions')
        results.append(book)
        for epoch,g in book.groupby('epoch'):
            summaries.append(dict(pair='+'.join(pair),epoch=epoch,**summarize(g)))
    book = pd.concat(results,ignore_index=True)
    summary = pd.DataFrame(summaries)
    dev = []
    for name,g in summary[summary.epoch.lt(3)].groupby('pair'):
        g=g.set_index('epoch')
        frequency = g.at[0,'n']>=50 and g.at[1,'n']>=72 and g.at[2,'n']>=36 and g.at[2,'months']>=8
        dev.append(dict(pair=name,frequency=frequency,risk_ok=g.at[2,'dd']<=35,stress_ok=g.stress.min()>0,
                        worst_lcb=g.lcb.min(),mean_2025=g.at[2,'mean']))
    dev=pd.DataFrame(dev).sort_values(['worst_lcb','mean_2025','pair'],ascending=[False,False,True])
    dev['eligible']=dev.frequency & dev.risk_ok & dev.stress_ok
    dev['selected']=False
    if dev.eligible.any():
        dev.loc[dev[dev.eligible].index[0],'selected']=True
    dev.to_csv(OUT/'development.csv',index=False)
    ctrl = pd.read_parquet(PARENT/'controls/fixed_paired_events.parquet').rename(columns={'candidate_id':'source_id'})
    paired,stat = evidence(book,ctrl)
    stat['p_holm_six']=_holm(stat.p,family_size=6)
    stat.to_csv(OUT/'controls.csv',index=False)
    paired[['pair','source_id','decision','eligible_coverage','matched_actual_pct','matched_random_pct']].to_parquet(OUT/'paired_events.parquet',index=False)
    summary.to_csv(OUT/'periods.csv',index=False)
    book.to_parquet(OUT/'events.parquet',index=False)
    split_account(streams[streams.asset.isin(['BTC','ALT'])])


if __name__=='__main__':
    run()

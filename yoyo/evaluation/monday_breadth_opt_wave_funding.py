"""Charge observed settlement rates to both exits on each identical second-wave position."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import argparse

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.data.altseason_funding import FundingClient, collect


def repair_months():
    frame=pd.read_parquet(study.OUT/'second_wave_member_funding.parquet')
    missing=frame[frame.wave_funding.isna()|frame.fixed_funding.isna()]
    clients={}
    for row in missing.itertuples():
        end=row.decision+pd.Timedelta(days=6)
        for month in sorted({row.decision.strftime('%Y-%m'),end.strftime('%Y-%m')}):
            root=funding.ROOT/'funding_months'/month
            if month not in clients:
                clients[month]=FundingClient('binance',root)
            start=pd.Timestamp(month+'-01',tz='UTC')
            stop=start+pd.offsets.MonthBegin()
            try:
                collect(clients[month],row.symbol,start.value//10**6,stop.value//10**6)
            except (OSError,ValueError,RuntimeError) as exc:
                print(f'Funding lookup unavailable: {row.symbol} {month} {exc}',flush=True)


def charge_symbol(pair):
    symbol, trades = pair
    trades = trades.copy()
    prices = pd.read_parquet(study.CACHE/(symbol+'.parquet'), columns=['entry_time','entry_open','peak_pct'],
                             filters=[('policy_id','=',5)]).set_index('entry_time')
    trades['entry_open'] = prices.entry_open.reindex(trades.decision).to_numpy()
    trades['six_day_peak'] = prices.peak_pct.reindex(trades.decision).to_numpy()
    if 'case_open' in trades:
        trades['entry_open']=trades.entry_open.fillna(trades.case_open)
    if trades.entry_open.isna().any():
        raise ValueError(f'Missing existing VectorBT market fill for {symbol}')
    trades['entry_time'] = trades.decision
    trades['policy_id'] = 0
    trades['exit_legs'] = 1
    trades['full_end'] = trades.decision + pd.Timedelta(days=6)
    trades['month_range'] = list(zip(trades.decision.dt.strftime('%Y-%m'),trades.full_end.dt.strftime('%Y-%m')))
    parts = []
    for months, group in trades.groupby('month_range'):
        group = group.copy()
        history = funding.rates(symbol,months=tuple(sorted(set(months))))
        group['archive_mark'] = bool(not history.empty and history.price_proxy.any())
        planned=group.exit_time if 'exit_time' in group else group.decision+pd.to_timedelta(group.exit_h,unit='h')
        for name, end in [('wave',planned),('fixed',group.full_end)]:
            group['exit_time'] = end
            group['first_exit_time'] = end
            points = funding.charged(group,history,{0:SimpleNamespace(take_fraction=0)})
            group[name+'_funding'] = 100 * points / group.entry_open
        parts.append(group)
    return pd.concat(parts,ignore_index=True)


def run():
    out=study.OUT
    req=out/'second_wave_requests'
    events=pd.read_parquet(req/'locked_events.parquet')
    rules=pd.read_csv(req/'locked_rules.csv')
    events=events[events.epoch.eq(3)].merge(rules[['candidate_id','minutes']],on='candidate_id',validate='many_to_one')
    alt=events[events.asset.eq('ALT')].merge(study.member_bridge(),on=['minutes','known'])
    index=events[~events.asset.eq('ALT')].assign(symbol=lambda z:z.asset+'USDT')
    positions=pd.concat([alt,index],ignore_index=True)
    groups=list(positions.groupby('symbol'))
    all_parts=[]
    with ThreadPoolExecutor(8) as pool:
        for i, part in enumerate(pool.map(charge_symbol,groups),1):
            all_parts.append(part)
            if i%100==0: print(f'Funded {i}/{len(groups)} symbols',flush=True)
    members=pd.concat(all_parts,ignore_index=True)
    members[['symbol','asset','decision','exit_h','entry_open','six_day_peak','archive_mark','wave_funding','fixed_funding']].to_parquet(
        out/'second_wave_member_funding.parquet',index=False)
    results=[]
    for (asset,decision),group in members.groupby(['asset','decision']):
        row=group.iloc[0]
        ok=group[['wave_funding','fixed_funding']].notna().all(axis=1)
        results.append(dict(asset=asset,decision=decision,expected=len(group),funded_members=int(ok.sum()),
            members_proxy=int(group.archive_mark.sum()),six_day_member_peak=group.six_day_peak.mean(),
            wave_funded=row.net_pct-group.wave_funding.mean() if ok.all() else np.nan,
            fixed_funded=row.baseline_pct-group.fixed_funding.mean() if ok.all() else np.nan,exit_h=row.exit_h))
    frame=pd.DataFrame(results)
    frame.to_csv(out/'second_wave_funding_events.csv',index=False)
    frame.groupby('asset').agg(expected=('decision','size'),covered=('wave_funded','count'),
        wave_funded=('wave_funded','mean'),fixed_funded=('fixed_funded','mean'),
        wave_win=('wave_funded',lambda v:100*v.dropna().gt(0).mean()),
        mean_member_peak=('six_day_member_peak','mean')).to_csv(out/'second_wave_funding.csv')


def basket_funding():
    out=study.OUT
    rules=pd.read_csv(out/'basket_trail_frozen_rules.csv')
    events=pd.read_csv(out/'basket_trail_candidate_events.csv.gz',parse_dates=['decision','exit_time'])
    e=events[events.epoch.eq(3)].merge(rules[['cohort','config_id']],on=['cohort','config_id'],validate='many_to_one')
    members=pd.read_csv(out/'basket_trail_members.csv.gz',usecols=['event_id','cohort','symbol'])
    case=pd.read_csv(out/'basket_trail_sep16.csv',parse_dates=['decision_entry_time_utc','exit_time_utc'])
    case=case.rename(columns={'decision_entry_time_utc':'decision','exit_time_utc':'exit_time','basket_trail_net_pct':'basket_net_pct',
                              'same_68_fixed6d_net_pct':'fixed6d_net_pct'})
    case['event_id']=members.loc[members.cohort.eq('sept16_original_68'),'event_id'].iloc[0]
    case['cohort']='sept16_'+case.cohort_rule_source
    cols=['event_id','cohort','config_id','decision','exit_time','basket_net_pct','fixed6d_net_pct']
    full=pd.concat([e[cols],case[cols]],ignore_index=True)
    full=full.merge(members[['event_id','symbol']],on='event_id',validate='many_to_many')
    entry=pd.read_parquet(out/'sept16_original_funded.parquet',filters=[('policy_id','=',5)],columns=['asset','entry_open']).set_index('asset')
    full['case_open']=full.symbol.map(entry.entry_open).where(full.decision.eq(study.CASE_ENTRY))
    full['exit_h']=(full.exit_time-full.decision).dt.total_seconds()/3600
    with ThreadPoolExecutor(6) as pool:
        priced=pd.concat(pool.map(charge_symbol,full.groupby('symbol')),ignore_index=True)
    summary=[]
    for (cohort,decision),g in priced.groupby(['cohort','decision']):
        ok=g[['wave_funding','fixed_funding']].notna().all(axis=1)
        summary.append(dict(cohort=cohort,decision=decision,expected=len(g),covered=int(ok.sum()),
            basket_funded=g.basket_net_pct.iloc[0]-g.wave_funding.mean() if ok.all() else np.nan,
            fixed_funded=g.fixed6d_net_pct.iloc[0]-g.fixed_funding.mean() if ok.all() else np.nan))
    rows=pd.DataFrame(summary)
    rows.to_csv(out/'synchronized_funding_events.csv',index=False)
    rows.groupby('cohort').agg(expected=('decision','size'),covered=('basket_funded','count'),
        basket_funded=('basket_funded','mean'),fixed_funded=('fixed_funded','mean'),
        basket_win=('basket_funded',lambda v:100*v.dropna().gt(0).mean())).to_csv(out/'synchronized_funding.csv')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repair-months',action='store_true')
    parser.add_argument('--basket',action='store_true')
    args=parser.parse_args()
    if args.basket:basket_funding()
    else:
        if args.repair_months:repair_months()
        run()

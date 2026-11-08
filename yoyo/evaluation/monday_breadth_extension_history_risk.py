"""Observed historical drawdown lower bound at instants both BTC and ALT are flat.

Compound every native fee-only position within its sleeve. During overlapping open
positions omit the portfolio observation rather than fill unknown marks with cash.
"""
import numpy as np
import pandas as pd

from yoyo.evaluation.monday_breadth_extension_pairs import EXP,PARENT


def calculate(book,slip):
    events=[]
    for asset,g in book.groupby('asset'):
        prior=pd.NaT
        for r in g.sort_values('decision').itertuples():
            if pd.notna(prior) and r.decision<prior:
                raise ValueError('Same-sleeve overlapping trades')
            events.extend([(r.decision,1,asset,0.),(r.exit_time,0,asset,(r.net_pct-slip)/100)])
            prior=r.exit_time
    wealth={'BTC':.5,'ALT':.5}
    opened=set()
    best=1.
    high_date=min(e[0] for e in events)
    risk=(0.,high_date,high_date)
    flat=0
    for clock,entry,asset,outcome in sorted(events):
        if entry:
            opened.add(asset)
            continue
        opened.remove(asset)
        wealth[asset] *= 1+outcome
        if opened:
            continue
        flat+=1
        equity=sum(wealth.values())
        if equity>=best:
            best,high_date=equity,clock
        if 1-equity/best>risk[0]:
            risk=(1-equity/best,high_date,clock)
    return dict(n_entries=len(book),flat_observations=flat,fee_only_growth=100*(sum(wealth.values())-1),
                min_observed_dd_pct=100*risk[0],peak_time=risk[1],trough_time=risk[2])


def run():
    parent=pd.read_parquet(PARENT/'audit/fixed/all_rule_and_account_events.parquet')
    alt=parent[parent.candidate_id.eq('freq36_ALT')]
    medium=parent[parent.candidate_id.eq('freq36_BTC')]
    partial=pd.read_parquet(EXP/'partial/events.parquet').assign(asset='BTC')
    rows=[]
    for strategy,btc in [('medium_time',medium),*partial.groupby('exit_policy')]:
        book=pd.concat([btc,alt],ignore_index=True)
        for period,b in [('all',book),*[(str(y),g) for y,g in book.groupby(book.decision.dt.year)]]:
            for slip in (0.,.2):
                rows.append(dict(strategy=strategy,period=period,extra_roundtrip_pp=slip,**calculate(b,slip)))
    pd.DataFrame(rows).to_csv(EXP/'sleeves/history_flat_risk.csv',index=False)


if __name__=='__main__':
    run()

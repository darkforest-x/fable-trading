"""Prioritize fresh 12H reclaims using only already-paid peer profit targets in a frozen shadow ledger."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_cash_giveback as cash

queue=cash.queue
EXP=cash.ROOT/'exp-cloud-close-peer-target-wave-20261109-v1'


def target_fills():
    paths=pd.read_csv(cash.ROSTER/'selected_paths.csv.gz',dtype={'symbol':str})
    target=paths[paths.tp_r.eq(2)&paths.exit_reason.isin(['take_profit','take_profit_open'])].set_index('event_id')
    orders=pd.read_csv(cash.EXP/'orders_median.csv.gz')
    closed=orders[(orders.col%4==1)&orders.net_units.lt(0)&orders.stage.isin([1,3])]
    paid=closed[closed.underlying_event_id.isin(target.index)].copy()
    paid=paid.set_index('underlying_event_id',verify_integrity=True)
    aligned=target.loc[paid.index]
    if (not np.array_equal(paid.hour,aligned.exit_bar_ms) or
            not np.array_equal(paid.stage,np.where(aligned.exit_phase.eq('open'),1,3))):
        raise ValueError('2R target did not receive the corresponding native fill')
    paid=aligned[['symbol','side']].assign(available_ms=paid.hour+cash.H)
    return paid.sort_values('available_ms')


def score(pool):
    events=target_fills()
    events.to_csv(EXP/'observed_target_fills.csv.gz')
    opportunity=pd.read_csv(queue.history.EXP/'opportunities.csv.gz',index_col='event_id')
    info=opportunity.loc[pool.index,['entry_ms','symbol','side']].copy()
    if not info.entry_ms.equals(pool.entry_ms):
        raise ValueError('Mismatched candidate decision clock')
    t=events.available_ms.to_numpy()
    for name,delay in [('now',0),('stale',30*24*cash.H)]:
        votes=pd.Series(0,index=info.index,dtype=int)
        for clock,group in info.groupby('entry_ms'):
            end=int(clock)-delay
            a,b=t.searchsorted([end-7*24*cash.H,end],side='right')
            peers=events.iloc[a:b].drop_duplicates('symbol',keep='last').set_index('symbol').side
            votes.loc[group.index]=np.sign(peers.sum()-group.symbol.map(peers).fillna(0)).astype(int)
        info[name]=votes
    info.to_csv(EXP/'opportunity_votes.csv.gz')
    out=pool.copy()
    width=pool.groupby('entry_ms').balanced.transform('max')+1
    for name,signal,sign in [('follow','now',1),('fade','now',-1),('stale','stale',1)]:
        out[name]=pool.balanced+width*(sign*info[signal]*info.side<0).astype(int)
    return out


if __name__=='__main__':
    queue.main(experiment=EXP,policies=[(s,s) for s in ['follow','fade','stale']],score_builder=score)

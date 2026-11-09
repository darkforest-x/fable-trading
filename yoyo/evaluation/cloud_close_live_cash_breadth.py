"""Rank fresh 12H opportunities using other symbols' already-observed marked profits in a frozen paper book."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_cash_giveback as cash

queue=cash.queue
EXP=cash.ROOT/'exp-cloud-close-live-cash-breadth-20261109-v1'
LAG=30*24*cash.H


def marks():
    meta=pd.read_csv(cash.ROSTER/'selected_paths.csv.gz',dtype={'symbol':str})
    meta=meta[meta.tp_r.eq(0)].set_index('event_id')
    funds=pd.read_csv(cash.SOURCE/'aligned/funded_median.csv.gz',index_col='event_id')
    fills=pd.read_csv(cash.EXP/'orders_median.csv.gz',float_precision='round_trip')
    quotes,_=queue.repair.checked_quotes()
    blocks=[]
    def save(row,net):
        if (net.index<=row.entry_ms).any():
            raise ValueError('Future or same-open cash vote')
        blocks.append(pd.DataFrame(dict(clock=net.index,symbol=row.symbol,side=row.side,
                       event_id=row.name,net_exit_value=net.to_numpy(),vote=row.side*np.sign(net.to_numpy()))))
    for eid,orders in fills.groupby('underlying_event_id',sort=False):
        cash.parent_marks(orders,meta.loc[eid],funds.loc[eid],quotes,0.,live_sink=save)
    votes=pd.concat(blocks,ignore_index=True)
    if votes.duplicated(['clock','symbol']).any():
        raise ValueError('A contract has more than one vote at a closed clock')
    return votes


def scores(pool):
    votes=marks()
    peer=votes.set_index(['clock','symbol']).vote
    total=votes.groupby('clock').vote.agg(['sum','size'])
    opportunities=pd.read_csv(queue.history.EXP/'opportunities.csv.gz',index_col='event_id')
    panel=opportunities.loc[pool.index,['symbol','side','entry_ms']].copy()
    if not panel.entry_ms.equals(pool.entry_ms):
        raise ValueError('Opportunity prior clocks differ')
    for label,offset in [('now',0),('lag',LAG)]:
        clock=panel.entry_ms-offset
        own=peer.reindex(pd.MultiIndex.from_arrays([clock,panel.symbol]))
        count=clock.map(total['size']).fillna(0).to_numpy()-own.notna().to_numpy(dtype=int)
        net=clock.map(total['sum']).fillna(0).to_numpy()-own.fillna(0).to_numpy()
        panel[label+'_peers']=count
        panel[label+'_direction']=np.where(count>=2,np.sign(net),0)
    panel.to_csv(EXP/'opportunity_votes.csv.gz')
    votes.to_csv(EXP/'holding_votes.csv.gz',index=False)
    out=pool.copy()
    width=pool.groupby('entry_ms').balanced.transform('max')+1
    for label,source,direction in [('follow','now',1),('fade','now',-1),('lagged_follow','lag',1)]:
        opposed=(panel.side*panel[source+'_direction']*direction)<0
        out[label]=pool.balanced+width*opposed.astype(int)
    return out


if __name__=='__main__':
    queue.main(experiment=EXP,policies=[(s,s) for s in ['follow','fade','lagged_follow']],score_builder=scores)

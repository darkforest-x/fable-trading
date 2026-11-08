"""Add only on a fresh complete V1.7 key when every open same-symbol runner is profitable."""
import gc
import json
import zlib

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as split


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-pyramid-20261109-v1'
SLOTS=9


def admit(runner,cap,slip):
    ordered=runner.copy()
    ordered['priority']=ordered.symbol.map(lambda s:zlib.crc32(s.encode()))
    ordered=ordered.sort_values(['entry_ms','entry_order','priority','symbol','event_id'])
    end=np.full(SLOTS,-1,dtype='int64')
    coins=np.full(SLOTS,'',dtype=object)
    sides=np.zeros(SLOTS)
    basis=np.zeros(SLOTS)
    picks=[]
    audit=dict(additions=0,same_contract=0,all_slots_busy=0)
    for r in ordered.itertuples():
        busy=end>=r.entry_ms
        same=busy&(coins==r.symbol)
        if same.any():
            # Liquidate remaining quantity at this open for the profitability
            # check only. The original runner's quantity and exits stay intact.
            mark=r.entry_price*(1-r.side*slip)
            net=r.side*(mark-basis[same])-wallet.TAKER*(mark+basis[same])
            if same.sum()>=cap or np.any(sides[same]!=r.side) or np.any(net<=0):
                audit['same_contract']+=1
                continue
        free=np.flatnonzero(~busy)
        if not len(free):
            audit['all_slots_busy']+=1
            continue
        i=int(free[0])
        audit['additions']+=int(same.any())
        end[i],coins[i],sides[i],basis[i]=r.exit_bar_ms,r.symbol,r.side,r.entry_price*(1+r.side*slip)
        picks.append((r.Index,i))
    loc=pd.DataFrame(picks,columns=['row','slot']).set_index('row')
    return ordered.loc[loc.index].assign(slot=loc.slot),audit


def run(paths,cap,slip=0.):
    last,audit=admit(paths[paths.tp_r.eq(0)],cap,slip)
    early=paths[paths.tp_r.eq(2)].set_index('event_id',drop=False).loc[last.event_id].copy()
    if np.any(early.exit_bar_ms.to_numpy()>last.exit_bar_ms.to_numpy()):
        raise ValueError('Partial exit after runner')
    early['slot']=2*last.slot.to_numpy()
    last['slot']=2*last.slot+1
    legs=pd.concat([early,last],ignore_index=True)
    data=wallet.prepare(None,2*SLOTS,assigned=(legs,audit))
    return split.simulate(data,.75,slip),audit


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    parent=json.loads((leader.EXP/'summary.json').read_text())
    if parent['pick']!=1.:
        raise ValueError('Unexpected coverage parent')
    paths=wallet.read_paths()
    paths=paths[paths.tp_r.isin([0,2])].copy()
    paths=paths[pd.MultiIndex.from_frame(paths[['entry_ms','symbol']]).isin(leader.leadership().dropna().index)].copy()
    source=pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    source.index=pd.RangeIndex(1,len(source)+1)
    paths['entry_order']=paths.source_row_number.map(source.prior_atr_pct).fillna(np.inf)
    real=paths[~paths.is_control]
    ctl=paths[paths.is_control&paths.parent_id.isin(real.parent_id)]
    parity,_=admit(real[real.tp_r.eq(0)],1,0.)
    oldids=pd.read_csv(leader.EXP/'funded_1.0.csv.gz',usecols=['event_id']).event_id
    if len(oldids)!=len(parity) or set(oldids)!=set(parity.event_id):
        raise ValueError('Cap-one roster differs from frozen parent')
    base=pd.read_csv(leader.EXP/'account_metrics.csv')
    rows=base[base.top_fraction.eq(1)].drop(columns='top_fraction').assign(cap=1).to_dict('records')
    audits,samples={},{}
    for cap in cfg['cap'][1:]:
        (m,tr,eq),audits[cap]=run(real,cap)
        rows.extend(dict(cap=cap,period=p,**v) for p,v in m.items())
        samples[cap]=tr,eq
        gc.collect()
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.trades.ge(100)]
    pick=int(eligible.cap.iloc[0]) if len(eligible) else 1
    best=int(dev.cap.iloc[0])
    checks={}
    for cap in sorted({pick,best}-{1}):
        tr,eq=samples[cap]
        tr.to_csv(EXP/f'funded_{cap}.csv.gz')
        eq.to_csv(EXP/f'hourly_{cap}.csv.gz',index_label='hour_end_utc')
        checks[cap]={}
        for name,events,slip in [('control',ctl,0.),('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                 ('slippage_5bp',real,.0005)]:
            checks[cap][name]=run(events,cap,slip)[0][0]
            gc.collect()
    result=dict(pick=pick,best_dev_growth=best,eligible=eligible.cap.tolist(),checks=checks,audit=audits)
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()

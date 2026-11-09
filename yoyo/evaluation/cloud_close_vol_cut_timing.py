"""Delay the same stream of original options-volatility reductions; never spend one twice or prematurely."""
from collections import deque
import gc
import zlib

import pandas as pd

from yoyo.evaluation import cloud_close_option_vol_budget as vol

cash=vol.cash
EXP=vol.EXP.parent/'exp-cloud-close-vol-cut-timing-20261109-v1'


def assign(panel,delay):
    eligible=panel[panel.common_coverage & panel.positive_first_capacity & panel.original_leverage.eq(2)].copy()
    eligible['tie']=[zlib.crc32(k.encode()) for k in eligible.index]
    eligible=eligible.sort_values(['entry_ms','tie','symbol'])
    queue=deque()
    links=[]
    for t,group in eligible.groupby('entry_ms',sort=True):
        for eid in group.index:
            if not queue or queue[0][1]+delay>t:
                break
            before,clock=queue.popleft()
            links.append(dict(event_id=eid,entry_ms=int(t),source_clock_ms=int(clock),source_event=before))
        for eid in group.index[group.current_rv>group.current_iv]:
            queue.append((eid,int(t)))
    links=pd.DataFrame(links)
    if (links.event_id.duplicated().any() or links.source_event.duplicated().any()
            or not (links.entry_ms-links.source_clock_ms>=delay).all()
            or len(links)+len(queue)!=int((eligible.current_rv>eligible.current_iv).sum())):
        raise ValueError('Causal volatility reductions lost exclusivity, chronology or token conservation')
    return links,list(queue)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed reduction timing controls')
    data,market,quotes,parents,caps,ratios=cash.account()
    panel=pd.read_csv(vol.EXP/'decisions.csv.gz',index_col='event_id')
    if set(panel.index)!=set(parents.index) or not panel.loc[parents.index,'entry_ms'].equals(parents.entry_ms):
        raise ValueError('Options source roster changed')
    results={}
    for name,delay in [('next_12h',cash.STEP),('after_30d',60*cash.STEP)]:
        links,pending=assign(panel,delay)
        links.to_csv(EXP/f'reductions_{name}.csv.gz',index=False)
        leverage=ratios.copy()
        leverage.loc[links.event_id]=1.
        for slip in [0.,.0005]:
            key=name+('_stress' if slip else '')
            case=cash.queue.engine.simulate(data,market,quotes,leverage,slip,EXP,key,entry_limits=caps)
            results[key]=dict(case=case,changed=len(links),unused_tokens=pending)
            print(key,len(links),case.get('metrics',{}).get('all'),flush=True)
            gc.collect()
    cash.queue.repair.archive.prices.safe_json(EXP/'summary.json',results)


if __name__=='__main__':
    main()

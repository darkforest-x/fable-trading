"""Add three first-reclaim filters from 5m candles available by confirmation close.

Wick length uses only the touch bar. Outer-rail excursion uses high/low from
touch through the first reclaim. Breadth counts same-side first touches in the
already closed touch bar across the fixed instrument universe.
"""
import numpy as np
import pandas as pd
from prepare import CACHE, HERE

def build():
    cache = np.load(CACHE/'windows.npy',mmap_mode='r')
    original = pd.read_csv(HERE/'entry_candidates.csv.gz')
    touches = original[original.rule.eq('touch_close')].set_index('event_id')
    touch_counts = touches.groupby(['entry_ms','rail_side']).symbol.transform('nunique')
    paths = pd.read_csv(HERE.parent/'exp-alt-month-first-week-five-minute-observation-20261111-v2/event_paths.csv.gz',
                        usecols=['event_id','level']).set_index('event_id')
    out = []
    for e in original[original.rule.eq('first_reclaim')].itertuples(index=False):
        touch = touches.loc[e.event_id]
        i, j = int(touch.entry_idx)-1, e.entry_idx
        a = cache[e.grid_idx]
        _,op,h,l,c = a[i]
        upper = e.rail_side=='upper'
        wick = h-max(op,c) if upper else min(op,c)-l
        level=paths.at[e.event_id,'level']
        penetration = np.max(a[i:j,2])/level-1 if upper else 1-np.min(a[i:j,3])/level
        rules = []
        if j==i+1 and h>l and wick >= .5*(h-l):
            rules.append('half_wick')
        if penetration >= .01:
            rules.append('deep_reclaim')
        if touch_counts.at[e.event_id] >= 5:
            rules.append('breadth5_reclaim')
        for rule in rules:
            row=e._asdict()
            row.update(rule=rule,candidate_id=f'{e.event_id}/{rule}/{j}')
            out.append(row)
    df=pd.DataFrame(out,columns=original.columns)
    df.to_csv(HERE/'shape_candidates.csv.gz',index=False)
    print(df.rule.value_counts().to_dict())

if __name__=='__main__':
    build()

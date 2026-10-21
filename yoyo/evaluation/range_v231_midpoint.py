"""Replay only admission for the owner-approved V2.31 midpoint-side filter.

Reuse frozen V2.30 annual 5m OHLC and its exact causal 15m Stoch5/3/3 and
pre-touch Monday/Fib snapshots. No future outcome, exit engine or parameter
search is used. Preserve current exact touch-extreme stops and first-match
consumption. Baseline identities must reproduce before writing new results.
"""
from pathlib import Path
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
from yoyo.evaluation.monday_range_v2_2_reference import signals
from yoyo.evaluation.pepe_range_exits import aggregate
from yoyo.evaluation.monday_range_v2_24_reference import select_matches as previous
from yoyo.evaluation.monday_range_v2_31_reference import select_matches

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'experiments/active/exp-range-v230-pivot-20261021-v1'
DEST=ROOT/'experiments/active/exp-range-v231-midpoint-20261021-v1'


def exact_stops(rows,q,tick,end):
    result=[]
    for row in rows:
        stop=float(q.iloc[row['touch_i']]['low' if row['side']==1 else 'high'])
        if q.index[row['entry_i']] < pd.Timestamp(end) and stop>0 and row['side']*(row['entry']-stop)>=tick:
            result.append(dict(row,stop=stop))
    return result


def main():
    cfg=json.loads((SOURCE/'config.json').read_text())
    summary={'source':str(SOURCE.relative_to(ROOT)),'start':cfg['start'],'end':cfg['end'],
             'builder_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
             'rule':'Matched key price: short >= Monday midpoint, long <= midpoint; touch snapshot, inclusive.',
             'training_eligible':False,'production_eligible':False,'symbols':{}}
    prepared={}
    for spec in cfg['symbols']:
        symbol=spec['symbol']
        five=pd.read_csv(SOURCE/symbol/'candles.csv.gz',float_precision='round_trip')
        five.index=pd.to_datetime(five.pop('ts'),unit='ms',utc=True)+pd.Timedelta(minutes=5)
        q=aggregate(five,15)
        assert np.all(np.diff(q.index.as_unit('ns').asi8)==pd.Timedelta(minutes=15).value)
        grid,names,weeks,_=monday_levels(q,cfg['ratios'],cfg['timezone'])
        a=signals(q).to_numpy()
        kwargs=dict(before=5,wait_bars=2,penetration=.1,buffer=.002,tick=spec['tick'],
                    timezone=cfg['timezone'],start=cfg['start'])
        old=exact_stops(previous(q,a,grid,names,weeks,**kwargs),q,spec['tick'],cfg['end'])
        cached=pd.read_csv(SOURCE/symbol/'signal_matches.csv',float_precision='round_trip')
        pd.testing.assert_frame_equal(pd.DataFrame(old)[cached.columns],cached,check_dtype=False,atol=1e-10,rtol=0)
        chosen=select_matches(q,a,grid,names,weeks,**kwargs)
        rows=exact_stops(chosen,q,spec['tick'],cfg['end'])
        for r in rows:
            j=r['touch_i']; midpoint=grid[j,0]+.5*(grid[j,1]-grid[j,0])
            assert (r['level']<=midpoint if r['side']==1 else r['level']>=midpoint)
        # Prefixes around sampled decisions ensure later candles cannot revise entries.
        ends=sorted({r['entry_i']+d for r in rows[::max(1,len(rows)//8)] for d in [0,1,2] if 0<r['entry_i']+d<=len(q)})
        for end in ends:
            got=select_matches(q.iloc[:end],a[:end],grid[:end],names,weeks[:end],**kwargs)
            assert got == [r for r in chosen if r['entry_i']<end]
        before={r['signal_i']:r for r in old}; after={r['signal_i']:r for r in rows}
        common=before.keys()&after.keys()
        summary['symbols'][symbol]={'previous':len(old),'current':len(rows),
          'long':sum(r['side']==1 for r in rows),'short':sum(r['side']==-1 for r in rows),
          'removed':len(before.keys()-after.keys()),'added':len(after.keys()-before.keys()),
          'rematched':sum(before[i]!=after[i] for i in common),'prefix_checks':len(ends),
          'baseline_exact':True}
        prepared[symbol]=rows
    DEST.mkdir(parents=True,exist_ok=False)
    for symbol,rows in prepared.items():
        pd.DataFrame(rows).to_csv(DEST/f'{symbol}_signal_matches.csv',index=False)
    (DEST/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

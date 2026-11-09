"""Own a dollar-bullish major ranked by the fraction of its 12H cross-asset clouds pointing up."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_major_rotation as rotation
from yoyo.evaluation import cloud_close_pair_branch as spread

EXP=rotation.EXP.parent/'exp-cloud-close-relative-cloud-leaders-20261109-v1'
SYMBOLS=sorted(spread.SYMBOLS)


def clocks_and_prices():
    loaded={}
    decisions={}
    h=spread.wallet.HOUR
    for s in SYMBOLS:
        time,px=spread.wallet.quotes(s)
        table=pd.DataFrame(px,index=time,columns=['open','high','low','close'])
        table=table.loc[:spread.wallet.END-1]
        if table.index.has_duplicates or not table.index.is_monotonic_increasing:
            raise ValueError('Duplicate or unsorted native prices')
        idx=table.index+ h
        complete=table.index.to_series().diff(11).eq(11*h).to_numpy()&(idx%spread.STEP==0)
        decisions[s]=pd.Series(table.close.to_numpy()[complete],index=idx[complete])
        loaded[s]={'bars1':table.loc[spread.wallet.START:]}
    clock=loaded['BTC']['bars1'].index
    if not np.array_equal(clock,np.arange(spread.wallet.START,spread.wallet.END,h)):
        raise ValueError('Allocation clock lacks continuous native BTC coverage')
    return loaded,pd.DataFrame(decisions)


def states(loaded,closes):
    """Completed consecutive native USD and pair close ratios, 50-bar V1.7 warm-up, and opening availability."""
    clock=loaded['BTC']['bars1'].index
    grid=clock[clock%spread.STEP==0]
    scores={}
    for s in SYMBOLS:
        peers={}
        for other in SYMBOLS:
            if other!=s:
                observed=(closes[s]/closes[other]).dropna()
                trend=spread.trend(observed).dropna()
                peers[other]=trend.gt(0).astype(float)
        votes=pd.DataFrame(peers)
        bull=spread.trend(closes[s].dropna()).reindex(grid).gt(0)
        scores[s]=votes.mean(axis=1).reindex(grid).where(bull)
    scores=pd.DataFrame(scores)
    for name in ['leader','laggard','alphabetic']:
        selected=[]
        previous=-1
        values=scores[SYMBOLS].to_numpy()
        for i,t in enumerate(grid):
            options=np.flatnonzero(np.isfinite(values[i]))
            chosen=-1
            if len(options):
                if name!='alphabetic':
                    best=(max if name=='leader' else min)(values[i,options])
                    options=options[values[i,options]==best]
                    chosen=previous if previous in options else options[0]
                else:
                    chosen=options[0]
                if t not in loaded[SYMBOLS[chosen]]['bars1'].index:
                    chosen=-1
            selected.append(chosen)
            previous=chosen
        scores[name]=selected
    return scores


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed multi-major relative-cloud rotation')
    loaded,closes=clocks_and_prices()
    choices=states(loaded,closes)
    choices.to_csv(EXP/'decisions.csv.gz',index_label='earliest_entry_ms')
    cases={}
    for name in ['leader','laggard','alphabetic']:
        for slip in [0.,.0005]:
            key=name+('_stress' if slip else '')
            cases[key]=rotation.run(loaded,choices,name,slip,symbols=SYMBOLS,directory=EXP)
            print(key,cases[key]['metrics']['all'],flush=True)
    rotation.source._write_json(EXP/'summary.json',dict(arms=cases,symbols=SYMBOLS,
                    start_ms=spread.wallet.START,end_ms=spread.wallet.END))


if __name__=='__main__':
    main()

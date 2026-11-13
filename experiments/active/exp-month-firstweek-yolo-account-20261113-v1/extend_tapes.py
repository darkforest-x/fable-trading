"""Fill missing trade-price UTC days for 168h positions and the preceding 24h watch.
Use cache only for missing native 5m timestamps. Keep original bars immutable.
Coverage covers the entire potential holding window, even for a signal which may
ultimately have no trade or exit earlier. Genuine listing/delisting gaps remain.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from universe import HERE, ROOT, DATA, FIVE, DAY, daily, load_bars
OUT=DATA/'execution_days'

def run(case_group):
    symbol,group=case_group
    bounds=[]
    for r in group.itertuples():
        bounds.append((int(r.start_ms),int(r.end_ms)))
    required=np.unique(np.concatenate([np.arange(left,right+FIVE,FIVE) for left,right in bounds]))
    ym=pd.to_datetime(required,unit='ms')
    missing=[]
    for m in ym.to_period('M').unique():
        raw=load_bars(symbol,str(m))
        sub=required[ym.to_period('M')==m]
        miss=np.setdiff1d(sub,raw[:,0])
        for d in pd.to_datetime(miss,unit='ms').normalize().unique():
            day=pd.Timestamp(d)
            target=OUT/symbol/f'{day:%Y-%m-%d}.npy'
            ts=day.value//10**6
            need=miss[(miss>=ts)&(miss<ts+DAY)]
            try:
                if target.is_file():a=np.load(target,allow_pickle=False)
                else:
                    a=daily(symbol,day.strftime('%Y-%m-%d'))
                    assert a.shape[1]==5 and np.isfinite(a).all() and np.all(np.diff(a[:,0])==FIVE)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    temp=target.with_suffix('.tmp.npy');np.save(temp,a,allow_pickle=False);temp.replace(target)
                unseen=len(np.setdiff1d(need,a[:,0]))
                missing.append((symbol,str(day.date()),len(need),unseen,'official'))
            except Exception as err:
                missing.append((symbol,str(day.date()),len(need),len(need),str(err)))
    return missing
def main():
    p=pd.read_csv(HERE/'primary_signals.csv')
    p['start_ms']=p.confirm_close_ms;p['end_ms']=p.start_ms+7*DAY
    m=pd.read_csv(HERE/'october_population.csv')
    m['start_ms']=m.known_ms;m['end_ms']=m.stop_ms+7*DAY
    rows=pd.concat([p,m])
    # Give independently reviewable photo accounts their complete path first.
    keys=rows.groupby('symbol');symbols=list(p.symbol.unique())+sorted(set(m.symbol)-set(p.symbol))
    results=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i,r in enumerate(pool.map(run,[(s,keys.get_group(s)) for s in symbols])):
            results.extend(r)
            if i==len(p.symbol.unique())-1 or i%40==0:
                print('through',i,'supplemented days',len(results),'missing',sum(t[3] for t in results),flush=True)
    pd.DataFrame(results,columns=['symbol','utc_day','original_missing_5m','remaining_missing_5m','source']).to_csv(DATA/'execution_coverage.csv',index=False)
    print('complete',len(symbols),'symbols',len(results),'supplements',sum(t[3] for t in results),'missing bars')
if __name__=='__main__':main()

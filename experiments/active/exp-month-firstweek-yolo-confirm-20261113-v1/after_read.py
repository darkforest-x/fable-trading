"""Describe prices only AFTER the frozen model had emitted its first matching box.
The model never sees these outcomes. Same OHLC tape and directional price-change
convention as the parallel cloud review. No stop, target, fee or simulated fill.
"""
from probe import HERE,DATA,M,F,ew,avg,resample
import numpy as np
import pandas as pd

def main():
    m=pd.read_csv(DATA/'manifest.csv.gz',float_precision='round_trip').set_index('id')
    cases=pd.read_csv(HERE/'pilot.csv'); results=[]
    for r in cases[cases.found].itertuples():
        raw=np.load(DATA/'tapes'/f'{r.id}.npy'); q=m.loc[r.id]; i=raw[:,0].searchsorted(r.first_ms)
        assert raw[i,0]==r.first_ms
        b=resample(raw); j=np.searchsorted(b[:,0]+3*F,r.first_ms,side='right')-1
        direction=1 if r.side=='long' else -1; w=raw[i:i+288]; assert len(w)==288
        ref=w[0,1]; v=lambda price:100*direction*(price/ref-1)
        aligned=lambda a,k:direction*(ew(a[:,4],2/21)[k]-ew(a[:,4],2/51)[k])>0
        results.append(dict(id=r.id,tf=r.tf,time=pd.Timestamp(r.first_ms,unit='ms',tz='UTC').tz_convert('Asia/Shanghai').isoformat(),
            ref=ref,cloud5=aligned(raw,i-1),cloud15=aligned(b,j),already=100*direction*(ref/q.direction_close-1),
            favorable24=max(0,v(w[:,2].max() if direction==1 else w[:,3].min())),
            adverse24=min(0,v(w[:,3].min() if direction==1 else w[:,2].max())),change24=v(w[-1,4])))
    pd.DataFrame(results).to_csv(HERE/'first_read.csv',index=False,float_format='%.8f')
if __name__=='__main__': main()

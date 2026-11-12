"""First observable small-timeframe confirmations after the frozen H1 direction close.
5/15m native chart EMA20/50 from past closes. The formation-only six-line gate is
SPIKE/IMACD: prior 12 closed bars' mean (six MA envelope / Wilder ATR14) <= 3,
and sum of pair-order flips >= 2. Our directional trigger is a *fresh* close
beyond all six; this deliberately does not reproduce the full SPIKE/IMACD signal.
The original dense-launch V1 (core + five confirmation bars) is a separate arm.
No execution simulation. Forward prices never enter the confirmation predicates.
"""
from pathlib import Path
from itertools import combinations
import argparse
import numpy as np
import pandas as pd
from scipy.signal import lfilter
from zoneinfo import ZoneInfo
from functools import lru_cache

HERE=Path(__file__).resolve().parent
DATA=HERE.parents[2]/'data/research/month-firstweek-cloud-dense-20261112-v1'
M=60000; F=5*M; DAY=1440*M

@lru_cache(maxsize=None)
def expiry(month):
    from sys import path
    path.insert(0,str(HERE.parent/'exp-month-firstweek-intraday-retest-20261112-v1'))
    from review import expiry as old
    return old(month)

def ew(c, alpha):
    return lfilter([alpha],[1.,alpha-1],c,zi=[c[0]*(1-alpha)])[0]

def avg(a,n):
    return pd.Series(a).rolling(n,min_periods=n).mean().to_numpy()

def previous(a):
    return np.r_[False,a[:-1]]

def features(a,d):
    """Continuous OHLC only. Use close through this bar; width/flips stop one bar earlier."""
    c=a[:,4]; h=a[:,2]; l=a[:,3]
    e20=ew(c,2/21); spread=e20-ew(c,2/51)
    cloud=d*spread>0
    flip=cloud & (np.r_[np.nan,d*spread[:-1]]<=0)
    ma=np.column_stack([avg(c,20),e20,avg(c,60),ew(c,2/61),avg(c,120),ew(c,2/121)])
    tr=np.maximum(h-l,np.maximum(abs(h-np.r_[c[0],c[:-1]]),abs(l-np.r_[c[0],c[:-1]])))
    atr=np.r_[np.full(13,np.nan),ew(np.r_[tr[:14].mean(),tr[14:]],1/14)]
    width=np.max(ma,axis=1)-np.min(ma,axis=1)
    width=np.divide(width,atr,out=np.full_like(width,np.nan),where=atr>0)
    flips=np.zeros(len(c))
    for i,j in combinations(range(6),2):
        diff=ma[:,i]-ma[:,j]
        flips[1:]+=( (diff[1:]>0)&(diff[:-1]<=0) ) | ( (diff[1:]<0)&(diff[:-1]>=0) )
    dense=np.r_[False,((avg(width,12)<=3.)&(avg(flips,12)*12>=2.-1e-9))[:-1]]
    outside=d*c>np.max(d*ma,axis=1)
    return cloud,flip,dense & outside & ~previous(outside)

def resample(a):
    assert a[0,0]%(3*F)==0
    b=a[:len(a)//3*3].reshape(-1,3,5)
    return np.column_stack((b[:,0,0],b[:,0,1],b[:,:,2].max(axis=1),b[:,:,3].min(axis=1),b[:,-1,4]))

def event(r, a, grade):
    # Every reference is a five-minute OPEN after the indicator bar is fully closed.
    known=int(r.touch_ms)+60*M
    direction=1 if r.side=='long' else -1
    b=resample(a); ca=a[:,0].astype('int64')+F; cb=b[:,0].astype('int64')+3*F
    assert ((np.diff(ca)==F).all() and (np.diff(cb)==3*F).all() and
            ca[0]<=known-479*3*F and a[-1,0]+F>=r.stop_ms+DAY)
    watch=(ca>known)&(ca<=r.stop_ms)&(ca<expiry(r.month))
    allowed=np.flatnonzero(watch)
    assert len(allowed)>0 or r.stop_ms-known<=F
    knownix=np.searchsorted(ca,known); assert ca[knownix]==known
    kk=np.searchsorted(cb,ca,side='right')-1
    fifteen=(kk>=0)&(cb[np.maximum(kk,0)]==ca)
    i15=np.maximum(0,kk)
    a5,flip5,break5=features(a,direction)
    a15,flip15,break15=features(b,direction)
    joint=a5 & a15[i15] & (kk>=0)
    diagnostics=dict(cloud5_at_h1=bool(a5[knownix]),cloud15_at_h1=bool(a15[kk[knownix]]))
    if grade:
        from grade import grade_hits
        g5=np.zeros(len(a),bool); g15=np.zeros(len(b),bool)
        if len(allowed):
            g5=grade_hits(a,direction,int(allowed[0]),int(allowed[-1])+1)
            i=np.flatnonzero((cb>known)&(cb<=r.stop_ms)&(cb<expiry(r.month)))
            if len(i): g15=grade_hits(b,direction,int(i[0]),int(i[-1])+1)
        rules={'launch5':g5,'launch15':fifteen & g15[i15], 'launch5_both':g5 & joint}
    else:
        rules={'cloud5_flip':flip5, 'cloud15_flip':fifteen & flip15[i15],
            'clouds_new_alignment':joint & ~previous(joint),
            'break5':break5,'break15':fifteen & break15[i15],
            'break5_cloud5':break5 & a5,'break5_cloud15':break5 & a15[i15],
            'break5_both':break5 & joint}
    rows=[]
    for arm,mask in rules.items():
        hits=np.flatnonzero(watch & mask)
        row=dict(id=r.id,symbol=r.symbol,month=r.month,side=r.side,arm=arm,
                 first20_ms=r.first_20_bar_ms,known=known,**diagnostics)
        if len(hits):
            t=int(hits[0]); ix=t+1; ref=a[ix,1]; stamp=int(ca[t])
            # Exactly same five-minute tape for each timeframe's 4/24h excursions.
            row.update(read_ms=stamp,delay=(stamp-known)//M,ref=ref,
                move=100*direction*(ref/r.direction_close-1),
                room=100*direction*(r.near_edge/ref-1),c5=bool(a5[t]),c15=bool(a15[i15[t]]))
            for h in (4,24):
                w=a[ix:ix+h*12]; assert len(w)==h*12 and w[-1,0]+F==stamp+h*60*M
                val=lambda x:100*direction*(x/ref-1)
                row.update({f'c{h}':val(w[-1,4]),f'f{h}':max(0,val(w[:,2].max() if direction==1 else w[:,3].min())),
                           f'a{h}':min(0,val(w[:,3].min() if direction==1 else w[:,2].max()))})
        rows.append(row)
    if grade: return rows,None
    # Negative/control observations for the same post-alert elapsed minute as a case.
    path=np.full((289,2),np.nan)
    if len(allowed):
        j=allowed+1; offsets=((ca[allowed]-known)//F).astype(int); ref=a[j,1]
        path[offsets,0]=100*direction*(ref/r.direction_close-1)
        path[offsets,1]=100*direction*(a[j+287,4]/ref-1)
    return rows,path

def main():
    p=argparse.ArgumentParser(); p.add_argument('--grade',action='store_true'); args=p.parse_args()
    m=pd.read_csv(DATA/'manifest.csv.gz',float_precision='round_trip')
    m=m[m.coverage.eq('complete')].sort_values('id'); assert m.id.is_unique and len(m)>0
    rows=[]; paths=[]
    for i,r in enumerate(m.itertuples()):
        a=np.load(DATA/'tapes'/f'{r.id}.npy'); vals,px=event(r,a,args.grade)
        rows.extend(vals)
        if px is not None: paths.append(px)
        if (i+1)%1500==0: print('observed',i+1,flush=True)
    name='grade' if args.grade else 'base'
    out=pd.DataFrame(rows)
    out.to_csv(DATA/f'{name}_events.csv.gz',index=False,compression='gzip')
    if paths: np.savez_compressed(DATA/'paths.npz',ids=np.array(m.id.tolist()),values=np.stack(paths))
    print(name,len(m),len(out),flush=True)
if __name__=='__main__': main()

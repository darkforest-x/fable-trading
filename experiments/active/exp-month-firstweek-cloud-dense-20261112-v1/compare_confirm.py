"""For a first observed check, match a different alert observable at the same elapsed 5m open.
Control has not yet confirmed this rule, is same side/NY month, different symbol,
and has moved within 0.5 pp from the H1 close. One control per case, without
replacement per arm. Closest progress has priority; ties deterministic. Contrast
is descriptive; markets and same-day moves can still differ. Monthly block CI.
"""
from pathlib import Path
import numpy as np
import pandas as pd
from observe import DATA,HERE,DAY

CUT=pd.Timestamp('2025-01-01T00:00Z').value//1000000

def summarize(events):
    pieces=[]
    for period,time in [('all',events),('through_2024',events[events.known+2*DAY<=CUT]),('from_2025',events[events.known>=CUT])]:
        for side,g0 in [('both',time),*time.groupby('side')]:
            for name,g in g0.groupby('arm'):
                t=g[g.read_ms.notna()]
                hit=g.first20_ms.notna()
                pieces.append(dict(period=period,side=side,arm=name,eligible=len(g),n=len(t),
                    unique_times=len(t.drop_duplicates(['symbol','side','read_ms'])),delay_med=t.delay.median(),prior_move_med=t.move.median(),room_med=t.room.median(),
                    mean4=t.c4.mean(),pos4=100*t.c4.gt(0).mean(),mean24=t.c24.mean(),med24=t.c24.median(),
                    pos24=100*t.c24.gt(0).mean(),mfe24=t.f24.median(),mae24=t.a24.median(),
                    large=int(hit.sum()),early20=int((hit & g.read_ms.le(g.first20_ms) & g.move.lt(20.)).sum())))
    return pd.DataFrame(pieces)

def match(g, paths):
    g=g.reset_index(drop=True)
    di=g.delay.to_numpy()/5; symbols=g.symbol.to_numpy(); path_id=g.path_id.to_numpy()
    idxs=[];jdxs=[];diffs=[]
    for _,frame in g.groupby(['month','side']):
        cohort=frame.index.to_numpy()
        for i in cohort[np.isfinite(di[cohort])]:
            tick=int(di[i])
            other=cohort[(symbols[cohort]!=symbols[i]) & (~np.isfinite(di[cohort]) | (di[cohort]>tick))]
            gap=abs(paths[path_id[other],tick,0]-paths[path_id[i],tick,0])
            good=np.isfinite(gap)&(gap<=.5)
            if not good.any(): continue
            idxs.extend([i]*int(good.sum())); jdxs.extend(other[good]); diffs.extend(gap[good])
    ids=g.id.to_numpy(); months=g.month.to_numpy(); out=[]
    cases,controls=set(),set()
    for k in np.argsort(diffs,kind='stable'):
        i=idxs[k]; j=jdxs[k]
        if i in cases or j in controls: continue
        cases.add(i); controls.add(j); tick=int(di[i])
        a=paths[path_id[i],tick,1]; b=paths[path_id[j],tick,1]
        assert np.isclose(a,g.c24.iat[i],rtol=0,atol=1e-8)
        out.append((ids[i],ids[j],months[i],g.side.iat[i],g.arm.iat[i],tick*5,diffs[k],a,b,a-b))
    return out

def ci(pairs):
    a=pairs.groupby('month')['diff'].agg(['sum','size']).to_numpy()
    boots=a[np.random.default_rng(1618).integers(len(a),size=(2500,len(a)))].sum(axis=1)
    return np.quantile(boots[:,0]/boots[:,1],[.025,.975])

def main():
    events=pd.concat([pd.read_csv(DATA/f'{n}_events.csv.gz') for n in ('base','grade')],ignore_index=True)
    assert not events.duplicated(['id','arm']).any() and events.groupby('arm').size().nunique()==1
    file=np.load(DATA/'paths.npz'); paths=file['values']; imap=dict(zip(file['ids'],range(len(paths))))
    events['path_id']=events.id.map(imap)
    assert events.path_id.notna().all()
    counts=summarize(events); counts.to_csv(HERE/'outcomes.csv',index=False,float_format='%.6f')
    pairs=pd.DataFrame([r for _,g in events.groupby('arm') for r in match(g,paths)],
        columns='case_id control_id month side arm delay gap case24 control24 diff'.split())
    pairs.to_csv(DATA/'matching.csv.gz',index=False)
    matched=[]
    for name,g in pairs.groupby('arm'):
        low,high=ci(g)
        matched.append(dict(arm=name,pairs=len(g),months=g.month.nunique(),gap_pp=g['gap'].median(),
            delta24=g['diff'].mean(),low=low,high=high,pre2025=g.loc[g.month<'2025-01','diff'].mean(),
            from2025=g.loc[g.month>='2025-01','diff'].mean()))
    pd.DataFrame(matched).to_csv(HERE/'controls.csv',index=False,float_format='%.6f')
    # Same first six-line confirmation; clouds are labels here, not a delayed second attempt.
    first=events[events.read_ms.notna() & events.arm.isin(['break5','launch5','break15','launch15'])].copy()
    first['both_at_first']=first.c5.astype(bool) & first.c15.astype(bool)
    first['positive24']=first.c24.gt(0)
    first.groupby(['arm','both_at_first']).agg(n=('id','size'),delay_med=('delay','median'),
        move_med=('move','median'),mean24=('c24','mean'),positive24=('positive24','mean'),
        mfe24=('f24','median'),mae24=('a24','median')).to_csv(HERE/'cloud_at_first_dense.csv',float_format='%.6f')
    aligned=events[events.arm=='cloud5_flip']
    print('at H1',aligned[['cloud5_at_h1','cloud15_at_h1']].value_counts().to_dict())
    print(counts[(counts.period=='all')&(counts.side=='both')].to_string(index=False))
    print(pd.DataFrame(matched).to_string(index=False))
if __name__=='__main__': main()

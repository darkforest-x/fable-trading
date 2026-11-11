"""Catalogue first daily rail touches and 7/14/45-day excursions from that rail.

No wick/close/uniqueness filter. An observation begins in the next UTC hour,
so OHLC extremes before the first actual intersecting hour has ended never
contribute. The 45-day label is only issued for contiguous full paths.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from census12 import OUT, BASE, HOUR, read_raw
from scan import bounds

COLS=['币安合约','纽约区间月','首次触价小时（北京）','12小时K线开盘（北京）','方向','轨价','当根收回',
      '7天最大反向幅度%','14天最大反向幅度%','45天最大反向幅度%','第几小时达到20%','达20%前已知最大不利幅度%']


def per_symbol(pair):
    symbol,valid=pair
    q=read_raw(symbol); times=q[:,0].astype('int64'); clocks=times//HOUR
    if not len(q): return []
    out=[]; dates=set()
    for row in valid.itertuples():
        month=row.month;_,start,end=bounds(month)
        l,r=times.searchsorted([start.value//10**6,end.value//10**6])
        sub=q[l:r]; width=row.week_high-row.week_low
        lower=row.week_low-.618*width;upper=row.week_high+.618*width
        lo=(lower>0)&(sub[:,3]<=lower)&(sub[:,2]>=lower)
        hi=(sub[:,3]<=upper)&(sub[:,2]>=upper)
        hits=np.flatnonzero(lo|hi)
        if not len(hits):continue
        days=(clocks[l+hits]+8)//24
        for i in hits[np.r_[True,np.diff(days)!=0]]:
            pos=l+i;clock=clocks[pos];day=(clock+8)//24
            if day in dates:continue
            dates.add(day)
            if lo[i] and hi[i]:continue
            sign=1 if lo[i] else -1;level=lower if sign==1 else upper
            bar_clock=clock//12*12; bar_close=bar_clock+11
            ci=clocks.searchsorted(bar_close);closed=(q[ci,4]-level)*sign>0 if ci<len(q) and clocks[ci]==bar_close else None
            future=q[pos+1:pos+1+45*24]
            if len(future):
                breaks=np.flatnonzero(future[:,0]!=(clock+1+np.arange(len(future)))*HOUR)
                if len(breaks):future=future[:breaks[0]]
            good=((future[:,2] if sign==1 else future[:,3])/level-1)*sign*100
            bad=((future[:,3] if sign==1 else future[:,2])/level-1)*-sign*100
            excursions=[max(0.,float(good[:days*24].max())) if len(good)>=days*24 else None for days in (7,14,45)]
            cross=np.flatnonzero(good>=20)
            j=int(cross[0]) if len(cross) else None
            earlier=max(0.,float(bad[:j].max()) if j else 0.) if j is not None else None
            out.append([symbol,month,int(clock),int(bar_clock),'下轨' if sign==1 else '上轨',level,closed,*excursions,
                        j+1 if j is not None else None,earlier])
    return out


if __name__=='__main__':
    coverage=pd.read_csv(OUT/'coverage_months.csv')
    groups=coverage[coverage.status.eq('valid')].sort_values('month').groupby('symbol')
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows=[r for batch in pool.map(per_symbol,groups) for r in batch]
    a=pd.DataFrame(rows,columns=COLS).sort_values([COLS[2],COLS[0]])
    for c in COLS[2:4]:
        a[c]=pd.to_datetime(a[c],unit='h',utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M')
    a.to_csv(OUT/'逐日首次碰轨_7天14天45天.csv.gz',index=False,compression='gzip')
    a[a[COLS[9]]>=20].to_csv(OUT/'逐日首次碰轨_45天反转至少20pct.csv',index=False,encoding='utf-8-sig')
    for name in COLS[7:10]:
        mature=a[a[name].notna()];w=mature[mature[name]>=20]
        print(name,len(mature),len(w),w[COLS[0]].nunique(),len(w[[COLS[0],COLS[1],COLS[4]]].drop_duplicates()),flush=True)

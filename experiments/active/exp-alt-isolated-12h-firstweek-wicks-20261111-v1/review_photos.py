"""Reconcile the owner's 15 price-range measurements with actual venue-specific rails.

Measurements are transcribed from screenshots, not simulated fills. A photograph
can contain secondary marks. Case metadata selects the main ruler and month, not
a favorable touch inside the month. Output contains every intersecting 12h candle.
"""
from pathlib import Path
import numpy as np
import pandas as pd
from census12 import BASE, OUT, read_raw
from scan import bounds

CASES=[
 ('19.12.54','FARTCOIN','2026-09','上',-47.18,36.5),('19.13.11','OP','2026-08','下',51.64,12.5),
 ('19.13.15','NEAR','2025-07','上',-22.99,8),('19.13.21','ZK','2024-10','上',-25.23,0),
 ('19.13.27','JELLYJELLY','2026-05','上',-31.28,3.5),('19.13.32','HOOD','2026-03','下',45.27,5),
 ('19.13.40','XAG','2026-05','上',-30.17,10.5),('19.13.45','SSV','2026-09','上',-40.26,37.5),
 ('19.13.50','APR','2026-08','下',93.62,8.5),('19.13.54','PYTH','2026-10','上',-39.49,7.5),
 ('19.14.03','DRAM','2026-06','上',-47.18,31.5),('19.14.07','BE','2026-09','上',-47.63,35.5),
 ('19.14.11','SOXL','2026-09','下',68.33,13),('19.14.16','MVLL','2026-08','上',-39.40,6.5),
 ('19.14.22','RAM','2026-07','下',44.85,11)]
COLUMNS=['open','high','low','close']
REF=BASE.parent/'owner_reference'


def inspect(a,month,side):
    s,r,e=bounds(month)
    ex=pd.date_range(s,e,freq='h',inclusive='left')
    if len(ex.difference(a.index)):return {}
    anchor=a.loc[(a.index>=s)&(a.index<r)]
    h,l=anchor.high.max(),anchor.low.min()
    level=h+.618*(h-l) if side=='上' else l-.618*(h-l)
    active=a[(a.index>=r)&(a.index<e)]
    hits=active[(active.low<=level)&(active.high>=level)]
    event=hits.index.floor('12h').unique()
    result={'轨价':level,'触轨12h根数':len(event),'K线开盘（北京）':', '.join(t.tz_convert('Asia/Shanghai').strftime('%m-%d %H:%M') for t in event)}
    if len(hits):
        first=hits.index[0]
        future=a[(a.index>first)&(a.index<=first+pd.Timedelta(days=45))]
        path=((1-future.low/level) if side=='上' else (future.high/level-1))*100
        limit=path[path>=20]
        result['首次触轨小时（北京）']=first.tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')
        result['轨价起算已观察小时数']=len(path)
        result['轨价起算首次反向20%的小时数']=(limit.index[0]-first).total_seconds()/3600 if len(limit) else None
        result['首次触轨后已观察最大反向幅度%']=path.max()
    return result


if __name__=='__main__':
    ok=pd.read_csv(REF/'okx_hours.csv.gz')
    strict=pd.read_csv(OUT/'isolated_12h.csv')
    u=pd.read_csv(BASE/'universe_audit.csv').set_index('symbol')
    rows=[]
    for photo,v,m,side,change,days in CASES:
        symbol=v+'USDT'
        a=ok[ok.symbol.eq(v)&ok.month.eq(m)].copy()
        a.index=pd.to_datetime(a.time,unit='ms',utc=True)
        a=a.sort_index()
        row={'图片':'2026-11-11 '+photo+'.jpg','合约':symbol,'区间月（纽约）':m,'侧':side,'图中量尺幅度%':change,'图中量尺横向天数（绝对值）':days,'币安标的类型':u.loc[symbol,'underlying']}
        row.update({'OKX_'+k:val for k,val in inspect(a,m,side).items()})
        if u.loc[symbol,'eligible']:
            q=read_raw(symbol);a=pd.DataFrame(q[:,1:5],index=pd.to_datetime(q[:,0],unit='ms',utc=True),columns=COLUMNS)
            row.update({'币安_'+k:val for k,val in inspect(a,m,side).items()})
            d=strict[strict.symbol.eq(symbol)&strict.month.eq(m)&strict.side.eq('upper' if side=='上' else 'lower')]
            row['币安同月同侧原单针再触前20%案例数']=int((d.isolated & (d.max_before_retouch>=20)).sum())
        rows.append(row)
    pd.DataFrame(rows).to_csv(Path(__file__).parent/'原图15张逐项核对.csv',index=False,encoding='utf-8-sig')

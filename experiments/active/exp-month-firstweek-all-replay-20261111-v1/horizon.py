"""After the deck has been revealed, follow the original archived 45-day path across month-end.

The existing 20% excursion is an outcome label. It never determines the case's
identity or its causal readings. Track both highs/lows and closed H1 prices, and
avoid including the ambiguous high/low of the hour in which the initial touch happened.
"""
from replay import CUTOFF, HOUR, INPUT, OUT, WINDOW, continuous_hours, read_raw, save, shift, ROUNDTRIP
import numpy as np
import pandas as pd


def describe(event, raw):
    d=1 if event.side=='long' else -1
    start=event.touch_ms+HOUR
    end=WINDOW[event.month][2]
    hours=continuous_hours(raw,start,min(start+1080*HOUR,CUTOFF//HOUR*HOUR))
    result=dict(id=event.id,observed_hours_45=len(hours))
    if not len(hours):
        return result
    favorable=shift(hours[:,2 if d==1 else 3],event.rail,d)
    bad=shift(hours[:,3 if d==1 else 2],event.rail,d)
    closes=shift(hours[:,4],event.rail,d)
    result['peak_after_touch_pct']=max(0.,favorable.max())
    result['worst_after_touch_pct']=min(0.,bad.min())
    result['best_close_45_pct']=closes.max()
    result['peak_hour_open_ms']=int(hours[favorable.argmax(),0])
    crossing=np.flatnonzero(favorable>=20)
    if len(crossing):
        j=crossing[0]
        result['first_extreme20_hours']=int(j+1)
        result['first_extreme20_hour_open_ms']=int(hours[j,0])
        result['extreme20_after_expiry']=bool(hours[j,0]>=end)
        result['worst_before_20_hour_pct']=min(0.,bad[:j].min()) if j else 0.
    confirmed20=np.flatnonzero(closes>=20)
    if len(confirmed20):
        result['first_close20_after_touch_ms']=int(hours[confirmed20[0],0]+HOUR)
    return result


def main():
    frame=pd.read_csv(OUT/'cards.csv.gz',**ROUNDTRIP)
    records=[]
    for symbol,g in frame.groupby('symbol',sort=True):
        raw=read_raw(symbol)
        records.extend(describe(row,raw) for row in g.itertuples())
    res=pd.DataFrame(records)
    labels=pd.read_csv(INPUT,usecols=['币安合约','首次触价小时（北京）','第几小时达到20%','45天最大反向幅度%'],
                       **ROUNDTRIP)
    labels['id']=labels['币安合约']+'|'+labels['首次触价小时（北京）'].str[:10]
    comp=labels.merge(res,on='id',validate='one_to_one')
    old=comp['第几小时达到20%']
    new=comp['first_extreme20_hours']
    differences=~(new.eq(old)|(new.isna()&old.isna()))
    legacy_peak=comp['45天最大反向幅度%']
    complete=comp.observed_hours_45.eq(1080)
    peak_diff=complete&(~np.isclose(comp.peak_after_touch_pct,legacy_peak,rtol=1e-10,atol=1e-10))
    if differences.any() or peak_diff.any() or not complete.equals(legacy_peak.notna()):
        save(comp[differences|peak_diff|complete.ne(legacy_peak.notna())],'45horizon_source_differences')
        print('archived outcome/hour differences',int(differences.sum()),'peak differences',int(peak_diff.sum()),
              'coverage differences',int(complete.ne(legacy_peak.notna()).sum()),flush=True)
    save(res,'45day_path')
    print('Reached 20%',new.notna().sum(),'of',len(res),'first after original rail expired',
          res.extreme20_after_expiry.eq(True).sum(),flush=True)


if __name__=='__main__': main()

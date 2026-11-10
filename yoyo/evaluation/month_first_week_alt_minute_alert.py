"""Bounded exact-minute replay of the 25-coin monthly alert on the frozen cohort.

Every rolling 5-minute window is inside one UTC hour or two adjacent hours.
Use this upper bound to rule out impossible windows before loading 1m bars.
Only reconciled 60-confirmed-minute OHLC is allowed to establish an event time.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import gzip
import threading
import time

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation.month_first_week_alt_entries import DEST, ROOT, prices, universe

CACHE=ROOT/'data/research/month-first-week-alt-minute-alert-20261110'
HOUR=pd.Timedelta(hours=1)
MIN=pd.Timedelta(minutes=1)
LIMIT=25
COOLDOWN=30*MIN
LOCAL=threading.local()
LOCK=threading.Lock()
NEXT=0.


def request_window(key, end):
    global NEXT
    url='https://www.okx.com/api/v5/market/history-candles'
    params=dict(instId=key+'-USDT-SWAP',bar='1m',after=int(end.timestamp()*1000),limit=100)
    if not hasattr(LOCAL,'session'):
        LOCAL.session=requests.Session()
    for attempt in range(3):
        with LOCK:
            delay=max(0.,NEXT-time.monotonic())
            NEXT=time.monotonic()+delay+.14
        time.sleep(delay)
        try:
            response=LOCAL.session.get(url,params=params,timeout=10)
            payload=response.json()
            if response.status_code==429 or str(payload.get('code')) in ('50011','50040'):
                time.sleep(2**attempt);continue
            response.raise_for_status()
            return payload
        except (requests.RequestException,ValueError) as error:
            msg=str(error)
    return {'code':'transport_or_throttle','msg':locals().get('msg','rate limit')}


def minute_touch(row):
    symbol,hour,side,level,expected=row
    path=CACHE/symbol/(hour.strftime('%Y%m%dT%H')+'.json.gz')
    if not path.exists():
        payload=request_window(symbol,hour+HOUR)
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix('.tmp')
        with gzip.open(tmp,'wt') as out:json.dump(payload,out)
        tmp.replace(path)
    else:
        with gzip.open(path,'rt') as src: payload=json.load(src)
    result=dict(symbol=symbol,touch=hour,side=side,minute=pd.NaT,status='ok')
    if str(payload.get('code'))!='0':
        return dict(result,status='api_'+str(payload.get('code')))
    bars={int(r[0]):r for r in payload['data'] if r[8]=='1'}
    start=int(hour.timestamp()*1000)
    times=range(start,start+3_600_000,60_000)
    if not all(t in bars for t in times):
        return dict(result,status='incomplete_1m')
    o,h,l,c=np.array([[float(x) for x in bars[t][1:5]] for t in times]).T
    if not np.allclose([o[0],h.max(),l.min(),c[-1]],expected,rtol=1e-7,atol=0):
        return dict(result,status='ohlc_mismatch')
    ix=np.flatnonzero(l<=level if side=='lower' else h>=level)
    if not len(ix):return dict(result,status='missing_literal_touch')
    return dict(result,minute=hour+int(ix[0])*MIN)


def main():
    events=pd.read_csv(DEST/'events.csv.gz',parse_dates=['touch'])
    selected=set();possible=set()
    for side,g in events.groupby('side'):
        counts=g.groupby('touch').size().to_dict()
        for hour in counts:
            if counts[hour]+counts.get(hour-HOUR,0)>=LIMIT or counts[hour]+counts.get(hour+HOUR,0)>=LIMIT:
                possible.add((side,hour))
                # Neighbors also needed to inspect the 30-minute quiet period and cooldown.
                selected.update((side,hour+i*HOUR) for i in [-1,0,1])
    sub=events[[(r.side,r.touch) in selected for r in events.itertuples()]].copy()
    keys=universe().set_index('symbol',drop=False)
    work=[]
    for symbol,g in sub.groupby('symbol'):
        bars=prices(keys.loc[symbol])
        for r in g.itertuples():
            work.append((symbol,r.touch,r.side,r.level,bars.loc[r.touch,['open','high','low','close']].to_numpy()))
    CACHE.mkdir(parents=True,exist_ok=True)
    # No candidate can be both a first upper and a first lower touch in these windows in this tape.
    assert len({(t[0],t[1]) for t in work})==len(work)
    print('possible hours',len(possible),'minute windows including neighbors',len(work),flush=True)
    with ThreadPoolExecutor(max_workers=9) as pool:
        rows=pd.DataFrame(pool.map(minute_touch,work))
    rows.to_csv(DEST/'alert_minute_times.csv.gz',index=False)
    found=[];episodes=[];fixed=[]
    for side,g in rows.groupby('side'):
        known=g.dropna(subset=['minute']).set_index('minute').sort_index()
        missing=g[g.status!='ok']
        for hour in sorted(t for s,t in possible if s==side):
            minute_index=pd.date_range(hour,hour+HOUR-MIN,freq='min')
            candidates=known[(known.index>hour-5*MIN)&(known.index<hour+HOUR)]
            per_min=candidates.groupby(level=0).size()
            prior=pd.date_range(hour-4*MIN,hour+HOUR-MIN,freq='min')
            lower=per_min.reindex(prior,fill_value=0).rolling(5).sum().reindex(minute_index).astype(int)
            for stamp,n in lower.items():
                # An unresolved first touch may be anywhere in its known hour.
                unknown=missing[(missing.touch<=stamp)&(missing.touch+HOUR>stamp-4*MIN)].symbol.nunique()
                if n+unknown>=LIMIT:
                    found.append(dict(side=side,minute=stamp,confirmed=n,possible_upper=n+unknown))
            fixed_counts=known[(known.index>=hour)&(known.index<hour+HOUR)].groupby(pd.Grouper(freq='5min')).size()
            missing_bar=missing[missing.touch==hour].symbol.nunique()
            for stamp,n in fixed_counts.items():
                if side!='lower' or n+missing_bar<31 or n>62: continue
                close=stamp+5*MIN; decision=close+30*MIN
                quiet_known=len(known[(known.index>=close)&(known.index<decision)])
                quiet_unknown=missing[(missing.touch<decision)&(missing.touch+HOUR>close)].symbol.nunique()
                fixed.append(dict(stamp=stamp,confirmed=n,possible_upper=n+missing_bar,
                    new_in_next_30m=quiet_known,unknown_in_quiet_hours=quiet_unknown,
                    signal_status=('failed_quiet' if quiet_known else 'confirmed' if n>=31 and n+missing_bar<=62 and not quiet_unknown else 'unresolved')))
    windows=pd.DataFrame(found,columns=['side','minute','confirmed','possible_upper']).sort_values(['side','minute'])
    for side,g in windows.groupby('side'):
        # Summarize distinct dates rather than infer trigger count through unresolved windows.
        for day,part in g.groupby(g.minute.dt.floor('D')):
            eligible=part[part.confirmed>=LIMIT]
            best=part.loc[part.confirmed.idxmax()]
            episodes.append(dict(side=side,utc_day=day,first_confirmed_minute=eligible.minute.min(),
                known_peak=int(best.confirmed),peak_minute=best.minute,max_possible=int(part.possible_upper.max())))
    episodes=pd.DataFrame(episodes)
    episodes.to_csv(DEST/'alert_episode_days.csv',index=False)
    windows.to_csv(DEST/'alert_rolling_windows.csv.gz',index=False)
    fixed=pd.DataFrame(fixed,columns=['stamp','confirmed','possible_upper','new_in_next_30m','unknown_in_quiet_hours','signal_status'])
    fixed.to_csv(DEST/'alert_fixed_five_minute.csv',index=False)
    result=dict(window='five rolling one-minute candles, per side, 25 distinct first tests; dates with confirmed trigger-sized windows; no assumption about cooldown through unknown minutes',
        per_side_possible_hours={s:sum(1 for k in possible if k[0]==s) for s in ['upper','lower']},
        request_count=len(work),source=str(CACHE.relative_to(ROOT)),statuses=rows.status.value_counts().to_dict(),
        days_confirmed=int(episodes.first_confirmed_minute.notna().sum()),days_only_possible=int(episodes.first_confirmed_minute.isna().sum()),
        strict_fixed_31_62_quiet=fixed.signal_status.value_counts().to_dict())
    (DEST/'alert_minute_summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(result,flush=True)


if __name__=='__main__':main()

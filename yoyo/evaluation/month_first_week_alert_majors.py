"""Six-day pilot on the first demonstrable monthly 25-coin alert per UTC day.

Reuse the confirmed minute-alarm days, execute at the next 1m open. The
fixed-5m quiet variant uses the end of its 30-minute observation instead.
Two adjacent non-overlapping same-weekday six-day windows are timing controls.
Floating extrema use actual holding minutes in boundary hours and complete H1
inside. All fills and fees are independent VectorBT portfolio orders.
"""
import gzip
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation.month_first_week_alt_entries import DEST, ROOT, HOUR
from yoyo.evaluation.month_first_week_alt_minute_alert import request_window, CACHE, MIN


def fetch_hour(item):
    symbol,t=item
    path=CACHE/symbol/(t.strftime('%Y%m%dT%H')+'.json.gz')
    if not path.exists():
        payload=request_window(symbol,t+HOUR)
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix('.tmp')
        with gzip.open(tmp,'wt') as f:json.dump(payload,f)
        tmp.replace(path)
    with gzip.open(path,'rt') as f:rows=json.load(f).get('data',[])
    df=pd.DataFrame([([pd.to_datetime(int(r[0]),unit='ms',utc=True)]+list(map(float,r[1:5])))
                     for r in rows if r[8]=='1'],columns=['time','open','high','low','close']).set_index('time').sort_index()
    df=df.loc[(df.index>=t)&(df.index<t+HOUR)]
    if not df.index.equals(pd.date_range(t,periods=60,freq='min')):
        raise ValueError(f'{symbol} missing comparison minutes {t}')
    return item,df


def main():
    days=pd.read_csv(DEST/'alert_episode_days.csv',parse_dates=['first_confirmed_minute'])
    fixed=pd.read_csv(DEST/'alert_fixed_five_minute.csv',parse_dates=['stamp'])
    clocks=[('rolling25',v+MIN) for v in days.loc[days.side.eq('lower'),'first_confirmed_minute'].dropna()]
    clocks += [('fixed31_62_quiet',t+35*MIN) for t in fixed.loc[fixed.signal_status.eq('confirmed'),'stamp']]
    horizon=pd.Timedelta(days=6)
    cutoff=pd.Timestamp('2026-11-08T12:00Z')
    groups=[(sym,rule,observed,delta,observed+pd.Timedelta(days=delta))
            for rule,observed in clocks for sym in ['SOL','BTC','ETH'] for delta in [-7,0,7]
            if observed+pd.Timedelta(days=delta)+horizon<cutoff]
    keys=sorted({(s,clock.floor('h')) for s,_,_,_,t in groups for clock in (t,t+horizon)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        minutes=dict(pool.map(fetch_hour,keys))
    out=[]
    for s in ['SOL','BTC','ETH']:
        path=ROOT/'experiments/active/exp-cloud-top20-peak10r-20261108-v1/inputs'/s/'bars1h.csv.gz'
        hourly=pd.read_csv(path);hourly.index=pd.to_datetime(hourly.pop('time'),unit='ms',utc=True)
        for (sym,clock),df in minutes.items():
            if sym==s:
                actual=[df.open.iloc[0],df.high.max(),df.low.min(),df.close.iloc[-1]]
                expected=hourly.loc[clock,['open','high','low','close']].to_numpy(float)
                if not np.allclose(actual,expected,rtol=1e-7,atol=0):
                    raise ValueError(f'{s}: mismatched boundary hour {clock}')
        for sym,rule,observed,delta,t in groups:
            if s!=sym:continue
            end=t+horizon
            a=minutes[s,t.floor('h')];b=minutes[s,end.floor('h')]
            inside=hourly.loc[(hourly.index>t.floor('h'))&(hourly.index<end.floor('h'))]
            assert len(inside)==143
            held=pd.concat([a[a.index>=t],inside,b[b.index<end]])
            entry=a.loc[t,'open'];exit=b.loc[end,'open']
            out.append(dict(symbol=s,rule=rule,observed=observed,offset_days=delta,entry_time=t,exit_time=end,
                entry=entry,exit=exit,mfe_pct=100*(max(held.high.max(),exit)/entry-1),
                mae_pct=100*(1-min(held.low.min(),exit)/entry)))
    results=pd.DataFrame(out)
    px=np.vstack([results.entry,results.exit])
    pf=vbt.Portfolio.from_orders(px,price=px,size=np.vstack([np.ones(len(results)),-np.ones(len(results))]),
        fees=.0005,init_cash=4*px.max(axis=0),cash_sharing=False,allow_partial=False,raise_reject=True)
    records=pf.trades.records.sort_values('col')
    assert len(records)==len(results) and records.status.eq(1).all()
    results['net_pct']=records.pnl.to_numpy()/results.entry*100
    results.to_csv(DEST/'alert_major_paths.csv',index=False)
    stats=[]
    for (s,rule),x in results.groupby(['symbol','rule']):
        real=x[x.offset_days==0].set_index('observed')
        ctl=x[x.offset_days!=0].groupby('observed').net_pct.mean().reindex(real.index)
        stats.append(dict(symbol=s,rule=rule,episodes=len(real),profitable=int(real.net_pct.gt(0).sum()),
            mean_net_pct=real.net_pct.mean(),median_net_pct=real.net_pct.median(),worst_net_pct=real.net_pct.min(),
            timing_control_pct=ctl.mean(),mean_excess_pp=(real.net_pct-ctl).mean(),median_mfe_pct=real.mfe_pct.median(),
            median_mae_pct=real.mae_pct.median()))
    pd.DataFrame(stats).to_csv(DEST/'alert_major_metrics.csv',index=False)
    print(pd.DataFrame(stats).round(3).to_string(index=False))


if __name__=='__main__':main()

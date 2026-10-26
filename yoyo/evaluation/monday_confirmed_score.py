"""Frozen paper-score inputs on historical candles, without touch-bar leakage.

Implements the current monitor's feature windows: up to 300 preceding5m bars,
300 completed1h bars, 14-bar TR, 12-bar recent quote volume, 168/169 completed
hours, prior24h touches, and completed NY Monday. Price/volume features end
strictly before the touch5m open. Binance base volume times close approximates
quote volume; this is disclosed and never called identical OKX live data.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
MODEL=ROOT/'yoyo/monitor/monday_reclaim_score.json'
MIN5=300_000_000_000
HOUR=3_600_000_000_000


def five_bars(frame,step):
    """Return real complete5m buckets only; retain holes rather than fill."""
    if step==5:
        f=frame[['open','high','low','close','volume']].copy()
        f['qv']=f.volume*f.close
        return f
    r=frame.assign(qv=frame.volume*frame.close).resample('5min',closed='left',label='left')
    f=r.agg(dict(open='first',high='max',low='min',close='last',volume='sum',qv='sum'))
    return f[r.close.count().eq(5)].copy()


def hourly_bars(f):
    r=f.resample('1h',closed='left',label='left')
    h=r.agg(dict(open='first',high='max',low='min',close='last',qv='sum'))
    return h[r.close.count().eq(12)].copy()


def feature_row(five,hourly,side,high,low,level,monday_end_ns,touch_ns,opp_first,cluster):
    """Same bounded windows and formulas as monday_reclaim_paper.features.

Only the approach timestamp can use the touch bucket itself: at the observed
extension touch, crossing its nearer Monday edge is already known. No final
touch close, high, volume or confirmation price enters this score.
"""
    touch=touch_ns//MIN5*MIN5
    fi=five.index.as_unit('ns').asi8;hi=hourly.index.as_unit('ns').asi8
    k=int(np.searchsorted(fi,touch));j=int(np.searchsorted(hi+HOUR,touch,side='right'))
    pre=five.iloc[max(0,k-300):k];hours=hourly.iloc[max(0,j-300):j]
    if len(pre)<289 or len(hours)<169:raise ValueError('short_history')
    if not np.all(np.diff(pre.index.as_unit('ns').asi8)==MIN5):raise ValueError('pre5m_gap')
    if not np.all(np.diff(hours.index.as_unit('ns').asi8)==HOUR):raise ValueError('hourly_gap')
    pp=pre[['open','high','low','close','qv']].to_numpy()
    tr=np.maximum.reduce((pp[-14:,1]-pp[-14:,2],abs(pp[-14:,1]-pp[-15:-1,3]),abs(pp[-14:,2]-pp[-15:-1,3])))
    vol=float(tr.mean()/pp[-1,3]);last_hour=float(pp[-12:,4].sum())
    base=hours.qv.iloc[-169:-1].to_numpy();median=float(np.median(base))
    edge=low if side==1 else high
    # The live provider has at most300 5m rows including a developing touch
    # row. Use299 historical rows for the cross search plus observed touch.
    recent=five.iloc[max(0,k-299):k]
    cutoff=recent.index.as_unit('ns').asi8[0] if len(recent) else touch
    old=hours[(hours.index.as_unit('ns').asi8>=monday_end_ns)&(hours.index.as_unit('ns').asi8<cutoff)]
    crossed=old.index[(old.low<=edge) if side==1 else (old.high>=edge)].as_unit('ns').asi8.tolist()
    recent=recent[recent.index.as_unit('ns').asi8>=monday_end_ns]
    crossed+=recent.index[(recent.low<=edge) if side==1 else (recent.high>=edge)].as_unit('ns').asi8.tolist()
    # A genuine directional extension touch implies this nearer edge has
    # been crossed no later than now, without reading the remaining candle.
    crossed.append(int(touch))
    mon=hourly[hourly.index.as_unit('ns').asi8==monday_end_ns-HOUR]
    if len(mon)!=1:raise ValueError('monday_close_missing')
    pos=(float(mon.close.iloc[0])-low)/(high-low)
    return dict(bucket=math.floor(math.log2(vol)*4),
        log_vol_ratio=math.log(max(last_hour/median,.05)) if median>0 else math.log(.05),
        log_approach=math.log1p(max(0.,(touch-min(crossed))/60_000_000_000)),
        opp_first=int(opp_first),monday_close_side=(1-pos) if side==1 else pos,
        log_liquidity=math.log10(max(float(hours.qv.iloc[-168:].sum())/7,1e4)),
        toward24=-side*100*(pp[-1,3]/pp[-289,3]-1),cluster24=float(cluster),side=float(side),
        range_pct=100*(high-low)/level)


def score(features,spec):
    z=spec['intercept']+sum(c*(features[f]-m)/s for f,c,m,s in zip(spec['features'],spec['coef'],spec['mean'],spec['scale']))
    return 1/(1+math.exp(-max(-700,min(700,z))))


def touch_context(events):
    """Pre-touch opposite-side and market context; excludes simultaneous touches."""
    s=events[events.population.eq('signal')].drop_duplicates('event_id').copy()
    s['touch_ns']=s.entry_ns//MIN5*MIN5
    return s[['event_id','symbol','week','side','touch_ns']].sort_values('touch_ns').reset_index(drop=True)


def context_at(ctx,symbol,week,side,touch_ns):
    touch=touch_ns//MIN5*MIN5
    # A sorted timestamp slice bounds both searches; a week is at most8 days
    # away from its Monday anchor, including DST. Never scan the full universe.
    ts=ctx.touch_ns.to_numpy()
    end=int(np.searchsorted(ts,touch));start=int(np.searchsorted(ts,touch-8*24*HOUR))
    recent=ctx.iloc[start:end]
    opp=recent[(recent.symbol==symbol)&(recent.week==week)&(recent.side==-side)]
    recent=ctx.iloc[int(np.searchsorted(ts,touch-24*HOUR)):end]
    same=recent[(recent.side==side)&(recent.symbol!=symbol)]
    return bool(len(opp)),int(same.symbol.nunique())

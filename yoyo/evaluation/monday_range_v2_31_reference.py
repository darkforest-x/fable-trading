"""V2.31 causal admission oracle: the two-bar window is an expiry, not a delay.

Reuse the existing closed 15m arrows and Monday/Fib grid. Each candidate is
checked against the pre-arrow bodies and the arrow/touch interval available at
its decision close. No body after admission can cancel an admitted event.
Touches within two bars before, on, or after the arrow are eligible.
Enter at the earliest close where both arrow and qualifying touch are known.
Long keys must be <= the touch snapshot Monday midpoint; short keys must be
>= it. Equality is eligible. Apply this gate before candidate selection so a
rejected touch cannot consume an arrow or hide a later eligible touch.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def select_matches(quarter, arrows, prices, labels, weeks, *, before=5, wait_bars=2,
                   penetration=.10, buffer=.002, tick=.1,
                   timezone="America/New_York", start="2026-09-01T00:00:00+08:00",
                   bar_minutes=15, allowed_keys=None):
    """Return only decisions observable in this input prefix.

    Source columns: open/high/low/close, close-time index at bar_minutes; prices/weekly IDs
    must describe the range known before each touch opens. Previous bodies use
    [arrow-before, arrow); pairing bodies include both arrow and touch.
    Touch offsets range from -2 through wait_bars, relative to arrow.
    All selected indices are <= entry_i; later outcomes are never inspected.
    Optional allowed_keys restricts entry candidates before selection. Keep
    the complete price grid: columns 0/1 still define Monday's low/high and
    the midpoint, and this option does not change any exit-key snapshot.
    """
    if bar_minutes not in (5, 15):
        raise ValueError("Signal candles must be 5m or 15m")
    if not 0 <= before <= 20 or wait_bars not in (1, 2):
        raise ValueError("Invalid previous-body or touch-window settings")
    eligible = set(labels) if allowed_keys is None else set(allowed_keys)
    if not eligible.issubset(labels):
        raise ValueError("Unknown entry key")
    o,h,l,c=(quarter[k].to_numpy() for k in ("open","high","low","close"))
    lower,upper=np.minimum(o,c),np.maximum(o,c)
    n=len(quarter); rows=[]
    for i in np.flatnonzero(np.asarray(arrows)[:n]):
        i=int(i); side=int(arrows[i])
        if i<before:
            continue
        candidates=[]
        for j in range(max(0,i-2),min(n,i+wait_bars+1)):
            if weeks[i] is None or weeks[j]!=weeks[i]:
                continue
            wick=lower[j]-l[j] if side==1 else h[j]-upper[j]
            for k,p in enumerate(prices[j]):
                if labels[k] not in eligible:
                    continue
                outside=l[j]<=p<lower[j] if side==1 else upper[j]<p<=h[j]
                depth=p-l[j] if side==1 else h[j]-p
                midpoint = prices[j,0] + .5 * (prices[j,1] - prices[j,0])
                level_allowed = p <= midpoint if side == 1 else p >= midpoint
                if not outside or depth<wick*penetration or not level_allowed:
                    continue
                def clear(a,b):
                    return not np.any((lower[a:b]<=p)&(p<=upper[a:b]))
                if not (clear(min(i,j),max(i,j)+1) and clear(i-before,i)):
                    continue
                candidates.append((max(i,j),abs(i-j),j,k))
                break
        if not candidates:
            continue
        e,_,j,k=min(candidates)
        if not 1<=quarter.index[e].tz_convert(timezone).dayofweek<=4:
            continue
        raw=(l[j] if side==1 else h[j])-side*c[e]*buffer
        stop=(math.floor(raw/tick+1e-8) if side==1 else math.ceil(raw/tick-1e-8))*tick
        if stop<=0 or side*(c[e]-stop)<tick:
            continue
        if quarter.index[e]<pd.Timestamp(start):
            continue
        opened=lambda k: (quarter.index[k]-pd.Timedelta(minutes=bar_minutes)).tz_convert("Asia/Shanghai").isoformat()
        rows.append(dict(signal_i=i,touch_i=int(j),entry_i=int(e),side=side,
            signal_time=opened(i),touch_time=opened(j),entry_bar_time=opened(e),
            entry_time=quarter.index[e].tz_convert("Asia/Shanghai").isoformat(),
            key=labels[k],level=float(prices[j,k]),entry=float(c[e]),stop=round(stop,8)))
    return sorted(rows,key=lambda r:(r["entry_i"],r["signal_i"]))


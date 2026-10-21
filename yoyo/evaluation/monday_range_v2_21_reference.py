"""Causal candidate-level parity oracle for Pine V2.21, without trade PnL.

Owner rules: closed 15m OHLC, the existing Stoch arrows and Monday/Fib grid;
directional wick penetration >=10%, no body at the SAME candidate price during
pairing, before the arrow (default 5), or after it (default 2). A post window is
known only at its final close. Each price is filtered BEFORE selecting the first
admissible pairing; failed prices cannot consume another eligible candidate.
The existing Monday-grid and signal builders supply inputs, not new market data.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd


def select_matches(quarter, arrows, prices, labels, weeks, *, before=5, after=2,
                   penetration=.10, buffer=.002, tick=.1,
                   timezone="America/New_York", start="2026-09-01T00:00:00+08:00"):
    """Return only decisions observable in this input prefix.

    Source columns: open/high/low/close, close-time index; prices/weekly IDs
    must describe the range known before each touch opens. Previous bodies use
    [arrow-before, arrow); confirmation bodies use (arrow, arrow+after].
    All selected indices are <= entry_i; later outcomes are never inspected.
    """
    if not 0 <= before <= 20 or after not in (0, 1, 2):
        raise ValueError("Body windows must match the Pine input bounds")
    o,h,l,c=(quarter[k].to_numpy() for k in ("open","high","low","close"))
    lower,upper=np.minimum(o,c),np.maximum(o,c)
    n=len(quarter); rows=[]
    for i in np.flatnonzero(np.asarray(arrows)[:n]):
        i=int(i); side=int(arrows[i])
        if i<before or i+after>=n:
            continue
        candidates=[]
        for j in range(max(0,i-2),min(n,i+3)):
            if weeks[i] is None or weeks[j]!=weeks[i]:
                continue
            wick=lower[j]-l[j] if side==1 else h[j]-upper[j]
            for k,p in enumerate(prices[j]):
                outside=l[j]<=p<lower[j] if side==1 else upper[j]<p<=h[j]
                depth=p-l[j] if side==1 else h[j]-p
                if not outside or depth<wick*penetration or (side==-1 and p<prices[j,0]):
                    continue
                def clear(a,b):
                    return not np.any((lower[a:b]<=p)&(p<=upper[a:b]))
                if not (clear(min(i,j),max(i,j)+1) and clear(i-before,i)
                        and clear(i+1,i+after+1)):
                    continue
                candidates.append((max(i,j,i+after),abs(i-j),j,k))
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
        opened=lambda k: (quarter.index[k]-pd.Timedelta(minutes=15)).tz_convert("Asia/Shanghai").isoformat()
        rows.append(dict(signal_i=i,touch_i=int(j),entry_i=int(e),side=side,
            signal_time=opened(i),touch_time=opened(j),
            entry_time=quarter.index[e].tz_convert("Asia/Shanghai").isoformat(),
            key=labels[k],level=float(prices[j,k]),entry=float(c[e]),stop=round(stop,8)))
    return sorted(rows,key=lambda r:(r["entry_i"],r["signal_i"]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    # Reuse existing numerical definitions, in the existing isolated runtime.
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    from yoyo.evaluation.monday_range_v2_2_reference import signals
    q=pd.read_csv(args.source,index_col="close_time",parse_dates=True).loc["2026-08-24":]
    grid,names,weeks,_=monday_levels(q,[0,.5,.618,.705,1.13,1.27,1.618],"America/New_York")
    arrows=signals(q).to_numpy()
    args.out.mkdir(parents=True,exist_ok=True)
    for before,after in [(0,0),(5,0),(5,1),(5,2)]:
        rows=select_matches(q,arrows,grid,names,weeks,before=before,after=after)
        for n in sorted({r["entry_i"]+1 for r in rows}|{len(q)}):
            prefix=select_matches(q.iloc[:n],arrows[:n],grid[:n],names,weeks[:n],before=before,after=after)
            assert prefix==[r for r in rows if r["entry_i"]<n],(before,after,n)
        pd.DataFrame(rows).to_csv(args.out/f"signals_pre{before}_post{after}.csv",index=False)
        print(f"pre={before}, post={after}: {len(rows)} signals; causal prefixes passed",flush=True)


if __name__=="__main__":
    main()

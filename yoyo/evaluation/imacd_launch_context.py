"""Two independent causal startup diagnostics, with no trained thresholds.

Source: owner asked to test startup quality and higher-timeframe background
separately after formation/position. Breakout reads current confirmed close
and highs/lows over t-12..t-1. Higher context reads the most recent fully
closed higher bar available at LOCAL OPEN (not local close), matching the
existing Pine/monitor clock, with its own 340-bar seed warmup. Higher md/ATR
use that higher bar and earlier OHLC only. Unknown/stale context fails closed.
These are independent research gates; they are never stacked or deployed.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

HIGHER = {15:60,60:240,240:1440}


def add_context(bars, features, higher_bars, higher_features, minutes):
    if not bars.index.equals(features.index) or not higher_bars.index.equals(higher_features.index):
        raise ValueError('features and price clocks must align')
    if minutes not in HIGHER:
        raise ValueError('unsupported research timeframe')
    if not bars.index.is_monotonic_increasing or not higher_bars.index.is_monotonic_increasing:
        raise ValueError('chronological clocks required')
    f=features.copy()
    side=f.release_side
    top=bars.high.shift(1).rolling(12,min_periods=12).max()
    bottom=bars.low.shift(1).rolling(12,min_periods=12).min()
    distance=pd.Series(np.where(side>0,bars.close-top,np.where(side<0,bottom-bars.close,np.nan)),index=bars.index)
    f['prior_box_high']=top;f['prior_box_low']=bottom
    f['box_breakout']=distance.notna() & distance.gt(0)
    f['breakout_score']=distance/f.atr.shift(1).where(lambda x:x>0)
    higher_ms=HIGHER[minutes]*60000
    opens=bars.index.asi8//1000000
    closes=higher_bars.index.asi8//1000000+higher_ms
    j=np.searchsorted(closes,opens,side='right')-1
    safe=np.clip(j,0,max(0,len(closes)-1))
    hm=np.full(len(f),np.nan);ha=np.full(len(f),np.nan);used=np.full(len(f),np.nan)
    known=np.zeros(len(f),dtype=bool)
    if len(closes):
        hm=higher_features.md.to_numpy()[safe]
        ha=higher_features.atr.to_numpy()[safe]
        used=closes[safe].astype(float)
        known=(j>=340)&(closes[safe]==(opens//higher_ms)*higher_ms)&np.isfinite(hm)&np.isfinite(ha)&(ha>0)
    f['htf_known']=known
    f['htf_index']=np.where(known,j,-1)
    f['htf_md']=np.where(known,hm,np.nan)
    f['htf_atr']=np.where(known,ha,np.nan)
    f['htf_close_ms']=np.where(known,used,np.nan)
    f['htf_same_direction']=known & (hm*side.to_numpy()>0)
    f['htf_score']=f.htf_md*side.replace(0,np.nan)/f.htf_atr
    return f

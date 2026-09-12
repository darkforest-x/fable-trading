"""Matched-entry null for V1+ signal locations, separate from strategy returns.

Current/prior ATR, five-bar extremes and prior 120 ATR/price observations define
risk and volatility quartiles. Selection uses a deterministic identity hash,
never exit, profit or MFE. Both actual and random locations get the same
protection-only outcome: reference-close risk, next-open entry, prior-stop-first
execution, default arm protection, fixed fees. Raw opposite-reference exits are
omitted from BOTH sides of this benchmark; their actual performance is reported
only in the complete strategy replay. This null is not a tradable portfolio.
"""
from __future__ import annotations

import hashlib
import math
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import risk_reference
from yoyo.evaluation.spike_v1_plus_replay import _path_plus


def protection_outcome(a: dict, i: int, side: int, plus: bool, tick: float, last: int) -> dict | None:
    """Use future bars solely as an outcome; protection is known before each bar."""
    c = float(a["close"][i]); atr = float(a["atr"][i])
    extreme = float(a["recentLow" if side == 1 else "recentHigh"][i])
    ref = risk_reference(side,c,extreme,atr,tick=tick)
    if not ref.valid or i+1 > last: return None
    entry = float(a["open"][i+1]); risk = side*(entry-ref.stop)
    if not math.isfinite(risk) or risk <= 0: return None
    stop, peak, armed, s1, s2 = ref.stop, 0., False, False, False
    for j in range(i+1,last+1):
        alive, stop, peak, _, price, armed, s1, s2 = _path_plus(side,c,ref.risk,stop,peak,armed,s1,s2,
            float(a["open"][j]),float(a["high"][j]),float(a["low"][j]),float(a["close"][j]),float(a["atr"][j]),tick,plus)
        if not alive:
            net = side*(price/entry-1)-.002
            return dict(net_return=net,net_r=net/(risk/entry),censored=False)
    price = float(a["close"][last]); net=side*(price/entry-1)-.002
    return dict(net_return=net,net_r=net/(risk/entry),censored=True)


def matched_benchmark(context, trades: pd.DataFrame, config: dict) -> pd.DataFrame:
    """At most two target locations per stream/side/year/arm, chosen without outcomes."""
    b = context.cache["bars"]
    index = b.index
    step = pd.Timedelta(minutes=context.minutes)
    frac = b.atr/b.close
    q = pd.concat([frac.shift(1).rolling(120,min_periods=120).quantile(x) for x in (.25,.5,.75)],axis=1)
    ready = b.ready.astype(bool).to_numpy() & np.isfinite(q.to_numpy()).all(axis=1)
    bucket = (frac.to_numpy()[:,None] > q.to_numpy()).sum(axis=1)
    months = index.strftime("%Y-%m")
    a = {name:b[name].to_numpy(float) for name in ("open","high","low","close","atr","recentLow","recentHigh")}
    start, split, end = map(pd.Timestamp,(config["window_start"],config["development_end"],config["window_end"]))
    ts = trades.copy()
    ts["signal_bar_open"] = pd.to_datetime(ts.signal_bar_open,utc=True)
    ts["entry_time"] = pd.to_datetime(ts.entry_time,utc=True)
    ts["period"] = np.where(ts.entry_time < split,"development","validation")
    ts["hash"] = [hashlib.sha256(f'{config["matched_control_seed"]}|{context.key}|{t}|{s}'.encode()).hexdigest()
                  for t,s in zip(ts.signal_bar_open,ts.side)]
    ts = ts.sort_values("hash").groupby(["cohort","side","period"],sort=False).head(config["matched_max_targets_per_stream_side_year"])
    results = []; paths = {}
    for t in ts.itertuples(index=False):
        out = dict(stream_key=context.key,**context.identity,cohort=t.cohort,side=t.side,period=t.period,
                   target_time=t.signal_bar_open,matched=False,reason="unavailable")
        i = index.get_indexer([t.signal_bar_open])[0]
        if i < 0 or not ready[i]:
            results.append({**out,"reason":"target_history_missing"}); continue
        left,right = (start,split) if t.period=="development" else (split,end)
        valid = ready & (index+step>=left) & (index+step<right)
        # Use only complete contiguous entries. The next open is a fill, never a feature.
        contiguous = np.r_[np.diff(index.asi8)==step.value,False]
        choices = np.flatnonzero(valid & contiguous & (months==months[i]) & (bucket==bucket[i]))
        choices = choices[choices!=i]
        if not len(choices):
            results.append({**out,"reason":"no_same_month_bucket"}); continue
        j = int(choices[int(t.hash,16)%len(choices)])
        last = int(np.flatnonzero(index+step<=right)[-1])
        plus = t.cohort=="v1_plus_default_both"
        def path(k):
            key = (k,int(t.side),plus,last)
            if key not in paths: paths[key]=protection_outcome(a,k,int(t.side),plus,float(context.cache["tick"]),last)
            return paths[key]
        target, control = path(i), path(j)
        out.update(control_time=index[j],month=months[i],volatility_bucket=int(bucket[i]))
        if target is None or control is None:
            results.append({**out,"reason":"invalid_entry_risk"}); continue
        if target["censored"] or control["censored"]:
            results.append({**out,"reason":"boundary_unresolved"}); continue
        results.append({**out,"matched":True,"reason":"matched","target_net_r":target["net_r"],
                        "control_net_r":control["net_r"],"difference_net_r":target["net_r"]-control["net_r"],
                        "target_net_return":target["net_return"],"control_net_return":control["net_return"]})
    return pd.DataFrame(results)


def benchmark_summary(pairs: pd.DataFrame) -> pd.DataFrame:
    """Exploratory sign-flip at month level, avoiding treating venue copies as IID."""
    rows=[]
    if pairs.empty: return pd.DataFrame()
    for key,g in pairs.groupby(["cohort","timeframe_min","period"]):
        ok=g.loc[g.matched.eq(True)]
        blocks=ok.groupby("month").difference_net_r.mean().to_numpy() if len(ok) else np.array([])
        p=np.nan
        if len(blocks)>=3:
            observed=abs(blocks.mean())
            null=(np.random.default_rng(20260912).choice([-1.,1.],size=(9999,len(blocks)))*blocks).mean(axis=1)
            p=float((np.sum(np.abs(null)>=observed)+1)/10000)
        rows.append(dict(cohort=key[0],timeframe_min=key[1],period=key[2],targets=len(g),matched=len(ok),
                         month_blocks=len(blocks),mean_target_net_r=float(ok.target_net_r.mean()) if len(ok) else np.nan,
                         mean_control_net_r=float(ok.control_net_r.mean()) if len(ok) else np.nan,
                         equal_month_difference=float(blocks.mean()) if len(blocks) else np.nan,exploratory_sign_flip_p=p))
    return pd.DataFrame(rows)

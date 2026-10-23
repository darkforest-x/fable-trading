"""Parameterized causal preparation, derived from the frozen Monday cases2-4.

Only the owner-authorized confirmation count and prior-volume window vary.
Inputs: completed pin OHLC, completed next N confirmation OHLC, prior V complete
15m volumes, completed NY Monday anchors and prior14 completed5m TR/open for
matching. A pin-indexed mask becomes available only at pin open+(N+1)*TF.
No exit information is used. Baseline signal identities are checked against
the frozen candidate files by the study runner, without replaying old fills.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from yoyo.evaluation.monday_15m_volume_rejection import week_keys

STEP = pd.Timedelta(minutes=5)


def base_context(f, symbol, blocked=()):
    f = f.copy(deep=False)
    f.index = f.index.as_unit('ns')
    local, week = week_keys(f.index)
    mon = f[local.dayofweek == 0].assign(week=week[local.dayofweek == 0])
    anchors = mon.groupby('week').agg(high=('high','max'), low=('low','min'), n=('close','size'))
    anchors = anchors[anchors.n.ge(240 if symbol == 'XAUUSD' else 288)]
    anchors = anchors[~anchors.index.isin(blocked)]
    tr = pd.concat([f.high-f.low, (f.high-f.close.shift()).abs(), (f.low-f.close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean().shift()/f.open
    bucket = np.floor(np.log2(atr.where(atr > 0))*4).fillna(-999).to_numpy(int)
    return dict(f=f, symbol=symbol, local5=local, week5=week, anchors=anchors, bucket=bucket)


def prepare_case(base, case):
    f, symbol = base['f'], base['symbol']
    minutes = {2:15, 3:60, 4:240}[case]
    step = pd.Timedelta(minutes=minutes)
    group = f.resample(f'{minutes}min', closed='left', label='left')
    aggregation = dict(open='first', high='max', low='min', close='last')
    if case == 2:
        aggregation['volume'] = 'sum'
    h = group.agg(aggregation).dropna()
    h['bars'] = group.close.count().reindex(h.index)
    h['minutes'] = group['count'].sum().reindex(h.index)
    h.index = h.index.as_unit('ns')
    local, week = week_keys(h.index)
    complete = h.bars.eq(minutes//5) & h.minutes.eq(minutes)
    if case == 2:
        mon = h[local.dayofweek == 0].assign(week=week[local.dayofweek == 0])
        anchors = mon.groupby('week').agg(high=('high','max'),low=('low','min'),bars=('close','size'),source_count=('bars','sum'))
        anchors = anchors[anchors.bars.eq(96) & anchors.source_count.eq(288)]
    else:
        anchors = base['anchors']
    hi = pd.Series(week,index=h.index).map(anchors.high)
    lo = pd.Series(week,index=h.index).map(anchors.low)
    start = pd.DatetimeIndex(pd.to_datetime(week)).tz_localize('America/New_York').tz_convert('UTC').as_unit('ns')
    expected = (h.index.asi8-start.asi8)//step.value
    seen = pd.Series(np.arange(len(h)),index=h.index).groupby(week).cumcount()
    known = seen.eq(expected) & complete.groupby(week).cummin()
    if symbol == 'XAUUSD':
        known[:] = True
    sides = {}
    for side in (1, -1):
        level = lo-.618*(hi-lo) if side == 1 else hi+.618*(hi-lo)
        touched = (h.low.le(level) if side == 1 else h.high.ge(level)) & hi.notna() & (hi>lo) & (local.dayofweek>=1)
        first = touched & touched.astype(int).groupby(week).cumsum().eq(1)
        admission = first & known
        if case == 4:
            l5, w5 = base['local5'], base['week5']
            hi5 = pd.Series(w5,index=f.index).map(anchors.high)
            lo5 = pd.Series(w5,index=f.index).map(anchors.low)
            level5 = lo5-.618*(hi5-lo5) if side == 1 else hi5+.618*(hi5-lo5)
            touch5 = (f.low.le(level5) if side == 1 else f.high.ge(level5)) & hi5.notna() & (hi5>lo5) & (l5.dayofweek>=1)
            first5 = touch5 & touch5.astype(int).groupby(w5).cumsum().eq(1)
            first_times = pd.Series(f.index[first5],index=w5[first5])
            first_bars = first_times.dt.floor('4h').reindex(week).set_axis(h.index)
            if symbol == 'XAUUSD':
                known5 = pd.Series(True,index=f.index)
            else:
                start5 = pd.DatetimeIndex(pd.to_datetime(w5)).tz_localize('America/New_York').tz_convert('UTC').as_unit('ns')
                expected5 = (f.index.asi8-start5.asi8)//STEP.value
                seen5 = pd.Series(np.arange(len(f)),index=f.index).groupby(w5).cumcount()
                known5 = seen5.eq(expected5) & f['count'].eq(5).groupby(w5).cummin()
            known_times = pd.Series(known5[first5].to_numpy(),index=w5[first5])
            admission = pd.Series(h.index,index=h.index).eq(first_bars) & (h.index>=start+pd.Timedelta(days=1))
            admission &= known_times.reindex(week).set_axis(h.index).eq(True)
        body_min, body_max = h[['open','close']].min(axis=1), h[['open','close']].max(axis=1)
        if case == 2:
            pin = ((h.low.le(level) & h.open.ge(level) & h.close.gt(level) & h.low.lt(body_min)) if side == 1 else
                   (h.high.ge(level) & h.open.le(level) & h.close.lt(level) & h.high.gt(body_max)))
            inside = body_min if side == 1 else body_max
        else:
            pin = (h.low.le(level) & body_min.gt(level)) if side == 1 else (h.high.ge(level) & body_max.lt(level))
            inside = (h.low if side == 1 else h.high) if case == 3 else h.close
        confirms = complete.copy(); masks = {}
        for n in range(1,6):
            confirms &= complete.shift(-n,fill_value=False)
            confirms &= (h.index.to_series().shift(-n)-h.index.to_series()).eq(step*n)
            if case == 4:
                confirms &= inside.shift(-n).ge(level) if side == 1 else inside.shift(-n).le(level)
            else:
                confirms &= inside.shift(-n).gt(level) if side == 1 else inside.shift(-n).lt(level)
            masks[n] = (admission & pin & complete & confirms).copy()
        sides[side] = dict(level=level, masks=masks)
    volumes = {}
    if case == 2:
        for window in (10,20,30,40):
            prior = h.volume.shift().rolling(window,min_periods=window).max()
            history = complete.shift().rolling(window).sum().eq(window)
            history &= (h.index.to_series()-h.index.to_series().shift(window)).eq(step*window)
            volumes[window] = (h.volume.gt(0) & h.volume.ge(prior) & history, prior)
    l5, w5, bucket = base['local5'], base['week5'], base['bucket']
    eligible = (l5.dayofweek>=1) & (bucket!=-999) & np.r_[False,np.diff(f.index.asi8)==STEP.value]
    eligible &= (f.index.minute%15==0) if case == 2 else (f.index.minute==0)
    if case == 4:
        eligible &= f.index.hour%4==0
    pools = {}
    for i in np.flatnonzero(eligible):
        pools.setdefault((w5[i],bucket[i]),[]).append(i)
    return dict(base=base,case=case,h=h,step=step,week=week,hi=hi,lo=lo,sides=sides,volumes=volumes,pools=pools)


def candidates(ctx, confirmations, volume_window, cfg):
    base=ctx['base']; f=base['f']; h=ctx['h']; symbol=base['symbol']; rows=[]
    for side in (1,-1):
        mask=ctx['sides'][side]['masks'][confirmations].copy()
        if ctx['case']==2:
            mask &= ctx['volumes'][volume_window][0]
        for j in np.flatnonzero(mask.to_numpy()):
            when=h.index[j]+ctx['step']*(confirmations+1)
            i=f.index.get_indexer([when])[0]
            if i<0:continue
            entry=float(f.open.iloc[i]);stop=float(h.low.iloc[j] if side==1 else h.high.iloc[j])
            target=float(ctx['lo'].iloc[j] if side==1 else ctx['hi'].iloc[j]);risk=side*(entry-stop)
            if risk<=0 or side*(target-entry)<=0 or base['bucket'][i]==-999:continue
            pair=f'{symbol}-{i}-{side}'
            rows.append(dict(trade_id=pair,pair_id=pair,population='signal',symbol=symbol,
                entry_i=int(i),side=side,entry=entry,stop=stop,target_price=target,risk=risk,
                week=ctx['week'][j],bucket=int(base['bucket'][i]),entry_time=str(when),pin_time=str(h.index[j]),
                level=float(ctx['sides'][side]['level'].iloc[j]),segment=int(when>=pd.Timestamp(cfg['split']))))
    signals=pd.DataFrame(rows)
    if signals.empty:return signals
    rng=np.random.default_rng(cfg['seed']);controls=[]
    for r in signals.itertuples():
        pool=np.asarray(ctx['pools'].get((r.week,r.bucket),[]),dtype=int)
        pool=pool[pool!=r.entry_i]
        if len(pool)<cfg['controls_per_signal']:continue
        for k,i in enumerate(rng.choice(pool,cfg['controls_per_signal'],replace=False)):
            entry=float(f.open.iloc[i]);scale=entry/r.entry
            controls.append(dict(trade_id=f'{r.pair_id}-c{k}',pair_id=r.pair_id,population='random',symbol=symbol,
                entry_i=int(i),side=r.side,entry=entry,stop=entry-r.side*r.risk*scale,
                target_price=entry+(r.target_price-r.entry)*scale,risk=r.risk*scale,
                week=r.week,bucket=r.bucket,entry_time=str(f.index[i]),segment=int(f.index[i]>=pd.Timestamp(cfg['split']))))
    return pd.concat([signals,pd.DataFrame(controls)],ignore_index=True)

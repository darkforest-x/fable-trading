"""Entry-only rule ablations authorized by Owner on 2026-10-19.

The original Pine-compatible legacy and structural state machines are mirrored
with vectorized predicates. Removing a named rule removes every repeated use
in ENTRY detection; the caller retains original raw signals for exits and
retest cancellations. Stateful changes need not be supersets of the baseline.
All fields use OHLCV through the current close; no outcome is an input.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v9_htf_sma import side_gate

ARMS = ('baseline', 'no_density', 'no_bb', 'no_launch_volume', 'no_imacd',
        'no_advance', 'no_candle_shape', 'no_body_support', 'no_rope_cap',
        'no_volume_cap', 'no_h1', 'simple_breakout')
FEATURES = ('compression_run60', 'advance20_atr', 'relative_btc20_atr')


def context(frame, gap):
    """Original 12-bar density/parent, 3-bar launch and segmented volume fields."""
    f = frame
    segments = pd.Series(gap.astype(int).cumsum(), index=f.index)
    med = f.volume.groupby(segments).transform(lambda x: x.rolling(20, min_periods=20).median())
    d = {k: f[k].to_numpy(float) for k in
         ('open', 'high', 'low', 'close', 'atr', 'md', 'sb', 'middle', 'ropeHigh', 'ropeLow')}
    d.update(ready=f.ready.to_numpy(bool), gap=np.asarray(gap, bool),
             recent=(f.ready & f.pastWidth.le(3) & f.pastCrosses.ge(2)).shift().rolling(12, min_periods=12).sum().gt(0).to_numpy(),
             advance=((f.close-f.close.shift(3))/f.atr.shift(3)).to_numpy(),
             volume=(f.volume.rolling(3).sum()/(3*med.shift(3))).where(med.shift(3).gt(0)).to_numpy(),
             ph=f.high.shift().rolling(12, min_periods=12).max().to_numpy(),
             pl=f.low.shift().rolling(12, min_periods=12).min().to_numpy(),
             fast_hi=f[['s20', 'e20']].max(axis=1).to_numpy(),
             fast_lo=f[['s20', 'e20']].min(axis=1).to_numpy())
    return d


def legacy(d, side, omit='baseline', simple=False):
    """12-bar fresh breakout/cooldown and up-to-three-bar legacy confirmation."""
    c = d['close']; n = len(c)
    early = d['ready'] & ((c>d['ph']) & (c>d['fast_hi']) if side==1 else (c<d['pl']) & (c<d['fast_lo']))
    if simple:
        early = d['ready'] & ((c>d['ph']) if side==1 else (c<d['pl']))
    quality = d['ready'].copy()
    if omit!='no_density': quality &= d['recent']
    if omit!='no_launch_volume': quality &= d['volume']>=1.5
    if omit!='no_advance': quality &= side*d['advance']>=1.5
    if omit!='no_imacd':
        quality &= (side*(d['md']-d['sb'])>=0) & (side*(d['middle']-np.r_[np.nan,d['middle'][:-1]])>0)
    # A gap resets previous_early before evaluating this bar in the oracle.
    edges = early & (~np.r_[False,early[:-1]] | d['gap'])
    segment = np.cumsum(d['gap'])
    confirmed = np.zeros(n,bool); highs = np.full(n,np.nan); lows = highs.copy()
    last=-100; last_segment=-1
    for i in np.flatnonzero(edges):
        if segment[i]!=last_segment: last=-100
        last_segment=int(segment[i])
        if i-last<12: continue
        last=int(i)
        for j in ([i] if simple else range(i,min(n,i+4))):
            if segment[j]!=segment[i]: break
            if simple or (quality[j] and (c[j]>d['ph'][i] if side==1 else c[j]<d['pl'][i])):
                confirmed[j]=True; highs[j]=d['ph'][i]; lows[j]=d['pl'][i]
                break
    return confirmed, highs, lows


def structural(d, side, omit='baseline'):
    """Original pending parent/body/launch state; predicate ablations are local."""
    parent_event, ph, pl = legacy(d,side,omit)
    o,h,l,c = (d[k] for k in ('open','high','low','close'))
    md,sb,atr = (d[k] for k in ('md','sb','atr'))
    rope = d['ropeHigh' if side==1 else 'ropeLow']
    valid = (~d['gap'] & d['ready'] & np.isfinite(md) & np.isfinite(sb)
             & np.isfinite(atr) & np.isfinite(rope) & (atr>0))
    span=h-l
    shape=(side*(c-o)>0) & (span>0) & (np.abs(c-o)>=.55*span) & ((c-l if side==1 else h-c)>=.75*span)
    if omit=='no_candle_shape': shape=np.ones(len(c),bool)
    envelope=np.isfinite(d['advance']) & np.isfinite(d['volume'])
    if omit!='no_advance': envelope &= side*d['advance']>=1.5
    if omit!='no_launch_volume': envelope &= d['volume']>=1.5
    beyond=side*(c-rope)>0
    body=(np.minimum(o,c)>rope if side==1 else np.maximum(o,c)<rope)
    momentum=(side*(md-sb)>0) & (side*(md-np.r_[np.nan,md[:-1]])>0)
    if omit=='no_imacd': momentum=np.ones(len(c),bool)
    out=np.zeros(len(c),bool); pending=evidence=support=False
    hi=lo=np.nan; segment_start=0
    for i in range(len(c)):
        if not valid[i]:
            pending=evidence=support=False;hi=lo=np.nan;segment_start=i+1
            continue
        if not beyond[i]: support=False
        elif body[i]: support=True
        if parent_event[i]:
            hi,lo,pending=ph[i],pl[i],True
            evidence=bool(envelope[i] and shape[max(segment_start,i-2):i+1].any())
        elif pending and shape[i] and envelope[i]: evidence=True
        if pending and (c[i]<lo if side==1 else c[i]>hi):
            pending=evidence=False;hi=lo=np.nan
        elif (pending and evidence and (support or omit=='no_body_support') and beyond[i]
              and (c[i]>hi if side==1 else c[i]<lo) and momentum[i]):
            out[i]=True;pending=evidence=False
    return out


def bb_fields(frame, gap):
    """BB200 width versus prior 500 P10; prior run only, 712-bar availability."""
    c=frame.close;n=len(c)
    age=np.arange(n)-np.maximum.accumulate(np.where(gap,np.arange(n),0))+1
    width=4*c.rolling(200,min_periods=200).std(ddof=0)/c.rolling(200,min_periods=200).mean().abs()
    threshold=width.shift().rolling(500,min_periods=500).quantile(.1,interpolation='linear')
    compressed=(age>=700)&width.le(threshold).to_numpy()
    runs=np.arange(n)-np.maximum.accumulate(np.where(~compressed,np.arange(n),-1))
    # Clip each candidate run at the left edge of this precise prior60 window.
    duration=np.zeros(n,float)
    for offset in range(1,61):
        if offset<n:
            duration[offset:]=np.maximum(duration[offset:],np.minimum(runs[:-offset],61-offset))
    duration[:60]=np.nan
    prior=np.zeros(n,bool)
    for offset in range(1,11):
        s=pd.Series(compressed)
        prior |= (s.shift(offset,fill_value=False)&s.shift(offset+1,fill_value=False)&s.shift(offset+2,fill_value=False)).to_numpy()
    available=(age>=712)&threshold.notna().to_numpy()
    return available, available&prior, duration


def rules(frame,gap,asset,minutes,h1=None):
    """All ablation memberships; baseline must exactly match archived facts.

    Six-MA density is the combined prior-width and crossing gate, not removal
    of the six-MA price envelope. IMACD includes legacy MD/SB/middle slope and
    final MD/SB/MD slope. Shape includes all repeated launch-body checks.
    Simple breakout is a deliberately multi-rule benchmark, not an ablation.
    Calendar/asset/valid-data gates are unchanged. no_bb retains BB warmup.
    """
    d=context(frame,gap);available,bb,duration=bb_fields(frame,gap)
    clock=frame.index+pd.Timedelta(minutes=minutes)
    rv=frame.rv.to_numpy()
    always=d['ready'] & ~gap & (asset not in ('','USDC')) & np.isfinite(rv) & (rv>=0) & np.asarray(clock.dayofweek!=6)
    base_raw={s:structural(d,s) for s in (1,-1)}
    assert not (base_raw[1]&base_raw[-1]).any()
    output={}
    structural_arms={'no_density','no_launch_volume','no_imacd','no_advance','no_candle_shape','no_body_support'}
    for arm in ARMS:
        for s in (1,-1):
            raw=(structural(d,s,arm) if arm in structural_arms else
                 legacy(d,s,simple=True)[0] if arm=='simple_breakout' else base_raw[s])
            mask=raw & always
            if arm=='simple_breakout': mask &= available
            else:
                mask &= available if arm=='no_bb' else bb
                if arm!='no_rope_cap':
                    with np.errstate(divide='ignore',invalid='ignore',over='ignore'):
                        distance=s*(d['close']-d['ropeHigh' if s==1 else 'ropeLow'])/d['atr']
                    mask &= np.isfinite(distance) & (distance<=3)
                if minutes==15 and arm!='no_h1': mask &= side_gate(d['close'],np.full(len(frame),s),h1)
            if arm!='no_volume_cap': mask &= rv<=50
            output[arm,s]=mask
    return output,np.where(base_raw[1],1,np.where(base_raw[-1],-1,0)),duration


def state_features(frame,gap,btc,duration,minutes):
    """Anchor-close features, never future confirmation or final MAE/MFE.

    compression_run60: max prior-60 compressed run, capped at60; original
    BB200/prior500 calculation. advance20: close[t]-close[t-20] / ATR[t-20].
    relative_btc20: difference of contemporaneous 20-bar log returns divided
    by coin ATR[t-20]/close[t-20]. Directions are applied when rows are emitted.
    Exact timestamp endpoints and all 21 contiguous coin/BTC bars are required.
    """
    c=frame.close;lag=c.shift(20);a=frame.atr.shift(20)
    delta=frame.index.to_series().diff().eq(pd.Timedelta(minutes=minutes))
    continuity=delta.rolling(20,min_periods=20).min().eq(1)&~pd.Series(gap,index=c.index).rolling(21,min_periods=21).max().eq(1)
    b=btc.reindex(c.index)
    bvalid=b.notna().rolling(21,min_periods=21).sum().eq(21)
    return pd.DataFrame(dict(compression_run60=duration,
        advance20_atr=((c-lag)/a).where(continuity & a.gt(0)),
        relative_btc20_atr=((np.log(c/lag)-np.log(b/b.shift(20)))/(a/lag)).where(continuity & bvalid & a.gt(0))),index=c.index)

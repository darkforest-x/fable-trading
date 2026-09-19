"""Causal six-MA density diagnostics, independent of future trade outcomes.

Owner 2026-09-20 asks to decompose V9 density. Inputs are existing SMA/EMA
20/60/120, OHLC, ATR and ready. A density decision at t summarizes t-12..t-1;
the stable ATR reference is median(t-76..t-13), strictly before that core.
Relative width uses price, with a prior256 reference ending t-13. No future
price, trade result, fitted label or production parameter is used here.
"""
from __future__ import annotations

from itertools import combinations
import numpy as np
import pandas as pd

MAS = ("s20", "e20", "s60", "e60", "s120", "e120")
WIDTHS = (0.5, 1., 1.5, 2.)
ARM_NAMES = ("legacy", "width_0.5", "width_1", "width_1.5", "width_2", "max_width_3",
             "stable_atr_3", "distinct_pairs_2", "group_edges_2", "coverage_10of12",
             "relative_p20", "contraction_20pct")


def diagnostics(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """All returned core fields end at t-1; current width is diagnostic only.

    Missing timestamps or invalid OHLC invalidate local windows and impose a
    340-bar rewarm for comparisons. Input legacy MA values are not recomputed,
    preserving archived V9 semantics; EMA state may retain pre-gap history.
    """
    if not isinstance(frame.index,pd.DatetimeIndex) or not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("Require unique increasing chart clock")
    out=pd.DataFrame(index=frame.index)
    valid=np.isfinite(frame[[*MAS,"open","high","low","close","atr"]]).all(axis=1) & frame.atr.gt(0) & frame.low.gt(0)
    valid &= frame.high.ge(frame[["open","close","low"]].max(axis=1)) & frame.low.le(frame[["open","close","high"]].min(axis=1))
    gap=frame.index.to_series().diff().ne(pd.Timedelta(minutes=minutes)) | ~valid | ~valid.shift(1,fill_value=False)
    segment=gap.cumsum()
    count=valid.groupby(segment).cumsum()
    span=frame[list(MAS)].max(axis=1,skipna=False)-frame[list(MAS)].min(axis=1,skipna=False)
    width=(span/frame.atr).where(valid)
    out["mean_atr12"]=width.shift(1).rolling(12,min_periods=12).mean()
    out["max_atr12"]=width.shift(1).rolling(12,min_periods=12).max()
    out["width_now_atr"]=width
    out["core_end_width_atr"]=width.shift(1)
    out["stable_atr"]=frame.atr.shift(13).rolling(64,min_periods=64).median()
    out["stable_mean12"]=span.shift(1).rolling(12,min_periods=12).mean()/out.stable_atr
    out["stable_max12"]=span.shift(1).rolling(12,min_periods=12).max()/out.stable_atr
    out["stable_last3_max"]=span.shift(1).rolling(3,min_periods=3).max()/out.stable_atr
    out["coverage3_12"]=width.le(3).astype(int).shift(1).rolling(12,min_periods=12).sum()
    out["core_mean_atr_to_ref"]=frame.atr.shift(1).rolling(12,min_periods=12).mean()/out.stable_atr
    price_width=(span/frame.close).where(valid)
    out["median_price_width12"]=price_width.shift(1).rolling(12,min_periods=12).median()
    out["price_width_prior_p20"]=price_width.shift(13).rolling(256,min_periods=256).quantile(.2)
    old=price_width.shift(9).rolling(4,min_periods=4).median()
    new=price_width.shift(1).rolling(4,min_periods=4).median()
    out["contraction_ratio"]=new/old.where(old.gt(0))
    out["price_near_core"]=(frame.close-(frame[list(MAS)].max(axis=1)+frame[list(MAS)].min(axis=1))/2).abs().shift(1).rolling(3,min_periods=3).max()/out.stable_atr
    flip_cols={}; group_cols={}; pair_recent=[]
    for a,b in combinations(range(6),2):
        d=frame[MAS[a]]-frame[MAS[b]]
        flip=((d.gt(0)&d.shift(1).le(0))|(d.lt(0)&d.shift(1).ge(0))) & ~gap
        flip_cols[a,b]=flip.astype(int)
        roll=flip.astype(int).shift(1).rolling(12,min_periods=12).sum()
        pair_recent.append(roll.gt(0))
        if a//2 != b//2:
            group_cols.setdefault((a//2,b//2),[]).append(flip)
    out["cross_count12"]=pd.DataFrame(flip_cols).sum(axis=1).shift(1).rolling(12,min_periods=12).sum()
    out["distinct_pairs12"]=pd.concat(pair_recent,axis=1).sum(axis=1)
    edges=[]
    for values in group_cols.values():
        edges.append(pd.concat(values,axis=1).any(axis=1).astype(int).shift(1).rolling(12,min_periods=12).sum().gt(0))
    out["group_edges12"]=pd.concat(edges,axis=1).sum(axis=1)
    out["known"]=frame.ready.fillna(False).astype(bool) & valid & count.ge(340) & out.price_width_prior_p20.notna()
    # Raw legacy can be traced separately; comparisons use the shared known set.
    out["legacy_raw"]=frame.ready.fillna(False).astype(bool) & out.mean_atr12.le(3) & out.cross_count12.ge(2)
    out["legacy_recent12"]=out.legacy_raw.shift(1).rolling(12,min_periods=12).max().eq(1)
    out["legacy_recent3"]=out.legacy_raw.shift(1).rolling(3,min_periods=3).max().eq(1)
    out["legacy_recent6"]=out.legacy_raw.shift(1).rolling(6,min_periods=6).max().eq(1)
    positions=np.arange(len(out),dtype=float)
    last=pd.Series(np.where(out.legacy_raw,positions,np.nan),index=out.index).ffill()
    out["legacy_age"]=positions-last
    out["legacy"]=out.legacy_raw & out.known
    for cap in WIDTHS:
        out[f"width_{cap:g}"]=out.known & out.mean_atr12.le(cap) & out.cross_count12.ge(2)
    out["max_width_3"]=out.known & out.max_atr12.le(3) & out.cross_count12.ge(2)
    out["stable_atr_3"]=out.known & out.stable_mean12.le(3) & out.cross_count12.ge(2)
    out["distinct_pairs_2"]=out.known & out.mean_atr12.le(3) & out.distinct_pairs12.ge(2)
    out["group_edges_2"]=out.known & out.mean_atr12.le(3) & out.group_edges12.ge(2)
    out["coverage_10of12"]=out.legacy & out.coverage3_12.ge(10)
    out["relative_p20"]=out.legacy & out.median_price_width12.le(out.price_width_prior_p20)
    out["contraction_20pct"]=out.legacy & out.contraction_ratio.le(.8)
    return out


def phenotypes(d):
    """Overlapping mechanism flags, NOT truth labels or classifier accuracy."""
    return pd.DataFrame({
        "wide_over_1atr":d.legacy & d.mean_atr12.gt(1),
        "same_pair_repeat":d.legacy & d.distinct_pairs12.eq(1),
        "within_length_only":d.legacy & d.group_edges12.eq(0),
        "not_all_groups_connected":d.legacy & d.group_edges12.lt(2),
        "mean_hides_over3":d.legacy & d.max_atr12.gt(3),
        "atr_denominator_sensitive":d.legacy & d.stable_mean12.gt(3),
        "relative_not_low":d.legacy & d.median_price_width12.gt(d.price_width_prior_p20),
        "expanding_over_50pct":d.legacy & d.contraction_ratio.gt(1.5),
        "recent_but_not_now":d.known & d.legacy_recent12 & ~d.legacy_raw,
        "compact_without_cross":d.known & ~d.legacy_raw & d.stable_max12.le(1) & d.cross_count12.lt(2),
        "compact_interwoven":d.known & d.stable_max12.le(1) & d.group_edges12.ge(2),
        "compact_converging":d.known & d.stable_last3_max.le(1) & d.contraction_ratio.le(.8),
    },index=d.index)

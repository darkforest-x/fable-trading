"""Strict realized net-R > 5 statistics for the versioned V5 study.

Source: V3 spike_10r_search and spike_10r_serial statistics, copied with only
threshold and output field names changed. Uses future closed-path net_r only
as an evaluation label; no function computes an entry feature. Invalid and
censored paths remain unknown. Original returns, costs, monthly paired-null
and bootstrap seeds are preserved for a target-only comparison against V4.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_10r_search import SEED, SPLIT
from yoyo.evaluation.spike_high_r_entry_report import block_test


def control_statistics(part, controls, period):
    """Same-stream/month/ATR-bucket random timing; censored pairs never redraw."""
    closed = part.loc[part.valid_entry & ~part.censored]
    pair = closed[['event_key','entry_time','net_r','net_return']].merge(controls,on='event_key',how='left',validate='one_to_one',suffixes=('','_controlmeta'))
    if len(pair) and not np.allclose(pair.net_r,pair.target_net_r,equal_nan=True):
        raise ValueError('missing control or target drift')
    ok = pair.matched.fillna(False) & ~pair.control_censored.fillna(True)
    if period=='earlier':
        ok &= pair.control_entry_time.lt(SPLIT) & pair.control_exit_time.lt(SPLIT)
    elif period=='later':
        ok &= pair.control_entry_time.ge(SPLIT)
    p = pair.loc[ok].copy()
    p['r_delta'] = p.net_r-p.control_net_r
    p['tail_delta'] = p.net_r.gt(5).astype(int)-p.control_net_r.gt(5).astype(int)
    month = p.entry_time.dt.strftime('%Y-%m')
    rtest = block_test(p.groupby(month).r_delta.sum())
    ttest = block_test(p.groupby(month).tail_delta.sum())
    return dict(matched_pairs=len(p),unmatched_closed=len(pair)-len(p),
        random_gt5=int(p.control_net_r.gt(5).sum()),paired_gt5=int(p.net_r.gt(5).sum()),
        random_precision=float(p.control_net_r.gt(5).mean()),
        random_mean_net_bp=float(p.control_net_return.mean()*1e4),
        paired_excess_r=float(p.r_delta.mean()),paired_excess_net_bp=float((p.net_return-p.control_net_return).mean()*1e4),
        random_tail_p=ttest['p'],random_net_p=rtest['p'])


def metrics(part, universe):
    closed = part.loc[part.valid_entry & ~part.censored]
    all_closed = universe.loc[universe.valid_entry & ~universe.censored]
    r = closed.net_r
    h, base_h = int(r.gt(5).sum()),int(all_closed.net_r.gt(5).sum())
    return dict(candidates=len(part),invalid=int((~part.valid_entry).sum()),censored=int((part.valid_entry & part.censored).sum()),
        closed=len(closed),gt5=h,precision=float(r.gt(5).mean()),
        confirmed_gt5_per_candidate=h/len(part) if len(part) else np.nan,
        candidate_coverage=len(part)/len(universe) if len(universe) else np.nan,coverage=len(closed)/len(all_closed) if len(all_closed) else np.nan,
        recall=h/base_h if base_h else np.nan,win_rate=float(r.gt(0).mean()),
        mean_r=float(r.mean()),mean_gross_bp=float(closed.gross_return.mean()*1e4),mean_net_bp=float(closed.net_return.mean()*1e4),
        assets=closed.asset.nunique(),positive_asset_months=closed.loc[r.gt(5)].assign(month=closed.entry_time.dt.strftime('%Y-%m')).groupby(['asset','month']).ngroups)


def rate_interval(universe, selected):
    """Pair-month bootstrap of ratio differences, retaining empty selected months."""
    u = universe.loc[universe.valid_entry & ~universe.censored].copy()
    s = selected.loc[selected.valid_entry & ~selected.censored].copy()
    for frame in (u,s):
        frame['month'] = frame.entry_time.dt.strftime('%Y-%m')
        frame['tail'] = frame.net_r.gt(5).astype(int)
    months = sorted(u.month.unique())
    if not months or not len(s):
        return dict(precision_delta=np.nan,precision_ci_low=np.nan,precision_ci_high=np.nan)
    count = []
    for frame in (u,s):
        count.append(frame.groupby('month').agg(n=('tail','size'),h=('tail','sum')).reindex(months,fill_value=0).to_numpy(float))
    a = np.array(count)
    rng = np.random.default_rng(SEED)
    draws = a[:,rng.integers(len(months),size=(4000,len(months))),:].sum(axis=2)
    valid = (draws[:,:,0]>0).all(axis=0)
    difference = draws[1,valid,1]/draws[1,valid,0]-draws[0,valid,1]/draws[0,valid,0]
    lo,hi = np.quantile(difference,[.025,.975])
    return dict(precision_delta=float(s['tail'].mean()-u['tail'].mean()),precision_ci_low=float(lo),precision_ci_high=float(hi))


def serial_retention(part, baseline):
    """Count retained original winners by identity, separating newly opened wins."""
    def winners(frame):
        return set(frame.loc[frame.valid_entry & ~frame.censored & frame.net_r.gt(5),'event_key'])
    old,new=winners(baseline),winners(part)
    return dict(retained_gt5=len(old & new),lost_gt5=len(old-new),gained_gt5=len(new-old),
                recall=len(old & new)/len(old) if old else np.nan,
                gt5_count_ratio=len(new)/len(old) if old else np.nan)

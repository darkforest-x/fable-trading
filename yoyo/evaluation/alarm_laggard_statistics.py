"""Alarm/week grouped comparisons for observational, interval-censored outcomes.

Random candidate means are exact finite-pool expectations within each alarm,
volume tertile and volatility tertile. Weekly sign randomization is a paired
symmetry diagnostic, not a randomized treatment or coin-independent test.
No price path here is converted to fills, returns, fees or account equity.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def interval_rates(frame, prefix='', denominator=None):
    """Candidate-weighted partial-identification intervals; conditional recovery."""
    n = len(frame) if denominator is None else float(frame[denominator].sum())
    totals = {k:float(frame[prefix+k].sum()) for k in ['deep_lower','deep_upper','joint_lower','joint_upper']}
    rates = {k: v/n if n else None for k,v in totals.items()}
    rates['conditional_recovery_lower'] = totals['joint_lower']/totals['deep_upper'] if totals['deep_upper'] else None
    rates['conditional_recovery_upper'] = min(1., totals['joint_upper']/totals['deep_lower']) if totals['deep_lower'] else (1. if totals['deep_upper'] else None)
    return dict(n=n, counts=totals, rates=rates)


def weekly_interval(lower, upper, weeks, cfg):
    """Jointly resample calendar weeks; preserve all candidates from each alarm."""
    frame = pd.DataFrame(dict(week=weeks, lower=lower, upper=upper, n=1))
    grouped = frame.groupby('week')[['lower','upper','n']].sum()
    if grouped.empty:
        return dict(alarms=0,weeks=0,lower_mean=None,upper_mean=None,ci95=None,lower_sign_p=None)
    a = grouped.to_numpy(float)
    rng = np.random.default_rng(cfg['statistics']['seed'])
    indices = rng.integers(0,len(a),(cfg['statistics']['bootstrap_repeats'],len(a)))
    totals = a[indices].sum(axis=1)
    low_boot = totals[:,0]/totals[:,2]; up_boot = totals[:,1]/totals[:,2]
    signs = rng.choice([-1,1],(cfg['statistics']['permutation_repeats'],len(a)))
    null = signs @ a[:,0]
    observed = a[:,0].sum()
    p = (1+int((null >= observed-1e-14).sum()))/(len(null)+1)
    return dict(alarms=int(a[:,2].sum()), weeks=len(a),
                lower_mean=float(observed/a[:,2].sum()),upper_mean=float(a[:,1].sum()/a[:,2].sum()),
                ci95=[float(np.quantile(low_boot,.025)),float(np.quantile(up_boot,.975))],
                lower_sign_p=float(p))


def holm(values):
    """Holm family adjustment with the original key order preserved."""
    valid = sorted([(k,v) for k,v in values.items() if v is not None],key=lambda x:x[1])
    out, last = {}, 0.
    for rank,(key,value) in enumerate(valid):
        last = max(last,min(1.,value*(len(valid)-rank)))
        out[key] = last
    return out


def summarize(records, candidates, alarms, cfg, out):
    """Yearly and pooled fixed-policy frequencies; same alarm eligibility in pairs."""
    alarm_data = alarms.copy()
    dates = pd.to_datetime(alarm_data.alarm_ms,unit='ms',utc=True)
    alarm_data['year'] = dates.dt.year
    alarm_data['week'] = ((alarm_data.alarm_ms//86400000+3)//7).astype(int)
    results = {}
    for method in ('six','btc'):
        results[method] = {}
        for period in ('2025','2026','pooled','design_day'):
            mask = alarm_data[method].copy()
            if period=='design_day': mask &= alarm_data.design_day
            else:
                mask &= ~alarm_data.design_day
                if period!='pooled': mask &= alarm_data.year.eq(int(period))
            ids = alarm_data.loc[mask,'alarm_id']
            c = candidates[candidates.alarm_id.isin(ids)]
            r = records[records.alarm_id.isin(ids)].merge(alarm_data[['alarm_id','week']],on='alarm_id')
            # Object bools can arise from concatenating empty CSV frames; never
            # invert an object Series, where ~True is the integer -2.
            not_deep = c.not_deep.astype('boolean').astype(bool)
            entry = dict(all_alarms=len(ids), alarms_with_rankable_coins=int(c.alarm_id.nunique()),
                         candidate_universe_not_deep=interval_rates(c[not_deep]),
                         already_deep_comparator=interval_rates(c[~not_deep]), rules={})
            for rule in cfg['selection']['rules']:
                f = r[r.rule==rule].copy()
                matched = f[f.matched_n>0]
                detail = dict(alarms_with_selection=len(f),matched_alarms=len(matched),
                              selected_n=int(f.selected_n.sum()),matched_n=int(f.matched_n.sum()),
                              unmatched_n=int(f.unmatched_n.sum()),selected_ambiguous=int(f.selected_ambiguous.sum()),
                              selected_censored=int(f.selected_censored.sum()),
                              selected=interval_rates(f,'selected_','selected_n'),
                              matched_selected=interval_rates(f,'matched_','matched_n'),
                              matched_random_expected=interval_rates(f,'control_','matched_n'),excess={})
                for outcome in ('deep','joint'):
                    lo = matched['matched_'+outcome+'_lower']/matched.matched_n - matched['control_'+outcome+'_upper']/matched.matched_n
                    hi = matched['matched_'+outcome+'_upper']/matched.matched_n - matched['control_'+outcome+'_lower']/matched.matched_n
                    detail['excess'][outcome] = weekly_interval(lo,hi,matched.week,cfg)
                entry['rules'][rule] = detail
            a = r[r.rule=='lag'].set_index('alarm_id')
            b = r[r.rule=='lag_then_recovery'].set_index('alarm_id').reindex(a.index)
            entry['recovery_stage_increment'] = {}
            for outcome in ('deep','joint'):
                lo = b['selected_'+outcome+'_lower']/b.selected_n - a['selected_'+outcome+'_upper']/a.selected_n
                hi = b['selected_'+outcome+'_upper']/b.selected_n - a['selected_'+outcome+'_lower']/a.selected_n
                entry['recovery_stage_increment'][outcome] = weekly_interval(lo.to_numpy(),hi.to_numpy(),a.week.to_numpy(),cfg)
                entry['recovery_stage_increment'][outcome].pop('lower_sign_p',None)
            results[method][period] = entry
    family = {rule+'/'+outcome:results['six']['pooled']['rules'][rule]['excess'][outcome]['lower_sign_p']
              for rule in cfg['selection']['rules'] for outcome in ('deep','joint')}
    adjusted = holm(family)
    for name,p in adjusted.items():
        rule,outcome=name.split('/')
        results['six']['pooled']['rules'][rule]['excess'][outcome]['holm_p'] = p
    # Small directly inspectable table; labels remain bounds rather than guessed points.
    flat = []
    for method,periods in results.items():
        for period,entry in periods.items():
            for rule,d in entry['rules'].items():
                flat.append(dict(method=method,period=period,rule=rule,alarms=entry['all_alarms'],
                                 selected_n=d['selected_n'],matched_n=d['matched_n'],
                                 **{'selected_'+k:v for k,v in d['selected']['rates'].items()},
                                 **{'random_'+k:v for k,v in d['matched_random_expected']['rates'].items()},
                                 joint_excess_lower=d['excess']['joint']['lower_mean'],
                                 joint_excess_upper=d['excess']['joint']['upper_mean'],
                                 joint_ci_lower=(d['excess']['joint']['ci95'] or [None,None])[0],
                                 joint_ci_upper=(d['excess']['joint']['ci95'] or [None,None])[1]))
    pd.DataFrame(flat).to_csv(out/'headline.csv',index=False)
    return dict(results=results, frozen_config=cfg,
                limitations=['Available archived crypto cohort,not a complete point-in-time exchange universe.',
                             '5m snapshots are1to241seconds old at alarm; current-minute lag and exact order timing are unobserved.',
                             'First-touch and recovery labels retain bar-boundary and missing-data uncertainty; no simulated fills.',
                             'Yearly chronological historical evidence,not untouched prospective validation.',
                             'Matched random pool means are exact expectation; week-sign p assumes paired symmetry and is not a causal-treatment claim.'])

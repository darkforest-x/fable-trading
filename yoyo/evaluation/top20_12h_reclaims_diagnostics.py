"""Diagnose control selection and price-path cleanliness using the frozen census.

No rescanning, fitting or fills. Each comparison changes one control restriction
from the declared samecoin/week/side/priorvol pool. Close-to-level distance uses
signal-close information and prior14 TR. Future path metrics are labels only.
The baseline's large excess must not be mistaken for absolute profitability.
"""
import json
import subprocess
import sys
import numpy as np
import pandas as pd
from yoyo.evaluation.top20_12h_reclaims import EXP, compare, dump


def run():
    assert not (EXP/'control_diagnostics.json').exists()
    obs=pd.read_csv(EXP/'observations.csv.gz',float_precision='round_trip')
    events=obs[obs.is_event&obs.kind.eq('pure_wick')&obs.return_pct_6.notna()].copy()
    obs['distance_bucket']=pd.cut(obs.side*(obs.close-obs.level)/obs.tr_prior14,
        [0,.25,.5,1,2,4,np.inf],include_lowest=True,labels=False)
    lookup=obs.set_index('event_id');pool=obs[obs.inside&obs.vol_bucket.notna()&obs.return_pct_6.notna()]
    groups={k:g for k,g in pool.groupby(['symbol','week','side','vol_bucket'])};rows=[]
    for e in events.itertuples():
        g=groups.get((e.symbol,e.week,e.side,e.vol_bucket))
        if g is None:continue
        g=g[g.event_id.ne(e.event_id)]
        variants={'baseline':g,'controls_already_touched':g[g.prior_touch_count.ge(1)],
            'controls_within_24h':g[(g.bar_i-e.bar_i).abs().le(2)],
            'same_close_distance_bucket':g[g.distance_bucket.eq(lookup.loc[e.event_id,'distance_bucket'])],
            'non_reclaim_controls':g[~g.is_event]}
        for name,v in variants.items():
            if v.empty:continue
            rows.append(dict(variant=name,event_id=e.event_id,symbol=e.symbol,week=e.week,side=e.side,
                signal_pct=e.return_pct_6,random_pct=float(v.return_pct_6.mean()),signal_positive=float(e.return_pct_6>0),random_positive=float(v.positive_6.mean()),controls=len(v),control_ids=';'.join(v.event_id)))
    pairs=pd.DataFrame(rows);pairs.to_csv(EXP/'control_diagnostic_pairs.csv',index=False)
    old=pd.read_csv(EXP/'matched_pairs.csv');old=old[old.kind.eq('pure_wick')&old.horizon.eq(6)].set_index('event_id')
    base=pairs[pairs.variant.eq('baseline')].set_index('event_id').reindex(old.index)
    np.testing.assert_allclose(base[['signal_pct','random_pct']],old[['signal_pct','random_pct']],rtol=0,atol=1e-12)
    comparison={name:compare(g) for name,g in pairs.groupby('variant')}
    absolute=events.rename(columns={'return_pct_6':'signal_pct','positive_6':'signal_positive'}).assign(random_pct=0.,random_positive=0.)
    paths=[];checked=0
    for symbol,g in events.groupby('symbol'):
        b=pd.read_csv(EXP/symbol/'bars12.csv.gz',index_col=0,parse_dates=True,float_precision='round_trip')
        for e in g.itertuples():
            for h in [6,14]:
                f=b.iloc[e.bar_i+1:e.bar_i+1+h]
                if len(f)!=h or not f.complete.all():continue
                c=e.close;path=e.side*(np.r_[c,f.close.to_numpy(float)]/c-1)*100;steps=np.diff(path)
                paths.append(dict(event_id=e.event_id,symbol=symbol,week=e.week,horizon=h,
                    efficiency=float(path[-1]/np.abs(steps).sum()) if np.abs(steps).sum()>0 else 0.,
                    positive_close_fraction=float((path[1:]>0).mean()),directional_step_fraction=float((steps>0).mean()),
                    max_close_pullback_pct=float((np.maximum.accumulate(path)-path).max()),
                    giveback_from_best_close_pct=float(path.max()-path[-1])))
            # Source arithmetic check: independent direct closed-candle endpoints.
            expected=e.side*(b.close.iloc[e.bar_i+6]/b.close.iloc[e.bar_i]-1)*100
            assert abs(expected-e.return_pct_6)<1e-10;checked+=1
    path=pd.DataFrame(paths);path.to_csv(EXP/'path_diagnostics.csv',index=False)
    path_summary={str(h):dict(events=len(g),median_efficiency=float(g.efficiency.median()),median_positive_close_fraction=float(g.positive_close_fraction.median()),median_close_pullback_pct=float(g.max_close_pullback_pct.median()),median_giveback_pct=float(g.giveback_from_best_close_pct.median())) for h,g in path.groupby('horizon')}
    dump(EXP/'control_diagnostics.json',dict(comparisons=comparison,absolute_72h=compare(absolute),path_summary=path_summary,
        source_return_checks=checked,baseline_exactly_reproduced=True,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        caution='Each arm is one post-hoc control-pool restriction,not a validated trading rule. Sameweek event-conditioned matching can select countertrend weeks;absolute full-cohort metrics and unmatched events remain primary descriptive evidence. No multiple-testing claim.'))
    print(json.dumps(comparison,indent=2),flush=True)


def cohort_check():
    """Change matching distance on exactly the same1868 signal events."""
    pairs=pd.read_csv(EXP/'control_diagnostic_pairs.csv')
    strict=pairs[pairs.variant.eq('same_close_distance_bucket')]
    base=pairs[pairs.variant.eq('baseline')&pairs.event_id.isin(strict.event_id)]
    assert set(base.event_id)==set(strict.event_id)
    dump(EXP/'same_cohort_control_check.json',dict(baseline_same_events=compare(base),distance_matched_same_events=compare(strict),
        interpretation='Same signal cohort;only candidate control distance restriction changes. Post-hoc diagnostic,not an out-of-sample validation.'))


if __name__=='__main__':
    if '--cohort-check' in sys.argv:cohort_check()
    else:run()

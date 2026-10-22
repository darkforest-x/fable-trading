"""Compare frozen native structural-stop results with unchanged-entry baseline.

Owner chose latest5 completed15m extrema on October22. This analysis reads
saved entry/exit rows only, never creates entries or replays execution. Price
returns compare equal entry notional; own-risk R uses different position sizing
and is reported separately. Weekly inference is descriptive historical research.
"""
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT,dump
from yoyo.evaluation.pepe_range_exits import inference

EXP=ROOT/'experiments/active/exp-btc-monday-fib-body-20261022-v9'


def stats(values):
    return dict(zip(('min','q25','median','q75','max'),
                    map(float,np.quantile(values,[0,.25,.5,.75,1]))))


def bp_inference(paired,seed):
    """Reuse week-cluster resampling with explicit basis-point output units."""
    result=inference(paired.rename(columns={'net_bp':'net_r','random_bp':'net_r_random'}),seed)
    result['excess_sum_bp']=result.pop('excess_r')
    result['weekly_bootstrap_total_bp_95ci']=result.pop('weekly_bootstrap_total_r_95ci')
    return result


def run():
    cfg=json.loads((EXP/'config.json').read_text());old=ROOT/cfg['prior_inputs']
    a=pd.read_csv(EXP/'all_parents.csv',float_precision='round_trip')
    b=pd.read_csv(old/'all_parents.csv',float_precision='round_trip')
    s=a[a.population.eq('signal')].set_index('parent_id')
    prior=b[b.population.eq('signal')].set_index('parent_id').loc[s.index]
    entries=pd.read_csv(EXP/'entries.csv',float_precision='round_trip').set_index('trade_id').loc[s.index]
    oldentries=pd.read_csv(old/'entries.csv',float_precision='round_trip').set_index('trade_id').loc[s.index]
    assert set(s.index)==set(prior.index) and s.closed.all() and prior.closed.all()
    for c in ('entry_i','entry','side','tp1','tp2','tp3','tp4'):
        np.testing.assert_allclose(entries[c],oldentries[c],rtol=0,atol=1e-8)
    paired=s[['week','side','segment','entry','risk','outcome','net_r','net_bp']].join(
        prior[['risk','outcome','net_r','net_bp']],rsuffix='_old')
    paired['stop_pct']=100*paired.risk/paired.entry
    paired['stop_change']=np.where(paired.risk>paired.risk_old+1e-8,'wider',
                                   np.where(paired.risk<paired.risk_old-1e-8,'narrower','same'))
    paired['change_bp']=paired.net_bp-paired.net_bp_old
    paired['new_in_prior_risk_r']=paired.net_bp/10000*paired.entry/paired.risk_old
    paired.to_csv(EXP/'paired_stop_comparison.csv')
    random=a[a.population.eq('random')]
    matching=s[['pair_id','week','net_bp']].join(random.groupby('pair_id').net_bp.mean().rename('random_bp'),on='pair_id')
    def groups(cols):
        return [dict(group=str(key),n=len(g),wins=int(g.outcome.eq('win').sum()),
            old_wins=int(g.outcome_old.eq('win').sum()),mean_bp=float(g.net_bp.mean()),
            old_mean_bp=float(g.net_bp_old.mean()),sum_change_bp=float(g.change_bp.sum()))
            for key,g in paired.groupby(cols)]
    result=dict(experiment=EXP.name,unchanged_entry_and_target_checks=len(s),
        stop_distance_percent=stats(paired.stop_pct),
        stop_change_counts=paired.stop_change.value_counts().to_dict(),
        changed_outcomes=paired[paired.outcome.ne(paired.outcome_old)].reset_index().to_dict('records'),
        by_stop_change=groups('stop_change'),by_side=groups('side'),by_segment=groups('segment'),
        mean_change_bp=float(paired.change_bp.mean()),
        equal_notional_pf=float(s.loc[s.net_bp>0,'net_bp'].sum()/-s.loc[s.net_bp<0,'net_bp'].sum()),
        prior_equal_notional_pf=float(prior.loc[prior.net_bp>0,'net_bp'].sum()/-prior.loc[prior.net_bp<0,'net_bp'].sum()),
        new_sum_in_prior_risk_r=float(paired.new_in_prior_risk_r.sum()),
        prior_sum_r=float(prior.net_r.sum()),
        equal_notional_strategy_vs_random=bp_inference(matching,cfg['seed']),
        equal_notional_new_vs_old=bp_inference(paired.rename(columns={'net_bp_old':'random_bp'}),cfg['seed']),
        units='Percent and bp are unlevered entry-notional per trade, not compounded account return. Own-risk R differs across stop rules.',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(EXP/'stop_comparison.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':run()

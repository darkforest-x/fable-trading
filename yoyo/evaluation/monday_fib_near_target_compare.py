"""Attribute near-Monday TP1 effects using already completed native ledgers.

Owner October22 changes only first25percent target and its existing BE trigger.
Inputs are frozen entries and realized fills, not new signals. Equal-notional
return and weekly clustered comparisons include skipped old opportunities as
zero exposure; same-entry results are also separated explicitly.
"""
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT,dump
from yoyo.evaluation.monday_fib_structural_compare import stats,bp_inference

EXP=ROOT/'experiments/active/exp-btc-monday-fib-body-20261022-v10'


def run():
    cfg=json.loads((EXP/'config.json').read_text());old=ROOT/cfg['prior_inputs']
    a=pd.read_csv(EXP/'all_parents.csv',float_precision='round_trip')
    b=pd.read_csv(old/'all_parents.csv',float_precision='round_trip')
    s=a[a.population.eq('signal')].set_index('parent_id')
    prior=b[b.population.eq('signal')].set_index('parent_id')
    entries=pd.read_csv(EXP/'entries.csv',float_precision='round_trip').set_index('trade_id')
    oldentries=pd.read_csv(old/'entries.csv',float_precision='round_trip').set_index('trade_id')
    assert set(s.index)<=set(prior.index) and s.closed.all() and prior.closed.all()
    for c in ('entry_i','entry','side','stop','risk','tp2','tp3','tp4'):
        np.testing.assert_allclose(entries[c],oldentries.loc[entries.index,c],rtol=0,atol=1e-8)
    pair=s[['week','side','segment','entry','risk','outcome','net_r','net_bp','tp1','tp4','be_exit']].join(
        prior[['outcome','net_r','net_bp','tp1','tp4','be_exit']],rsuffix='_old')
    pair['change_bp']=pair.net_bp-pair.net_bp_old
    pair['change_r']=pair.net_r-pair.net_r_old
    pair.to_csv(EXP/'paired_target_comparison.csv')
    random=a[a.population.eq('random')]
    matching=s[['pair_id','week','net_bp']].join(random.groupby('pair_id').net_bp.mean().rename('random_bp'),on='pair_id')
    opportunity=prior[['week','net_bp']].rename(columns={'net_bp':'random_bp'}).join(s.net_bp).fillna({'net_bp':0.})
    removed=prior.loc[prior.index.difference(s.index)]
    def pf(frame):
        return float(frame.loc[frame.net_bp>0,'net_bp'].sum()/-frame.loc[frame.net_bp<0,'net_bp'].sum())
    result=dict(experiment=EXP.name,unchanged_entry_stop_and_later_targets=len(s),
        skipped_prior_ids=list(removed.index),skipped_prior_outcomes=removed.outcome.value_counts().to_dict(),
        skipped_prior_sum_bp=float(removed.net_bp.sum()),
        tp1_distance_pct=stats(100*entries.side*(entries.tp1-entries.entry)/entries.entry),
        prior_tp1_distance_pct=stats(100*oldentries.side*(oldentries.tp1-oldentries.entry)/oldentries.entry),
        transitions=[dict(old=x,new=y,n=int(n)) for (x,y),n in pair.groupby(['outcome_old','outcome']).size().items()],
        attribution_by_prior_outcome=[dict(prior_outcome=x,n=len(g),sum_change_bp=float(g.change_bp.sum()),
            sum_change_r=float(g.change_r.sum())) for x,g in pair.groupby('outcome_old')],
        equal_notional_pf=pf(s),prior_equal_notional_pf=pf(prior),
        mean_per_original_opportunity_bp=float(opportunity.net_bp.mean()),
        prior_mean_per_original_opportunity_bp=float(opportunity.random_bp.mean()),
        equal_notional_strategy_vs_random=bp_inference(matching,cfg['seed']),
        equal_notional_new_vs_old_per_original_opportunity=bp_inference(opportunity,cfg['seed']),
        outcome_changes=pair[pair.outcome.ne(pair.outcome_old)].reset_index().to_dict('records'),
        units='bp is unlevered original-entry-notional per trade, not account return. Same retained entries and initial risk; removed opportunities separately zero exposure.',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(EXP/'target_comparison.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':run()

"""Describe the two-wick engulfing rule against random and the v8 body rule.

Owner Notion task 区间1.618测试 #1. Reads completed native ledgers only; no
signal regeneration or replay. Arms differ, so the v8 comparison pairs the
same weekly-side first-touch opportunity, counting an untraded side as zero
exposure. Engulf close side and waits are descriptive, not entry filters.
"""
import json
import subprocess
import pandas as pd
from yoyo.evaluation.range_framework import ROOT,dump
from yoyo.evaluation.monday_fib_structural_compare import stats,bp_inference
from yoyo.evaluation import monday_fib_confirmation as m

EXP=ROOT/'experiments/active/exp-btc-monday-fib-wick-engulf-20261023-v1'


def pf(frame):
    loss=-frame.loc[frame.net_bp<0,'net_bp'].sum()
    return float(frame.loc[frame.net_bp>0,'net_bp'].sum()/loss) if loss else None


def run():
    cfg=json.loads((EXP/'config.json').read_text());old=ROOT/cfg['prior_inputs']
    a=pd.read_csv(EXP/'all_parents.csv',float_precision='round_trip')
    b=pd.read_csv(old/'all_parents.csv',float_precision='round_trip')
    entries=pd.read_csv(EXP/'entries.csv',float_precision='round_trip').set_index('trade_id')
    decisions=pd.read_csv(EXP/'decisions.csv',float_precision='round_trip')
    s=a[a.population.eq('signal')].set_index('parent_id');r=a[a.population.eq('random')]
    prior=b[b.population.eq('signal')]
    assert s.closed.all() and prior.closed.all() and set(s.index)==set(entries.index)
    s=s.join(entries[['level','wait_minutes','touch_i','wick1_i','wick2_i']])
    s['engulf_close_inside']=s.side*(s.entry-s.level)>0
    matching=s[['pair_id','week','net_bp']].join(r.groupby('pair_id').net_bp.mean().rename('random_bp'),on='pair_id')
    groups={}
    for name,mask in (('long',s.side.eq(1)),('short',s.side.eq(-1)),('earlier',s.segment.eq(0)),
                      ('later',s.segment.eq(1)),('engulf_close_inside',s.engulf_close_inside),
                      ('engulf_close_outside',~s.engulf_close_inside)):
        g=s[mask];ids=set(g.pair_id)
        groups[name]=dict(signal=m.metric(g),random=m.metric(r[r.pair_id.isin(ids)]),pf=pf(g) if len(g) else None)
    # Per weekly-side opportunity: both rules start from the same first touch.
    touched=decisions[['pair_id','week']].drop_duplicates('pair_id').set_index('pair_id')
    opportunity=touched.join(s.set_index('pair_id').net_bp).join(
        prior.set_index('pair_id').net_bp.rename('random_bp')).fillna({'net_bp':0.,'random_bp':0.})
    both=s.set_index('pair_id').join(prior.set_index('pair_id')[['entry_i','outcome','net_bp']],rsuffix='_v8',how='inner')
    result=dict(experiment=EXP.name,touched_weekly_sides=len(touched),
        statuses=decisions.status.value_counts().to_dict(),
        signal=m.metric(s),random=m.metric(r),equal_notional_pf=pf(s),random_pf=pf(r),
        strategy_vs_random_bp=bp_inference(matching,cfg['seed']),groups=groups,
        wait_touch_to_entry_minutes=stats(s.wait_minutes),
        wick2_to_entry_minutes=stats(s.entry_i-s.wick2_i),
        v8_body=dict(signal=m.metric(prior),pf=pf(prior)),
        v8_overlap=dict(both=len(both),only_wick=int(len(s)-len(both)),only_v8=int(len(prior)-len(both)),
            wick_entry_minus_v8_entry_minutes=stats(both.entry_i-both.entry_i_v8) if len(both) else None,
            transitions=[dict(v8=x,wick=y,n=int(n)) for (x,y),n in both.groupby(['outcome_v8','outcome']).size().items()]),
        per_opportunity_mean_bp=float(opportunity.net_bp.mean()),
        v8_per_opportunity_mean_bp=float(opportunity.random_bp.mean()),
        wick_vs_v8_per_opportunity_bp=bp_inference(opportunity,cfg['seed']),
        units='bp is unlevered original-entry-notional per trade, fee0, not account return. Per-opportunity rows count an untraded side as zero.',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(EXP/'rule_comparison.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=1))


if __name__=='__main__':run()

"""Restate existing standalone pivot native ledgers under owner zero fees.

No execution is rerun or new filter selected. The original pivot predicate
uses side*(confirmed pivot price-entry)>0, then exits at confirmation close;
it never reads the fee rate. Standalone policies exclude BE, reverse and
partial exits, and original native commissions remain source evidence.
Admission uses original touch, preceding20 bars and completed H1/15m watch;
all grouping below uses frozen signal identities, entry time and side only.
"""
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_fiveyear_fixed_target import metrics, read
from yoyo.evaluation.range_fixed_target_compare import cohorts
from yoyo.evaluation.pepe_range_exits import inference, pairs
from yoyo.evaluation.range_management_study import holm

DEST = ROOT / 'experiments/active/exp-btc-touch-fixed-targets-fiveyear-20261022-v1'
SOURCE = ROOT / 'experiments/active/exp-range-v231-fiveyear-filters-20261021-v1/BTC'
BASE = ROOT / 'experiments/active/exp-range-v231-fiveyear-20261021-v1/BTC'


def zero_fee_pivot(rows):
    """Only standalone original pivot paths are eligible for this restatement."""
    if not rows.reason.isin([0,1,7]).all() or rows.be_effective_i.ge(0).any():
        raise ValueError('Expected standalone pivot/initial-stop paths without BE')
    out = rows.copy()
    for column in ['fee_r','net_r','net_notional_bp']:
        out['source_' + column] = out[column]
    out['fee_r'] = 0.
    out['net_r'] = out.gross_r
    out['net_notional_bp'] = out.gross_r * out.risk / out.entry * 10000
    out['boundary_weight'] = out.reason.eq(0).astype(float)
    out['boundary_net_r'] = out.net_r * out.boundary_weight
    out['ambiguous_legs'] = out.ambiguous
    return out


def main():
    code = ['yoyo/evaluation/range_pivot_zero_fee.py','yoyo/evaluation/range_fixed_target_compare.py',
            'yoyo/evaluation/range_fiveyear_fixed_target.py','yoyo/layers/l3_backtest/nautilus_independent.py']
    if subprocess.run(['git','diff','--quiet','HEAD','--',*code],cwd=ROOT).returncode:
        raise RuntimeError('Commit accounting builder first')
    out = DEST / 'pivot_reference_summary.json'
    if out.exists():
        print('Reuse '+str(out)); return
    cfg = json.loads((DEST/'config.json').read_text())
    native = json.loads((SOURCE/'native.json').read_text())
    for policy in native['policies']:
        assert set(policy) <= {'name','confirmed_pivot_exit','opposite_hour_exit','strict_pivot_ties'}
        assert policy['confirmed_pivot_exit'] and not policy['opposite_hour_exit']
    eligible = read(SOURCE/'admission.csv')
    ids = eligible.loc[eligible.window_both, 'pair_id']
    sweep = read(SOURCE/'trades.csv'); combined = read(SOURCE/'combined_trades.csv')
    selected = sweep[sweep.pair_id.isin(ids)].sort_values('trade_id').reset_index(drop=True)
    pd.testing.assert_frame_equal(selected[combined.columns],combined.sort_values('trade_id').reset_index(drop=True),check_dtype=False)
    sets = [('original_pivot',read(BASE/'trades.csv'),read(BASE/'sensitivity.csv'),485),
            ('sweep20_pivot',sweep,read(SOURCE/'sensitivity.csv'),379),
            ('sweep20_mtf_pivot',combined,read(SOURCE/'sensitivity.csv').query('pair_id in @ids'),212)]
    results = []
    for variant, original, conservative, expected in sets:
        primary, lower = zero_fee_pivot(original), zero_fee_pivot(conservative)
        assert len(primary) == expected*2 and not primary.duplicated(['pair_id','population']).any()
        assert not primary.boundary_weight.gt(0).any()
        primary.to_csv(DEST/f'{variant}_zero_fee.csv',index=False)
        signal = primary[primary.population=='signal']
        for period, mask in cohorts(signal,cfg):
            pair_ids = signal.loc[mask,'pair_id']
            s,r = [primary[(primary.population==pop)&primary.pair_id.isin(pair_ids)] for pop in ['signal','random']]
            ss,rr = [lower[(lower.population==pop)&lower.pair_id.isin(pair_ids)] for pop in ['signal','random']]
            assert len(s)==len(r)==len(ss)==len(rr)==len(pair_ids)
            data=dict(variant=variant,period=period,signal=metrics(s),random=metrics(r),
                      strict_pivot_ties_signal=metrics(ss),strict_pivot_ties_random=metrics(rr),
                      paired_excess=inference(pairs(s,r),cfg['seed']))
            data['signal']['without_best5_r']=float(s.net_r.sum()-s.net_r.nlargest(5).sum())
            data['signal']['average_win_r']=float(s.loc[s.net_r>1e-9,'net_r'].mean())
            data['signal']['average_loss_r']=float(s.loc[s.net_r < -1e-9,'net_r'].mean())
            results.append(data)
    full=[r for r in results if r['period']=='five_years']
    for row,adjusted in zip(full,holm([r['paired_excess']['p_one_sided'] for r in full])):
        row['holm_p_three_existing_pivot_variants']=float(adjusted)
    summary=json.loads((SOURCE.parent/'summary.json').read_text())
    for row,variant in zip(full,['baseline','sweep20','sweep20_mtf']):
        old=next(r for r in summary['rows'] if r['symbol']=='BTC' and r['period']=='all' and r['variant']==variant)
        np.testing.assert_allclose([row['signal']['net_r'],row['signal']['win_rate']],
                                   [old['gross_r'],old['gross_win_rate']],rtol=0,atol=1e-8)
    dump(out,dict(rows=results,round_trip_cost=0,new_native_positions=0,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        inputs=[file_stamp(p) for p in [BASE/'trades.csv',BASE/'sensitivity.csv',SOURCE/'trades.csv',SOURCE/'sensitivity.csv',SOURCE/'admission.csv',SOURCE/'combined_trades.csv',SOURCE/'native.json']],
        checks=dict(combined_identity_and_population_parity=True,three_original_gross_summaries_reconciled=True,fee_independent_standalone_pivot=True),
        limitations='Existing cached native paths with zero-fee accounting, not new zero-fee executions. Pivot exits at confirmation close, not the earlier extreme; some pivot exits lose. Filters have distinct admitted cohorts. Random background does not itself satisfy sweep/MTF filters. Three-way p adjustment does not cover every earlier research choice. Independent R, no new forward validation or promotion.'))
    print(json.dumps(full,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    main()

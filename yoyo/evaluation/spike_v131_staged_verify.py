"""Independent scalar audit of staged-entry ledgers and selected summaries.

Uses standard-library CSV arithmetic, not the search engine's return functions.
Also measures actual filled opportunities and original-stop exposure; late
reclaim fills may chase above entry even though total quantity is capped.
"""
from __future__ import annotations

import csv
from datetime import datetime
import gzip
import json
import math
from pathlib import Path

ROOT=Path('experiments/active/exp-spike-v131-staged-entry-search-20261019-v1/results_v1')


def policy_value(row,p,touch=False,cap=False):
    w=float(p['fraction']); base=float(row['net_r'])
    if p['method']=='constant':return w*base,w>0,w
    method='touch' if touch and p['method']=='limit' else p['method']
    prefix=f"{method}_{round(float(p['depth'])*100):02d}"
    offset=int(row[prefix+'_offset'])
    filled=offset>=0 and (not p['expiry'] or offset<int(p['expiry']))
    if not filled:return w*base,w>0,w
    side=int(row['side']);entry=float(row['entry_price']);risk=float(row['initial_risk'])
    price=float(row[prefix+'_price']);exit_price=float(row['actual_exit_price'])
    exposure=side*(price-float(row['initial_stop']))/risk
    quantity=(1-w)*(min(1.,1./exposure) if cap else 1.)
    r=w*base+quantity*(side*(exit_price-price)-.002*price)/risk
    return r,True,w+quantity*exposure


def selected(row,name,period,cut):
    if name!='all':
        bits=name.split('_side')
        if int(row['timeframe_min'])!=int(bits[0][:-1]):return False
        if len(bits)==2 and int(row['side'])!=int(bits[1]):return False
    entry=datetime.fromisoformat(row['entry_time']);exit_time=datetime.fromisoformat(row['exit_time'])
    return period=='full' or (period=='early' and entry<cut and exit_time<cut) or (period=='later' and entry>=cut)


def run():
    result=json.loads((ROOT/'result.json').read_text());cut=datetime.fromisoformat(result['config']['split'].replace('Z','+00:00'))
    with gzip.open(ROOT/'leg_paths.csv.gz','rt') as f:rows=list(csv.DictReader(f))
    target=[r for r in rows if r['cohort']=='signal'];control=[r for r in rows if r['cohort']=='control']
    checks=0
    def eq(a,b):
        nonlocal checks
        assert math.isclose(float(a),float(b),rel_tol=0,abs_tol=1e-8),(a,b)
        checks+=1
    for r in rows:
        side=int(r['side']);entry=float(r['entry_price']);risk=float(r['initial_risk']);exit_price=float(r['actual_exit_price'])
        eq(r['net_r'],(side*(exit_price-entry)-.002*entry)/risk)
        held=int((datetime.fromisoformat(r['exit_time'])-datetime.fromisoformat(r['entry_time'])).total_seconds()/60/int(r['timeframe_min']))
        for method in ['limit','touch','reclaim']:
            for d in result['config']['depths_r']:
                key=f'{method}_{round(d*100):02d}';offset=int(r[key+'_offset'])
                assert -1<=offset<=held
                if offset<0:
                    assert r[key+'_price']=='';eq(r[key+'_r'],0)
                else:
                    price=float(r[key+'_price'])
                    eq(r[key+'_r'],(side*(exit_price-price)-.002*price)/risk)
                    assert offset>0 if method=='reclaim' else side*(entry-price)>0
    diagnostics=[]
    for group in result['selections']:
        candidates=[group[k] for k in ['full_best','early_best','best_half','best_positive_initial','early_selected_later','full_best_later']]
        candidates+=group['original']+group['half_at_minus05']
        for p in candidates:
            t=[r for r in target if selected(r,p['group'],p['period'],cut)]
            bykey={r['trade_key']:r for r in t}
            c=[r for r in control if r['trade_key'] in bykey and selected(r,p['group'],p['period'],cut)]
            values=[policy_value(r,p) for r in t];rr=[x[0] for x in values]
            eq(len(rr),p['n']);eq(math.fsum(rr),p['sum_net_r'])
            eq(math.fsum(max(0,x) for x in rr),p['positive_r']);eq(-math.fsum(min(0,x) for x in rr),p['negative_r'])
            eq(sum(x>0 for x in rr)/len(rr),p['win_rate']);eq(len(c),p['matched'])
            if c:
                eq(math.fsum(policy_value(r,p)[0] for r in c)/len(c),p['matched_control_mean_r'])
                eq(math.fsum(policy_value(bykey[r['trade_key']],p)[0]-policy_value(r,p)[0] for r in c)/len(c),p['matched_excess_mean_r'])
            if p in [group['full_best'],group['early_selected_later'],group['best_half']]:
                capped=[policy_value(r,p,cap=True)[0] for r in t]
                diagnostics.append(dict(group=p['group'],period=p['period'],candidate_id=p['candidate_id'],
                    method=p['method'],fraction=p['fraction'],depth=p['depth'],expiry=p['expiry'],
                    filled_opportunities=sum(x[1] for x in values),wins=sum(x>0 for x in rr),
                    win_rate_filled=sum(x>0 for x in rr)/sum(x[1] for x in values) if any(x[1] for x in values) else None,
                    original_stop_exposure_mean=math.fsum(x[2] for x in values)/len(values),
                    original_stop_exposure_max=max(x[2] for x in values),exposure_over_1r=sum(x[2]>1+1e-9 for x in values),
                    risk_capped_same_policy_sum_r=math.fsum(capped),
                    touch_same_policy_sum_r=math.fsum(policy_value(r,p,touch=True)[0] for r in t)))
    with gzip.open(ROOT/'search.csv.gz','rt') as f:grid=list(csv.DictReader(f))
    eq(len(grid),result['search_rows'])
    for group in result['selections']:
        for period,label in [('full','full_best'),('early','early_best')]:
            scores=[float(r['sum_net_r']) for r in grid if r['group']==group['group'] and r['period']==period]
            eq(len(scores),result['candidate_count']);eq(max(scores),group[label]['sum_net_r'])
    out=dict(checks=checks,leg_rows=len(rows),selected_summaries_verified=True,grid_maxima_verified=True,
        diagnostics=diagnostics,limitations='Scalar ledger/aggregation audit; OHLC path logic is covered by baseline parity and dedicated lifecycle tests. Risk-cap diagnostic holds the selected policy fixed and reduces remainder quantity to keep loss at the original absolute stop within original planned R; it is not a new optimized strategy.')
    (ROOT/'verification.json').write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False))
    print(json.dumps(dict(checks=checks,rows=len(rows),diagnostics=diagnostics[:3]),ensure_ascii=False))


if __name__=='__main__':run()

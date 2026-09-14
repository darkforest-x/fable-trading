"""Bounded ETH15m ICT exit study with development-first selection.

Entry and initial risk reuse frozen V8. Calendar features use the actual next
open; ATR/close matching uses current signal and preceding 120 closed bars.
Development reads only its own prefix, and every outcome is censored at its
window end. No parameter is selected from 2025 or later results or holdout.
"""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.spike_fanshen_prefix import read_prefix
from yoyo.evaluation import spike_fanshen_study as base
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v8_lowtf_study import v8_mask
from yoyo.evaluation.spike_recovery_exit import prepare
from yoyo.evaluation.spike_partial_exit import replay_partial
from yoyo.evaluation.spike_v8_ict import session_mask, matched_controls
from yoyo.evaluation.spike_v8_ict_multitf import assert_same_trades
from yoyo.evaluation.spike_net_recovery_cash import simulate_recovery

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-spike-v8-ict-exits-20260915-v1'
BUILDERS=[
 'yoyo/evaluation/spike_v8_ict_exit_study.py','yoyo/evaluation/spike_partial_exit.py',
 'yoyo/evaluation/spike_recovery_exit.py','yoyo/evaluation/spike_recovery_cash.py',
 'yoyo/evaluation/spike_net_recovery_cash.py','yoyo/evaluation/spike_v8_ict.py',
 'yoyo/evaluation/spike_v8_ict_multitf.py','yoyo/evaluation/spike_fanshen_study.py',
 'yoyo/evaluation/spike_burst_replay.py','yoyo/evaluation/spike_v8_lowtf_study.py',
 'yoyo/evaluation/spike_v7_fast.py','yoyo/evaluation/spike_v6_wvf_study.py',
 'yoyo/data/spike_fanshen_prefix.py',str((EXP/'config.json').relative_to(ROOT)),
 str((EXP/'PROJECT_PLAN.md').relative_to(ROOT)),
]


def outcome_stats(rows):
    """A stopped residual after a partial fill is not a full initial stop."""
    result=base.stats(rows)
    natural=[r for r in rows if not r['censored']]
    flags=[bool(r.get('full_initial_stop',str(r['exit_reason']).startswith('initial_stop'))) for r in natural]
    result.update(initial_stops=sum(flags),max_initial_stop_streak=base.longest(flags),
        partial_trades=sum(bool(r.get('partial_executed',False)) for r in natural),
        ambiguous_trades=sum(bool(r.get('ambiguous_stop_tp',False)) for r in natural))
    return result


def choose_development(rows, minimum=30):
    """Choose by predeclared total net R, rejecting nondevelopment scoring."""
    if any(r['window']!='development' for r in rows):
        raise ValueError('selection accepts development rows only')
    eligible=[r for r in rows if r['natural']>=minimum and np.isfinite(r['sum_net_r'])]
    if not eligible:raise ValueError('no development arm has enough natural trades')
    return sorted(eligible,key=lambda r:(-r['sum_net_r'],r['policy']))[0]


def cutoff_index(index, end):
    """Exclusive bar-open bound; no bar at or after end enters an outcome."""
    return int(index.searchsorted(pd.Timestamp(end),side='left'))


def write_trades(path, rows):
    data=pd.DataFrame(rows)
    if 'fills' in data:
        data['fills']=data.fills.map(lambda x:json.dumps(x,default=str))
    data.to_csv(path,index=False)


def run(phase):
    cfg=json.loads((EXP/'config.json').read_text())
    assert cfg['minutes']==15 and cfg['session']=='union'
    assert pd.Timestamp(cfg['end'])<=pd.Timestamp('2026-05-01T00:00:00Z')
    for rel in BUILDERS:
        subprocess.run(['git','cat-file','-e','HEAD:'+rel],cwd=ROOT,check=True)
        subprocess.run(['git','diff','--exit-code','HEAD','--',rel],cwd=ROOT,check=True)
    identities={p:base.sha(ROOT/p) for p in BUILDERS}
    selection_path=EXP/'results/selection.json'
    if phase=='evaluate':
        frozen=json.loads(selection_path.read_text())
        assert subprocess.check_output(['git','show','HEAD:'+str(selection_path.relative_to(ROOT))],cwd=ROOT)==selection_path.read_bytes()
        assert frozen['builders']==identities,'builders changed after development selection'
    out=EXP/'results'/phase
    out.mkdir(parents=True,exist_ok=False)
    end=cfg['development_end'] if phase=='develop' else cfg['end']
    receipt=dict(phase=phase,builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        builders=identities,read_end=end,holdout_consumed=False,holdout_consumption_number=0)
    base.save(out/'read_receipt.json',receipt)
    bars,input_receipt=read_prefix(ROOT/cfg['source'],15,end)
    frame=features(bars);frame.attrs['minutes']=15
    raw,_,mask=v8_mask(frame,15);prepared=prepare(frame,raw)
    base.save(out/'input_receipt.json',input_receipt)
    start=max(pd.Timestamp(cfg['start']),frame.index[cfg['minimum_warmup_bars']]+pd.Timedelta(minutes=15))
    windows={'development':(start,pd.Timestamp(end))} if phase=='develop' else {
        'validation':(pd.Timestamp(cfg['validation_start']),pd.Timestamp(cfg['common_start'])),
        'common':(pd.Timestamp(cfg['common_start']),pd.Timestamp(cfg['end'])),
        'available':(start,pd.Timestamp(cfg['end']))}
    summary,cash_rows,parity=[],[],[]
    for window,(left,right) in windows.items():
        end_i=cutoff_index(frame.index,right)
        entries=frame.index[1:end_i]
        indices=np.flatnonzero(mask.iloc[:end_i-1].to_numpy(bool)&(entries>=left)&(entries<right)&session_mask(entries,'union'))
        opportunities={}
        for name,policy in cfg['arms'].items():
            rows=[replay_partial(prepared,int(i),end_i=end_i,tick=cfg['tick'],**policy) for i in indices]
            rows=[r for r in rows if r is not None]
            if any(not np.isfinite(r['net_r']) for r in rows):raise ValueError('unresolved price gap')
            for r in rows:
                assert r['exit_i']<end_i
                fills=r['fills']
                assert abs(sum(x['fraction'] for x in fills)-1.)<1e-10
                assert abs(sum(x['gross_r_contribution'] for x in fills)-r['gross_r'])<1e-10
                assert abs(sum(x['allocated_cost_r'] for x in fills)-r['cost_r'])<1e-10
                assert abs(sum(x['gross_r_contribution']-x['allocated_cost_r'] for x in fills)-r['net_r'])<1e-10
            opportunities[name]=rows
            write_trades(out/f'{window}_{name}_opportunities.csv.gz',rows)
        original=base.serial(opportunities['original'])
        parity.append(dict(window=window,kind='original_engine',**base.assert_parity(frame.iloc[:end_i],raw.iloc[:end_i],indices,original)))
        if phase=='evaluate' and window in {'common','available'}:
            old=ROOT/cfg['previous_experiment']/'results'
            assert base.sha(old/'manifest.json')==cfg['previous_manifest_sha256']
            manifest=json.loads((old/'manifest.json').read_text())
            name=f'15m_{window}_original_union_trades.csv.gz'
            assert base.sha(old/name)==manifest['files'][name]
            parity.append(dict(window=window,kind='previous_ict',**assert_same_trades(original,pd.read_csv(old/name))))
        replay_cache={}
        for name,policy in cfg['arms'].items():
            opp=opportunities[name];accepted=base.serial(opp);tag=f'{window}_{name}'
            write_trades(out/(tag+'_trades.csv.gz'),accepted)
            fills=[dict(signal_i=t['signal_i'],side=t['side'],**f) for t in accepted for f in t['fills']]
            pd.DataFrame(fills).to_csv(out/(tag+'_fills.csv.gz'),index=False)
            pairs,paired=base.pair_delta(original,opp);pairs.to_csv(out/(tag+'_paired.csv.gz'),index=False)
            control,metrics=matched_controls(frame.iloc[:end_i],accepted,policy,policy_key=name,
                session='union',start=left,end=right,seed=cfg['seed'],controls_per_trade=5,permutations=9999,
                replay_fn=lambda i,p,s:replay_partial(prepared,i,end_i=end_i,side_override=s,tick=cfg['tick'],**p),cache=replay_cache)
            control.to_csv(out/(tag+'_controls.csv.gz'),index=False)
            summary.append(dict(window=window,start=str(left),end=str(right),policy=name,candidates=len(opp),
                **outcome_stats(accepted),**paired,**metrics))
            for schedule in ['fixed','double']:
                cash=simulate_recovery(opp,schedule=schedule,**cfg['cash'])
                base.save(out/(tag+f'_{schedule}_cash.json'),cash)
                cash_rows.append(dict(window=window,policy=name,schedule=schedule,**cash['summary']))
            print(phase,tag,'n',summary[-1]['natural'],'netR',round(summary[-1]['sum_net_r'],4),
                  'paired',round(paired['paired_delta_net_r'],4),flush=True)
        pd.DataFrame(summary).to_csv(out/'progress_summary.csv',index=False)
    data=pd.DataFrame(summary);data['p_holm']=np.nan
    for window in data.window.unique():
        group=data.loc[(data.window==window)&data.p.notna()].sort_values('p')
        if len(group):data.loc[group.index,'p_holm']=np.minimum(1,np.maximum.accumulate(group.p.to_numpy()*np.arange(len(group),0,-1)))
    data.to_csv(out/'summary.csv',index=False);pd.DataFrame(cash_rows).to_csv(out/'cash_summary.csv',index=False)
    base.save(out/'parity.json',parity)
    base.save(out/'manifest.json',dict(**receipt,files={p.name:base.sha(p) for p in sorted(out.iterdir()) if p.is_file()}))
    if phase=='develop':
        chosen=choose_development(summary,cfg['selection']['minimum_natural'])
        base.save(selection_path,dict(selected_policy=chosen['policy'],positive_development=chosen['sum_net_r']>0,
            development_result=chosen,builders=identities,builder_commit=receipt['builder_commit'],
            development_manifest_sha256=base.sha(out/'manifest.json'),holdout_consumed=False))
    print('COMPLETE',phase,len(summary),'summary',len(cash_rows),'cash',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['develop','evaluate'])
    run(parser.parse_args().phase)

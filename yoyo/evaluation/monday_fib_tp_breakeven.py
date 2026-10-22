"""Reuse frozen Monday confirmations; any TP immediately protects sibling legs.

Owner correction2026-10-22 supersedes the incomplete timed-only experiment.
Only exit management changes. No signal regeneration, downloading, or model
training. Independent delay workers write disjoint native output directories.
"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
from pathlib import Path
import pandas as pd
from yoyo.evaluation import monday_fib_confirmation as m
from yoyo.evaluation.range_framework import ROOT,dump,file_stamp
from yoyo.evaluation.pepe_range_exits import inference

OLD=m.EXP
EXP=ROOT/'experiments/active/exp-btc-monday-fib-confirm-20261022-v2'
CODE=m.CODE+['yoyo/evaluation/monday_fib_tp_breakeven.py',
             'tests/evaluation/test_nautilus_target_breakeven.py']


def context():
    global OLD
    m.EXP=EXP;m.CODE=CODE
    cfg=json.loads((EXP/'config.json').read_text())
    OLD=ROOT/cfg.get('prior_inputs',str(OLD.relative_to(ROOT)))
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:
        raise RuntimeError('Commit builders before native replay')
    entries=pd.read_csv(OLD/'entries.csv',float_precision='round_trip')
    random=pd.read_csv(OLD/'controls.csv',float_precision='round_trip')
    return cfg,entries,random


def initialize():
    cfg,entry,random=context()
    oldcfg=json.loads((OLD/'config.json').read_text())
    if cfg['wait_minutes']!=oldcfg['wait_minutes']:
        frame=m.base.aligned(pd.read_csv(m.SOURCE/'candles.csv.gz',float_precision='round_trip'))
        entry,decisions,checks=m.prepare(frame,cfg)
        random=m.controls(frame,entry,cfg)
        entry.to_csv(EXP/'entries.csv',index=False)
        random.to_csv(EXP/'controls.csv',index=False)
        decisions.to_csv(EXP/'decisions.csv',index=False)
    else:
        checks={}
        for name in ('entries.csv','controls.csv','decisions.csv'):
            shutil.copyfile(OLD/name,EXP/name)
    completed=[]
    for identity in sorted(OLD.rglob('identity.json')):
        folder=identity.parent
        rows=pd.read_csv(folder/'parents.csv',float_precision='round_trip')
        completed.append(dict(directory=str(folder.relative_to(ROOT)),
            completed_identity_path=str(identity.relative_to(ROOT)),identity_bytes=identity.stat().st_size,
            signal=m.metric(rows[rows.population.eq('signal')]),
            random=m.metric(rows[rows.population.eq('random')])))
    dump(OLD/'superseded_receipt.json',dict(reason=cfg.get('change','Owner adds immediate cost breakeven on any TP; these completed runs lack that trigger. Remaining delays interrupted, not completed.'),
        successor=EXP.name,completed=completed,training_eligible=False,production_eligible=False))
    dump(EXP/'admission_receipt.json',dict(source_experiment=OLD.name,
        previous_inputs=[file_stamp(OLD/name) for name in ('entries.csv','controls.csv','decisions.csv')],
        frozen_signals=len(entry),frozen_controls=len(random),
        admission_checks=checks,change=cfg.get('change','Every native target fill synchronously protects remaining same-parent tranches; optional timer remains independent.')))


def replay(delays):
    cfg,entry,random=context()
    entry=pd.read_csv(EXP/'entries.csv',float_precision='round_trip')
    random=pd.read_csv(EXP/'controls.csv',float_precision='round_trip')
    scope=json.loads((EXP/'scope.json').read_text()) if (EXP/'scope.json').exists() else {}
    if any(d not in scope.get('active_delays',cfg['be_minutes']) for d in delays):
        raise RuntimeError('Owner withdrew timed-management research; do not replay it')
    frame=m.base.aligned(pd.read_csv(m.SOURCE/'candles.csv.gz',float_precision='round_trip'))
    for delay in delays:
        assert delay in cfg['be_minutes']
        rows,audit=m.execute(frame,pd.concat([entry,random],ignore_index=True),delay,cfg)
        print(json.dumps(dict(delay=delay,audit=audit,
            any=m.metric(rows[rows.arm.eq('any')&rows.population.eq('signal')])),ensure_ascii=False),flush=True)


def replay_uncontested():
    """Execute only policy-independent parents while fee geometry is unresolved.

    This is a partial computation, NOT permission to exclude pending parents
    from the strategy denominator. Pending IDs remain explicitly enumerated.
    """
    cfg,_,_=context()
    entry=pd.read_csv(EXP/'entries.csv',float_precision='round_trip')
    random=pd.read_csv(EXP/'controls.csv',float_precision='round_trip')
    allentries=pd.concat([entry,random],ignore_index=True)
    ready=allentries.side*(allentries.tp1-allentries.entry)-cfg['round_trip_cost']*allentries.entry>=2*cfg['tick']
    m.EXP=EXP/'uncontested';m.EXP.mkdir(exist_ok=True)
    dump(m.EXP/'scope.json',dict(partial_only=True,pending_parents=allentries.loc[~ready,'trade_id'].tolist(),
        no_strategy_exclusion_authorized=True))
    frame=m.base.aligned(pd.read_csv(m.SOURCE/'candles.csv.gz',float_precision='round_trip'))
    rows,audit=m.execute(frame,allentries[ready],0,cfg)
    print(json.dumps(dict(partial_only=True,parents=len(rows),audit=audit),ensure_ascii=False),flush=True)


def collect():
    cfg,entry,random=context();summary=[];audits=[];ledger=[];old_comparison=[]
    scope=json.loads((EXP/'scope.json').read_text()) if (EXP/'scope.json').exists() else {}
    active_delays=scope.get('active_delays',cfg['be_minutes'])
    for delay in active_delays:
        folder=EXP/f'native_market_be_{delay}'
        assert (folder/'identity.json').exists(),f'Incomplete delay {delay}'
        identity=json.loads((folder/'identity.json').read_text())
        assert identity['config']==cfg and identity['delay']==delay
        native_stamp=file_stamp(ROOT/'yoyo/layers/l3_backtest/nautilus_independent.py')
        assert native_stamp in identity['code'], 'Native execution changed across arms'
        rows=pd.read_csv(folder/'parents.csv',float_precision='round_trip')
        audit=json.loads((folder/'audit.json').read_text());audits.append(dict(delay=delay,**audit))
        rows['delay']=delay;ledger.append(rows)
        for arm in m.ARMS:
            for segment in (-1,0,1):
                subset=rows[rows.arm.eq(arm)]
                if segment>=0:subset=subset[subset.segment.eq(segment)]
                s=subset[subset.population.eq('signal')];r=subset[subset.population.eq('random')]
                complete=r.groupby('pair_id').closed.all();ids=complete[complete].index
                paired=s[s.closed&s.pair_id.isin(ids)][['pair_id','week','net_r']]
                paired=paired.join(r.groupby('pair_id').net_r.mean().rename('net_r_random'),on='pair_id')
                summary.append(dict(arm=arm,delay=delay,segment=segment,signal=m.metric(s),
                    random=m.metric(r),comparison=inference(paired,cfg['seed'])))
        oldfolder=OLD/f'native_market_be_{delay}'
        if (oldfolder/'identity.json').exists():
            prior=pd.read_csv(oldfolder/'parents.csv',float_precision='round_trip')
            for arm in m.ARMS:
                s=rows[rows.arm.eq(arm)&rows.population.eq('signal')].set_index('parent_id')
                before=prior[prior.arm.eq(arm)&prior.population.eq('signal')].set_index('parent_id')
                shared=s.index.intersection(before.index)
                p=s.loc[shared,['outcome','net_r']].join(before[['outcome','net_r']],rsuffix='_old')
                npairs=len(shared)
                for col in ('entry_i','entry'):
                    assert (s.loc[shared,col]-before.loc[shared,col]).abs().lt(1e-8).all()
                old_comparison.append(dict(arm=arm,delay=delay,old=m.metric(before),new=m.metric(s),
                    paired_rows=npairs,added=sorted(set(s.index)-set(before.index)),
                    removed=sorted(set(before.index)-set(s.index)),
                    delta_r=float((p.net_r-p.net_r_old).sum()),
                    transitions=[dict(old=a,new=b,n=int(n)) for (a,b),n in p.groupby(['outcome_old','outcome']).size().items()]))
    pd.concat(ledger,ignore_index=True).to_csv(EXP/'all_parents.csv',index=False)
    selections=[]
    for arm in m.ARMS if len(active_delays)>1 else ():
        earlier=[x for x in summary if x['arm']==arm and x['segment']==0]
        for objective in ('win_pct','mean_bp'):
            best=max(earlier,key=lambda x:(x['signal'][objective],-x['delay']))
            later=next(x for x in summary if x['arm']==arm and x['segment']==1 and x['delay']==best['delay'])
            selections.append(dict(arm=arm,objective=objective,delay=best['delay'],earlier=best,later=later))
    final_cfg=dict(cfg,be_minutes=active_delays)
    if active_delays==[0]:
        final_cfg.update(exit='Four original25percent tranches; actual-entry0.7percent initial SL. Any native TP immediately protects same-parent remaining tranches at cost-inclusive entry. No holding-time trigger or holding-time expiry. Observation deadline follows wait_minutes; None means no elapsed-time timeout, original next-Monday reset retained.',
            selection='Disabled: owner withdrew n; only mandatory TP-triggered protection is evaluated.',
            inference='Week-cluster sign permutation; four fixed confirmation arms, descriptive chronological halves, no fitted parameter selection.',
            limitations='One-minute sampled intrabar signals and OHLC execution; independent reverse-order payoff sensitivity. No subminute tick sequence, slippage, funding, spread, queue, capacity or funded-account return claims.')
    dump(EXP/'summary.json',dict(config=final_cfg,scope=scope,admission=json.loads((EXP/'admission_receipt.json').read_text()),
        comparisons=summary,selections=selections,native_audits=audits,baseline_audits=[],
        prior_rule_comparison=old_comparison,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        audit_version_note='Only active scope is summarized. Completed timed arms are retained but excluded following owner instruction. Same-endpoint target-only metadata ordering is distinguished from exact stop/target prices,times,reasons.',
        training_eligible=False,production_eligible=False))
    from yoyo.evaluation import monday_fib_confirmation_review as review
    review.EXP=EXP
    review.run()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--experiment',type=Path)
    parser.add_argument('--initialize',action='store_true')
    parser.add_argument('--delays',type=int,nargs='+')
    parser.add_argument('--collect',action='store_true')
    parser.add_argument('--uncontested',action='store_true')
    args=parser.parse_args()
    if args.experiment:EXP=args.experiment.resolve()
    if args.initialize:initialize()
    if args.delays:replay(args.delays)
    if args.uncontested:replay_uncontested()
    if args.collect:collect()

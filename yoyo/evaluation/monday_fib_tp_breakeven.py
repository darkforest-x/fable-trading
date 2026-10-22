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
             'yoyo/evaluation/monday_fib_confirmation_review.py',
             'tests/evaluation/test_nautilus_target_breakeven.py']


def context():
    global OLD
    m.EXP=EXP;m.CODE=CODE
    cfg=json.loads((EXP/'config.json').read_text())
    m.ARMS=tuple(cfg.get('arms',('15m','30m','60m','any')))
    OLD=ROOT/cfg.get('prior_inputs',str(OLD.relative_to(ROOT)))
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:
        raise RuntimeError('Commit builders before native replay')
    entries=pd.read_csv(OLD/'entries.csv',float_precision='round_trip')
    random=pd.read_csv(OLD/'controls.csv',float_precision='round_trip')
    return cfg,entries,random


def initialize():
    cfg,entry,random=context()
    oldcfg=json.loads((OLD/'config.json').read_text())
    if admission_changed(cfg,oldcfg):
        frame=m.base.aligned(pd.read_csv(m.SOURCE/'candles.csv.gz',float_precision='round_trip'))
        entry,decisions,checks=m.prepare(frame,cfg)
        if cfg.get('reuse_execution_experiment'):
            keys=pd.MultiIndex.from_frame(entry[['arm','pair_id']])
            random=random[pd.MultiIndex.from_frame(random[['arm','pair_id']]).isin(keys)].copy()
        else:random=m.controls(frame,entry,cfg)
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
    receipt=(EXP/'prior_execution_receipt.json' if cfg.get('preserve_prior_receipts')
             else OLD/'superseded_receipt.json')
    dump(receipt,dict(reason=cfg.get('change','Owner adds immediate cost breakeven on any TP; these completed runs lack that trigger. Remaining delays interrupted, not completed.'),
        successor=EXP.name,completed=completed,training_eligible=False,production_eligible=False))
    dump(EXP/'admission_receipt.json',dict(source_experiment=OLD.name,
        previous_inputs=[file_stamp(OLD/name) for name in ('entries.csv','controls.csv','decisions.csv')],
        frozen_signals=len(entry),frozen_controls=len(random),
        admission_checks=checks,change=cfg.get('change','Every native target fill synchronously protects remaining same-parent tranches; optional timer remains independent.')))


def admission_changed(cfg,oldcfg):
    """Costs can change first-target feasibility, not just reported returns."""
    defaults=dict(require_arrow=True,adverse_extension_fraction=None,next_15m_close_confirmation=False)
    return any(cfg.get(k,defaults.get(k))!=oldcfg.get(k,defaults.get(k)) for k in
        ('wait_minutes','require_arrow','adverse_extension_fraction',
         'round_trip_cost','tick','stop_fraction','next_15m_close_confirmation','stop_15m_lookback'))


def check_projection_policy(cfg,source_cfg):
    """Protect native fill reuse when costs also set the breakeven barrier."""
    for key in ('round_trip_cost','target_breakeven','be_minutes','tick','stop_fraction','stop_15m_lookback'):
        if cfg.get(key)!=source_cfg.get(key):
            raise ValueError(f'Native projection changes execution policy: {key}')


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
            signals={arm:m.metric(rows[rows.arm.eq(arm)&rows.population.eq('signal')]) for arm in m.ARMS}),ensure_ascii=False),flush=True)


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


def project():
    """Causal entry filter reuses identical independent native positions/fills.

    No exits change, no portfolio competition or liquidity consumption exists.
    Original engine run and accounting evidence remain the execution source.
    """
    cfg,_,_=context()
    source=ROOT/cfg['reuse_execution_experiment']/'native_market_be_0'
    assert (source/'identity.json').exists(),'Source native execution is not complete'
    check_projection_policy(cfg,json.loads((source/'identity.json').read_text())['config'])
    entries=pd.read_csv(EXP/'entries.csv',float_precision='round_trip')
    controls=pd.read_csv(EXP/'controls.csv',float_precision='round_trip')
    desired=m.legs(pd.concat([entries,controls],ignore_index=True)).set_index('trade_id')
    rows=pd.read_csv(source/'trades.csv',float_precision='round_trip')
    rows=rows[rows.trade_id.isin(desired.index)].copy()
    assert len(rows)==len(desired)
    indexed=rows.set_index('trade_id').loc[desired.index]
    for c in ('entry_i','side','entry','stop','risk','target_price','end_i','weight'):
        assert (indexed[c]-desired[c]).abs().lt(1e-8).all(),f'Changed {c}'
    fills=pd.read_csv(source/'fills.csv',float_precision='round_trip')
    fills=fills[fills.trade_id.isin(desired.index)].copy()
    parents=pd.read_csv(source/'parents.csv',float_precision='round_trip')
    parents=parents[parents.parent_id.isin(entries.trade_id.tolist()+controls.trade_id.tolist())].copy()
    assert len(rows)==4*len(parents) and len(fills)==2*len(rows)
    folder=EXP/'native_market_be_0';folder.mkdir(exist_ok=True)
    source_audit=json.loads((source/'audit.json').read_text())
    tape=m.base.aligned(pd.read_csv(m.SOURCE/'candles.csv.gz',float_precision='round_trip'))
    decisions=pd.read_csv(EXP/'decisions.csv',float_precision='round_trip')
    confirmed=0;invalidated=0
    for r in entries.itertuples():
        part=tape.iloc[r.touch_i:r.entry_i+1]
        extreme=part.low.min() if r.side==1 else part.high.max()
        assert -r.side*(extreme-r.level)<=cfg['adverse_extension_fraction']*r.level+1e-8
        confirmed+=1
    for r in decisions[decisions.status.eq('adverse_extension_invalidated')].itertuples():
        part=tape.iloc[int(r.touch_i):int(r.invalid_i)+1]
        adverse=part.low if r.side==1 else part.high
        outside=-r.side*(adverse-r.level)>cfg['adverse_extension_fraction']*r.level+1e-8
        assert outside.iloc[-1] and not outside.iloc[:-1].any()
        if r.entry_i>=0:assert r.invalid_i<=r.entry_i
        invalidated+=1
    dump(EXP/'prefilter_checks.json',dict(retained_signals=confirmed,
        first_adverse_invalidations=invalidated,only_pre_entry_ohlc_read=True,
        unchanged_native_legs=len(rows),unchanged_native_fills=len(fills),
        same_retained_random_parents=len(controls),native_engine_replayed=False))
    audit=dict(engine=source_audit['engine'],version=source_audit['version'],
        target_breakeven=True,target_from_entry=True,positions=len(rows),fills=len(fills),oracle_checked=len(rows),
        native_fixed_target_accounting_checked=len(rows),
        execution_kind='Projection of unchanged independent native positions; no new engine replay',
        source_directory=str(source.relative_to(ROOT)),source_positions=source_audit['positions'],
        source_fills=source_audit['fills'],source_run_audit=source_audit,source_oracle_checks_inherited=True,
        projection_identity_checks=len(parents),causal_prefilter_checks=confirmed+invalidated,
        reverse_payoff_changes=int((abs(rows.net_r-rows.reverse_net_r)>1e-8).sum()))
    rows.to_csv(folder/'trades.csv',index=False);fills.to_csv(folder/'fills.csv',index=False)
    parents.to_csv(folder/'parents.csv',index=False);dump(folder/'audit.json',audit)
    dump(folder/'identity.json',dict(config=cfg,delay=0,mode='market',execution_kind='native_projection',
        source_identity=file_stamp(source/'identity.json'),source_trades=file_stamp(source/'trades.csv'),
        selected_trade_ids=desired.index.tolist(),code=[file_stamp(ROOT/p) for p in CODE]))


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
            oldcfg=json.loads((OLD/'config.json').read_text())
            timing_change=(cfg.get('next_15m_close_confirmation',False)
                           !=oldcfg.get('next_15m_close_confirmation',False))
            for arm in m.ARMS:
                s=rows[rows.arm.eq(arm)&rows.population.eq('signal')].set_index('parent_id')
                before=prior[prior.arm.eq(arm)&prior.population.eq('signal')].set_index('parent_id')
                shared=s.index.intersection(before.index)
                p=s.loc[shared,['outcome','net_r']].join(before[['outcome','net_r']],rsuffix='_old')
                npairs=len(shared)
                shifts=s.loc[shared,'entry_i']-before.loc[shared,'entry_i']
                if not timing_change:
                    for col in ('entry_i','entry'):
                        assert (s.loc[shared,col]-before.loc[shared,col]).abs().lt(1e-8).all()
                old_comparison.append(dict(arm=arm,delay=delay,old=m.metric(before),new=m.metric(s),
                    pairing_unit='same weekly-side first-touch opportunity; entries differ' if timing_change else 'same entry',
                    changed_entry_times=int(shifts.ne(0).sum()),
                    shared_entry_shift_minutes=shifts.to_dict() if timing_change else {},
                    paired_rows=npairs,added=sorted(set(s.index)-set(before.index)),
                    removed=sorted(set(before.index)-set(s.index)),
                    removed_metrics=m.metric(before.loc[before.index.difference(s.index)]),
                    delta_r=float((p.net_r-p.net_r_old).sum()),
                    risk_scale_changed=cfg.get('stop_15m_lookback')!=oldcfg.get('stop_15m_lookback'),
                    shared_mean_change_bp=float((s.loc[shared,'net_bp']-before.loc[shared,'net_bp']).mean()),
                    delta_in_prior_risk_units=float(((s.loc[shared,'net_bp']-before.loc[shared,'net_bp'])/10000
                        *before.loc[shared,'entry']/before.loc[shared,'risk']).sum()),
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
        initial=(f"Latest{cfg['stop_15m_lookback']} completed15m low/high initial SL, no buffer. "
                 if cfg.get('stop_15m_lookback') else f"Actual-entry{100*cfg['stop_fraction']:g}percent initial SL. ")
        final_cfg.update(exit='Four original25percent tranches. '+initial+'Any native TP immediately protects same-parent remaining tranches at cost-inclusive entry. No holding-time trigger or holding-time expiry. Observation deadline follows wait_minutes; None means no elapsed-time timeout, original next-Monday reset retained.',
            selection='Disabled: owner withdrew n; only mandatory TP-triggered protection is evaluated.',
            inference=f'Week-cluster sign permutation; {len(m.ARMS)} fixed confirmation arms, descriptive chronological halves, no fitted parameter selection.',
            limitations='Completed15m body confirmation and one-minute OHLC execution; independent reverse-order payoff sensitivity. No subminute tick sequence, slippage, funding, spread, queue, capacity or funded-account return claims.' if not cfg.get('require_arrow',True) else 'One-minute sampled intrabar arrows; OHLC execution and reverse-order sensitivity. No tick-sequence or funded-account claims.')
    dump(EXP/'summary.json',dict(config=final_cfg,scope=scope,admission=json.loads((EXP/'admission_receipt.json').read_text()),
        comparisons=summary,selections=selections,native_audits=audits,baseline_audits=[],
        prior_rule_comparison=old_comparison,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        audit_version_note='Only active scope is summarized. Completed timed arms are retained but excluded following owner instruction. Same-endpoint target-only metadata ordering is distinguished from exact stop/target prices,times,reasons.',
        training_eligible=False,production_eligible=False))
    from yoyo.evaluation import monday_fib_confirmation_review as review
    review.EXP=EXP;review.ARMS=m.ARMS
    review.run()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--experiment',type=Path)
    parser.add_argument('--initialize',action='store_true')
    parser.add_argument('--delays',type=int,nargs='+')
    parser.add_argument('--collect',action='store_true')
    parser.add_argument('--uncontested',action='store_true')
    parser.add_argument('--project',action='store_true')
    args=parser.parse_args()
    if args.experiment:EXP=args.experiment.resolve()
    if args.initialize:initialize()
    if args.delays:replay(args.delays)
    if args.uncontested:replay_uncontested()
    if args.project:project()
    if args.collect:collect()

"""Owner-authorized near/far Monday target comparison on identical entries.

The 2026-10-24 owner response authorized both interpretations. Keep the frozen
body-confirmation N1..5 cohorts, entry times, original pin stops and control
times. Only the limit target changes: long Monday high / short Monday low.
Monday anchors come from completed NY Mondays in the same original source
segment. Controls inherit the signal's fractional target distance. No new
minimum-RR or cost filter is added. Entries previously rejected because they
had passed the near target remain outside this controlled exit experiment.
Native VectorBT selects exits once per new geometry; near results are reused.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import time
import warnings
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_body_study as body
from yoyo.evaluation import monday_parameter_analysis as analysis
from yoyo.evaluation import monday_parameter_signals as signals
from yoyo.evaluation.monday_body_hold import excursions
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp

EXP = ROOT/'experiments/active/exp-monday-near-far-targets-vectorbt-20261024-v1'
CODE = body.CODE + ['yoyo/evaluation/monday_target_study.py','tests/evaluation/test_monday_targets.py']
EXCURSIONS = ['planned_rr','risk_bp','mfe_r','mae_r','mfe_bp','mfe_net_taker_r',
    'mfe_before_exit_bar_r','mfe_time','mae_time','holding_hours','exit_phase_audited','exit_at_open']


def far_candidates(near, anchors, decimals):
    """Change TP only; inputs are frozen candidates plus completed Monday H/L."""
    rows=[]
    for _,g in near.groupby('config_id',sort=False):
        g=g.copy()
        ss=g[g.population.eq('signal')].set_index('pair_id')
        hi=ss.week.map(anchors.high);lo=ss.week.map(anchors.low)
        if hi.isna().any() or lo.isna().any():
            raise AssertionError('Missing original completed Monday anchor')
        near_target=pd.Series(np.where(ss.side.eq(1),lo,hi),index=ss.index).round(decimals)
        np.testing.assert_allclose(ss.target_price,near_target,atol=10.**(-decimals)/4,rtol=0.)
        target=pd.Series(np.where(ss.side.eq(1),hi,lo),index=ss.index).round(decimals)
        ratio=(target-ss.entry)/ss.entry
        g['near_target_price']=g.target_price
        g['near_trade_key']=g.trade_key
        g['target_price']=g.entry*(1+g.pair_id.map(ratio))
        g.loc[g.population.eq('signal'),'target_price']=g.loc[g.population.eq('signal'),'pair_id'].map(target)
        if (g.side*(g.target_price-g.near_target_price)<=0).any():
            raise AssertionError('Far target must be strictly farther in profit direction')
        g=body.previous.normalized(g,decimals)
        if len(g)!=len(near[near.config_id.eq(g.config_id.iloc[0])]):
            raise AssertionError('Changing target removed an existing entry')
        g['target_mode']='far'
        g['config_id']=[f'far-n{int(n)}' for n in g.confirmations]
        rows.append(g.drop(columns=body.previous.OUTCOME+EXCURSIONS,errors='ignore'))
    return pd.concat(rows,ignore_index=True) if rows else pd.DataFrame()


def process(job,cfg):
    warnings.filterwarnings('ignore',category=pd.errors.Pandas4Warning)
    symbol,paths=job;dest=EXP/'symbols'/symbol;dest.mkdir(parents=True,exist_ok=True)
    prior=body.EXP/'symbols'/symbol;old_identity=json.loads((prior/'identity.json').read_text())
    stamps=[file_stamp(p) for p in paths]
    if stamps!=old_identity['inputs']: raise RuntimeError(f'Frozen inputs changed: {symbol}')
    ledger=prior/'trades.csv.gz'
    identity=dict(inputs=stamps,config=cfg,prior_ledger=file_stamp(ledger) if ledger.exists() else None,
        code=[file_stamp(ROOT/p) for p in CODE],versions={p:version(p) for p in ('vectorbt','numpy','pandas','numba')})
    if (dest/'done.json').exists():
        if json.loads((dest/'identity.json').read_text())!=identity: raise RuntimeError(f'Completed dependency changed: {symbol}')
        return json.loads((dest/'done.json').read_text())
    start=time.monotonic();raw=pd.read_csv(ledger,float_precision='round_trip') if ledger.exists() else pd.DataFrame()
    old=raw[raw.config_id.str.startswith('body-')].copy() if len(raw) else raw.copy()
    counts=dict(new_positions=0,reused_far_positions=0,near_rows=len(old),signal_rows=0,paired_entry_checks=0)
    old_done=json.loads((prior/'done.json').read_text())
    if len(old):
        blocked=set()
        if symbol=='XAUUSD':
            minutes,_=body.previous.gold_clock.read_minutes(paths)
            frame,q=body.previous.quality.clean_minutes(minutes)
            blocked=body.previous.quality.invalid_monday_weeks(q)
        else:
            frame=body.previous.volume.load(paths);frame['quality_segment']=0
        all_rows=[];all_fills=[]
        for segment,chunk in frame.groupby('quality_segment',sort=True):
            near=old[old.source_segment.eq(segment)].copy()
            if near.empty: continue
            decimals=max(2,min(9,int(np.ceil(-np.log10(chunk[body.previous.old.OHLC].to_numpy().min())))+7))
            anchors=signals.base_context(chunk,symbol,blocked)['anchors']
            far=far_candidates(near,anchors,decimals)
            unique=far.drop_duplicates('trade_key')
            cache={r['trade_key']:{k:r.get(k,np.nan) for k in body.previous.OUTCOME}
                for r in raw[raw.source_segment.eq(segment)].to_dict('records')}
            new=unique[~unique.trade_key.isin(cache)]
            result,fills=body.previous.execute(chunk,new,symbol,cfg)
            counts['new_positions']+=len(new);counts['reused_far_positions']+=len(unique)-len(new)
            for r in result.to_dict('records'):cache[r['trade_key']]={k:r[k] for k in body.previous.OUTCOME}
            if len(fills):all_fills.append(fills)
            far=pd.concat([far.reset_index(drop=True),pd.DataFrame([cache[k] for k in far.trade_key])],axis=1)
            audited=excursions(chunk,far[far.population.eq('signal')])
            far=pd.concat([audited,far[far.population.eq('random')]],ignore_index=True)
            near['target_mode']='near';near['near_trade_key']=near.trade_key;near['near_target_price']=near.target_price
            near['config_id']=[f'near-n{int(n)}' for n in near.confirmations]
            counts['paired_entry_checks']+=len(near)
            all_rows.extend([near,far])
        combined=pd.concat(all_rows,ignore_index=True)
        combined['crosses_selection_cut']=(pd.to_datetime(combined.entry_time,utc=True)<pd.Timestamp(cfg['split'])) & (pd.to_datetime(combined.exit_interval_end,utc=True)>pd.Timestamp(cfg['split']))
        combined.to_csv(dest/'trades.csv.gz',index=False)
        counts['signal_rows']=int(combined.population.eq('signal').sum())
        if all_fills: pd.concat(all_fills,ignore_index=True).to_csv(dest/'native-fills.csv.gz',index=False)
    done=dict(symbol=symbol,source_bars=old_done['source_bars'],first=old_done['first'],last=old_done['last'],seconds=time.monotonic()-start,**counts)
    dump(dest/'identity.json',identity);dump(dest/'done.json',done)
    return done


def finite(value):
    if isinstance(value,dict):return {k:finite(v) for k,v in value.items()}
    if isinstance(value,list):return [finite(v) for v in value]
    if isinstance(value,float) and not np.isfinite(value):return None
    return value


def target_pairs(g):
    a=g[g.population.eq('signal') & g.target_mode.eq('near')].copy()
    b=g[g.population.eq('signal') & g.target_mode.eq('far')].copy()
    return a.merge(b,on=['symbol','confirmations','pair_id'],suffixes=('_near','_far'),validate='one_to_one')


def transition(g,basis):
    p=target_pairs(g);both=p.closed_near & p.closed_far
    x=p[both];delta=x[f'{basis}_r_far']-x[f'{basis}_r_near']
    near_tp=p.reason_near.eq(4)
    return dict(entries=len(p),both_closed=int(both.sum()),near_closed=int(p.closed_near.sum()),far_closed=int(p.closed_far.sum()),
        near_tp=int(near_tp.sum()),near_tp_far_tp=int((near_tp&p.reason_far.eq(4)).sum()),
        near_tp_far_sl=int((near_tp&p.reason_far.eq(1)).sum()),near_tp_far_censored=int((near_tp&~p.closed_far).sum()),
        both_closed_near_mean_r=float(x[f'{basis}_r_near'].mean()),both_closed_far_mean_r=float(x[f'{basis}_r_far'].mean()),
        both_closed_delta_r=float(delta.mean()),both_closed_delta_bp=float((x[f'{basis}_bp_far']-x[f'{basis}_bp_near']).mean()),
        near_sl_same_exit=bool((p.loc[p.reason_near.eq(1),'exit_original_i_near']==p.loc[p.reason_near.eq(1),'exit_original_i_far']).all()))


def summarize(cfg):
    warnings.filterwarnings('ignore',category=pd.errors.Pandas4Warning)
    f=analysis.add_actual_week(pd.concat([pd.read_csv(p,float_precision='round_trip') for p in (EXP/'symbols').glob('*/trades.csv.gz')],ignore_index=True))
    f.to_csv(EXP/'all-trades.csv.gz',index=False)
    signals_only=f[f.population.eq('signal')]
    signals_only.to_csv(EXP/'signal-trades.csv.gz',index=False)
    signals_only[signals_only.confirmations.eq(3)].to_csv(EXP/'n3-trades.csv.gz',index=False)
    rows=[];lookup={};splits=[]
    for config,g0 in f.groupby('config_id'):
        mode=str(g0.target_mode.iloc[0]);n=int(g0.confirmations.iloc[0])
        for group,g in analysis.group_data(g0).items():
            if g.empty:continue
            basis='gross' if group in ('XAUUSD','GROUP1_GROSS') else 'net'
            for period in ('all','earlier','later'):
                x=g if period=='all' else g[g.segment.eq(int(period=='later'))&~g.crosses_selection_cut]
                d=body.detail(x,basis)
                # A combined group containing unknown gold costs has no full fee mean.
                if group=='GROUP1_GROSS':d['fee_r_mean']=None
                row=dict(config_id=config,target_mode=mode,confirmations=n,group=group,period=period,basis=basis,**d)
                rows.append(row);lookup[(config,group,period)]=(row,x)
            if n==3:
                for axis in ('side','year'):
                    values=g.side if axis=='side' else pd.to_datetime(g.entry_time,utc=True).dt.year
                    for value,x in g.groupby(values):
                        splits.append(dict(config_id=config,target_mode=mode,group=group,basis=basis,axis=axis,value=int(value),**body.detail(x,basis)))
    metrics=pd.DataFrame(rows);metrics.to_csv(EXP/'metrics.csv',index=False)
    pd.DataFrame(splits).to_csv(EXP/'year-direction.csv',index=False)
    selections=[]
    for (mode,group),e in metrics[metrics.period.eq('earlier')].groupby(['target_mode','group']):
        e=e[e.closed.gt(0)].sort_values(['mean_r','confirmations'],ascending=[False,True])
        if e.empty:continue
        best=e.iloc[0];later,x=lookup[(best.config_id,group,'later')]
        baseline,_=lookup[(f'{mode}-n3',group,'later')]
        selections.append(dict(target_mode=mode,group=group,basis=best.basis,confirmations=int(best.confirmations),
            earlier_n=int(best.closed),earlier_mean_r=float(best.mean_r),later=later,baseline_later=baseline,
            inference=analysis.bootstrap(analysis.strict_pairs(x,best.basis),best.basis,cfg['seed'])))
    tests=sorted([(i,s['inference']['p_raw']) for i,s in enumerate(selections) if 'p_raw' in s['inference']],key=lambda x:x[1]);adj=0.
    for rank,(i,p) in enumerate(tests):
        adj=max(adj,min(1.,p*(len(tests)-rank)));selections[i]['inference']['holm_p']=adj
    transitions=[]
    for group,g in analysis.group_data(f[f.confirmations.eq(3)]).items():
        if g.empty:continue
        basis='gross' if group in ('XAUUSD','GROUP1_GROSS') else 'net'
        transitions.append(dict(group=group,basis=basis,**transition(g,basis)))
    pd.DataFrame(transitions).to_csv(EXP/'target-transitions.csv',index=False)
    done=[json.loads(p.read_text()) for p in (EXP/'symbols').glob('*/done.json')]
    summary=dict(symbols=len(done),configurations=10,signal_rows=len(signals_only),
        new_far_positions=sum(d['new_positions'] for d in done),reused_far_positions=sum(d['reused_far_positions'] for d in done),
        reused_near_rows=sum(d['near_rows'] for d in done),paired_entry_checks=sum(d['paired_entry_checks'] for d in done),
        n3=[r for r in rows if r['confirmations']==3 and r['period']=='all'],selection=selections,transitions=transitions,
        limitations=cfg['limitations'],training_eligible=False,production_eligible=False)
    dump(EXP/'summary.json',finite(summary));dump(EXP/'coverage.json',done)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('n3','selection','transitions','limitations')}),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--workers',type=int,default=3);ap.add_argument('--symbols',nargs='*');ap.add_argument('--summarize',action='store_true');args=ap.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:raise RuntimeError('Commit source before results')
    cfg=json.loads((EXP/'config.json').read_text())
    if args.summarize:summarize(cfg);return
    jobs,_=body.previous.old.jobs(cfg)
    if args.symbols:jobs=[j for j in jobs if j[0] in args.symbols]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(process,j,cfg) for j in jobs]):print(json.dumps(future.result()),flush=True)
    if not args.symbols:summarize(cfg)


if __name__=='__main__':main()

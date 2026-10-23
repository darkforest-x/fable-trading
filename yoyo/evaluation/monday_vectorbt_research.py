"""Owner-authorized one-variable Monday parameter studies using VectorBT.

Confirmations1..5 for cases2/3/4; prior-volume10/20/30/40 for case2 with N=3.
Existing immutable baseline fills and identical trade geometries are reused.
New geometries pass once through VectorBT native stop kernels and Portfolio.
Features and execution are separate; no exit oracle or second-engine replay.
Selection uses mean independent R before2023, excluding boundary-crossing
positions, then evaluates later history. Previously researched history is not
a pristine holdout. XAU and the combined first group use gross R; crypto uses
net R. Funding/slippage/spread and account capacity remain unmodelled.
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
warnings.filterwarnings('ignore', category=pd.errors.Pandas4Warning)

from yoyo.evaluation import monday_5m_wick_engulf as old
from yoyo.evaluation import monday_15m_volume_rejection as volume
from yoyo.evaluation import monday_5m_wick_gold_clock as gold_clock
from yoyo.evaluation import monday_5m_wick_gold as quality
from yoyo.evaluation import monday_parameter_signals as signals
from yoyo.evaluation.monday_vectorbt_engine import execute
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp

EXP = ROOT/'experiments/active/exp-monday-vectorbt-parameters-20261023-v1'
BASES = {2: 'exp-monday-15m-volume-rejection-20261023-v1',
         3: 'exp-monday-1h-wick-hold-20261023-v1',
         4: 'exp-monday-4h-close-hold-20261023-v1'}
CODE = ['yoyo/evaluation/monday_parameter_signals.py','yoyo/evaluation/monday_vectorbt_engine.py',
        'yoyo/evaluation/monday_vectorbt_research.py','tests/evaluation/test_monday_vectorbt.py']
OUTCOME = ['exit_original_i','exit_path_segment','exit_price','reason','closed',
           'exit_interval_start','exit_interval_end','gross_r','gross_bp','fee_r','net_r','net_bp',
           'fee_entry_rate','fee_exit_rate','ambiguous_bar','stop_first_gross_r','stop_first_net_r','engine_origin']


def configurations():
    result=[]
    for case in (2,3,4):
        for n in (3,1,2,4,5):
            result.append(dict(case=case,confirmations=n,volume_window=20,
                               config_id=f'case{case}-n{n}-v20'))
    for window in (10,30,40):
        result.append(dict(case=2,confirmations=3,volume_window=window,
                           config_id=f'case2-n3-v{window}'))
    return result


def normalized(frame, decimals):
    f=frame.copy()
    if f.empty:return f
    for col in ('entry','stop','target_price'):
        f[col]=f[col].round(decimals)
    f['risk']=f.side*(f.entry-f.stop)
    tick=10.**(-decimals)
    f=f[f.risk.ge(tick*.999)&(f.side*(f.target_price-f.entry)>0)].copy()
    f['trade_key']=[f'{pd.Timestamp(t).value}:{int(s)}:{e:.{decimals}f}:{sl:.{decimals}f}:{tp:.{decimals}f}'
                    for t,s,e,sl,tp in zip(f.entry_time,f.side,f.entry,f.stop,f.target_price)]
    return f


def baseline_files(case,symbol,segment):
    folder=ROOT/'experiments/active'/BASES[case]/'symbols'/symbol
    if case != 2:folder=folder/f'segment-{segment}'
    return folder/'candidates.csv.gz',folder/'trades.csv.gz'


def process(job,cfg):
    warnings.filterwarnings('ignore', category=pd.errors.Pandas4Warning)
    symbol,paths=job; dest=EXP/'symbols'/symbol;dest.mkdir(parents=True,exist_ok=True)
    identity=dict(inputs=[file_stamp(p) for p in paths],config=cfg,
                  code=[file_stamp(ROOT/p) for p in CODE],
                  versions={p:version(p) for p in ('vectorbt','numpy','pandas','numba')})
    if (dest/'done.json').exists():
        if json.loads((dest/'identity.json').read_text()) != identity:
            raise RuntimeError(f'Changed completed study inputs: {symbol}')
        return json.loads((dest/'done.json').read_text())
    begin=time.monotonic();blocked=set()
    if symbol=='XAUUSD':
        raw,_=gold_clock.read_minutes(paths);f,q=quality.clean_minutes(raw)
        blocked=quality.invalid_monday_weeks(q);dump(dest/'data-quality.json',q)
    else:
        f=volume.load(paths);f['quality_segment']=0
    all_rows=[];all_fills=[];counts=dict(baseline_checks=0,new_positions=0,reused_positions=0,unique_positions=0)
    for segment,chunk in f.groupby('quality_segment',sort=True):
        decimals=max(2,min(9,int(np.ceil(-np.log10(chunk[old.OHLC].to_numpy().min())))+7))
        base=signals.base_context(chunk,symbol,blocked)
        cases=(3,4) if symbol=='XAUUSD' else (2,3,4)
        prepared={case:signals.prepare_case(base,case) for case in cases}
        baseline={};cache={};control_cache={}
        for case in cases:
            candidate_path,ledger_path=baseline_files(case,symbol,segment)
            if candidate_path.exists():
                try:
                    original=normalized(pd.read_csv(candidate_path,float_precision='round_trip'),decimals)
                except pd.errors.EmptyDataError:
                    original=pd.DataFrame()
            else:original=pd.DataFrame()
            baseline[case]=original
            if ledger_path.exists():
                ledger=normalized(pd.read_csv(ledger_path,float_precision='round_trip'),decimals)
                ledger['engine_origin']='cached_nautilus_1.231.0'
                for row in ledger.to_dict('records'):
                    key=row['trade_key'];outcome={k:row.get(k,np.nan) for k in OUTCOME}
                    if key in cache:
                        np.testing.assert_allclose([outcome['exit_price'],outcome['gross_r']],
                                                   [cache[key]['exit_price'],cache[key]['gross_r']],rtol=1e-8,atol=1e-8)
                    cache[key]=outcome
            if len(original):
                for row in original[original.population.eq('signal')].itertuples():
                    controls=original[original.population.eq('random') & original.pair_id.eq(row.pair_id)]
                    control_cache[(case,row.trade_key)]=controls.to_dict('records')
        variants=[]
        for variant in cfg['candidates']:
            case=variant['case']
            if case not in cases:continue
            generated=signals.candidates(prepared[case],variant['confirmations'],variant['volume_window'],cfg)
            if len(generated) and case!=2:
                for col in ('trade_id','pair_id'):generated[col]=f'q{segment}-'+generated[col]
            generated=normalized(generated,decimals)
            original=baseline[case]
            if variant['confirmations']==3 and variant['volume_window']==20:
                expected=set(original[original.population.eq('signal')].trade_key) if len(original) else set()
                actual=set(generated[generated.population.eq('signal')].trade_key) if len(generated) else set()
                if actual!=expected:raise AssertionError(f'Baseline signal mismatch {symbol} case{case} segment{segment}: extra={list(actual-expected)[:3]},missing={list(expected-actual)[:3]}')
                counts['baseline_checks']+=1
                generated=original.copy()
            if generated.empty:continue
            signals_only=generated[generated.population.eq('signal')]
            revised_controls=[]
            for row in signals_only.itertuples():
                key=(case,row.trade_key)
                if key not in control_cache:
                    control_cache[key]=generated[generated.population.eq('random') & generated.pair_id.eq(row.pair_id)].to_dict('records')
                for k,item in enumerate(control_cache[key]):
                    updated=item.copy();updated['pair_id']=row.pair_id;updated['trade_id']=f'{row.pair_id}-c{k}'
                    revised_controls.append(updated)
            generated=pd.concat([signals_only,pd.DataFrame(revised_controls)],ignore_index=True)
            for key,val in variant.items():generated[key]=val
            generated['quality_segment']=int(segment)
            variants.append(generated)
        if not variants:continue
        candidates=pd.concat(variants,ignore_index=True)
        unique=candidates.drop_duplicates('trade_key')
        new=unique[~unique.trade_key.isin(cache)].copy()
        result,fills=execute(chunk,new,symbol,cfg)
        counts['new_positions']+=len(new);counts['unique_positions']+=len(unique)
        counts['reused_positions']+=len(unique)-len(new)
        for row in result.to_dict('records'):cache[row['trade_key']]={k:row[k] for k in OUTCOME}
        if len(fills):all_fills.append(fills)
        outcomes=pd.DataFrame([cache[k] for k in candidates.trade_key])
        rows=pd.concat([candidates.reset_index(drop=True).drop(columns=OUTCOME,errors='ignore'),outcomes],axis=1)
        rows['source_segment']=int(segment)
        all_rows.append(rows)
    if all_rows:
        combined=pd.concat(all_rows,ignore_index=True)
        combined['crosses_selection_cut']=(pd.to_datetime(combined.entry_time,utc=True)<pd.Timestamp(cfg['split'])) & (pd.to_datetime(combined.exit_interval_end,utc=True)>pd.Timestamp(cfg['split']))
        combined.to_csv(dest/'trades.csv.gz',index=False)
        nsignal=int(combined.population.eq('signal').sum())
    else:nsignal=0
    if all_fills:pd.concat(all_fills,ignore_index=True).to_csv(dest/'vectorbt-fills.csv.gz',index=False)
    done=dict(symbol=symbol,source_bars=len(f),first=str(f.index[0]),last=str(f.index[-1]),
              signal_rows=nsignal,seconds=time.monotonic()-begin,**counts)
    dump(dest/'identity.json',identity);dump(dest/'done.json',done)
    return done


def paired(g,metric):
    a=g[g.population.eq('signal')&g.closed].set_index('pair_id')
    b=g[g.population.eq('random')&g.closed].groupby('pair_id')
    means=b[metric].mean()[b.size().eq(3)]
    return a.join(means.rename('control'),how='inner')


def summarize(cfg):
    completed=[json.loads(p.read_text()) for p in (EXP/'symbols').glob('*/done.json')]
    all_trades=pd.concat([pd.read_csv(p,float_precision='round_trip') for p in (EXP/'symbols').glob('*/trades.csv.gz')],ignore_index=True)
    all_trades.to_csv(EXP/'all-trades.csv.gz',index=False)
    rows=[]
    for config_id,c in all_trades.groupby('config_id',sort=True):
        variant=next(v for v in cfg['candidates'] if v['config_id']==config_id)
        groups={'BTC':c[c.symbol.eq('BTCUSDT')],'ETH':c[c.symbol.eq('ETHUSDT')],
                'XAUUSD':c[c.symbol.eq('XAUUSD')],
                'GROUP1_GROSS':c[c.symbol.isin(['BTCUSDT','ETHUSDT','XAUUSD'])],
                'ALTCOINS':c[~c.symbol.isin(['BTCUSDT','ETHUSDT','XAUUSD'])]}
        for group,g in groups.items():
            if g.empty:continue
            basis='gross' if group in ('XAUUSD','GROUP1_GROSS') else 'net'
            for period in ('all','earlier','later'):
                x=g if period=='all' else g[g.segment.eq(int(period=='later')) & ~g.crosses_selection_cut]
                a=x[x.population.eq('signal')&x.closed];b=x[x.population.eq('random')&x.closed]
                ps=paired(x,f'{basis}_bp');r=a[f'{basis}_r'];cr=b[f'{basis}_r']
                total=float(r.sum());loss=float(-r[r<0].sum())
                rows.append(dict(**variant,group=group,period=period,basis=basis,
                    signals=int(x.population.eq('signal').sum()),closed=len(a),censored=int((x.population.eq('signal')&~x.closed).sum()),
                    win_rate=float((r>0).mean()) if len(r) else None,
                    mean_r=float(r.mean()) if len(r) else None,total_r=total,
                    profit_factor=float(r[r>0].sum()/loss) if loss else None,
                    mean_bp=float(a[f'{basis}_bp'].mean()) if len(a) else None,
                    random_n=len(b),random_mean_r=float(cr.mean()) if len(b) else None,
                    random_mean_bp=float(b[f'{basis}_bp'].mean()) if len(b) else None,
                    paired=len(ps),paired_excess_bp=float((ps[f'{basis}_bp']-ps.control).mean()) if len(ps) else None,
                    stop_first_mean_r=float(a[f'stop_first_{basis}_r'].mean()) if len(a) else None))
    table=pd.DataFrame(rows);table.to_csv(EXP/'metrics.csv',index=False)
    selected=[]
    for case,axis,values in [(2,'confirmations',[1,2,3,4,5]),(3,'confirmations',[1,2,3,4,5]),(4,'confirmations',[1,2,3,4,5]),(2,'volume_window',[10,20,30,40])]:
        subset=table[table.case.eq(case) & (table.volume_window.eq(20) if axis=='confirmations' else table.confirmations.eq(3))]
        for group,early in subset[subset.period.eq('earlier')].groupby('group'):
            early=early[early.closed.gt(0)].sort_values(['mean_r',axis],ascending=[False,True])
            if early.empty:continue
            best=early.iloc[0]
            later=subset[subset.group.eq(group)&subset.period.eq('later')&subset.config_id.eq(best.config_id)].iloc[0]
            baseline=subset[subset.group.eq(group)&subset.period.eq('later')&subset.confirmations.eq(3)&subset.volume_window.eq(20)].iloc[0]
            raw=all_trades[all_trades.config_id.eq(best.config_id)&all_trades.segment.eq(1)&~all_trades.crosses_selection_cut]
            if group=='GROUP1_GROSS':raw=raw[raw.symbol.isin(['BTCUSDT','ETHUSDT','XAUUSD'])]
            elif group=='ALTCOINS':raw=raw[~raw.symbol.isin(['BTCUSDT','ETHUSDT','XAUUSD'])]
            else:raw=raw[raw.symbol.eq({'BTC':'BTCUSDT','ETH':'ETHUSDT'}.get(group,group))]
            ps=paired(raw,f'{best.basis}_bp')
            uncertainty={}
            if len(ps):
                cluster=ps.assign(delta=ps[f'{best.basis}_bp']-ps.control).groupby('week').delta.agg(['sum','count'])
                sums=cluster['sum'].to_numpy();counts=cluster['count'].to_numpy();rng=np.random.default_rng(cfg['seed']);draws=[];null=[]
                for _ in range(2000):
                    ix=rng.integers(len(sums),size=len(sums));draws.append(sums[ix].sum()/counts[ix].sum())
                    null.append((sums*rng.choice([-1,1],len(sums))).sum()/counts.sum())
                uncertainty=dict(paired_later=len(ps),excess_bp=float(sums.sum()/counts.sum()),
                                 ci95_bp=list(map(float,np.quantile(draws,[.025,.975]))),
                                 unadjusted_one_sided_p=float((1+np.sum(np.array(null)>=sums.sum()/counts.sum()))/2001))
            selected.append(dict(case=case,axis=axis,group=group,selected_value=int(best[axis]),
                selection_metric=f'earlier mean independent {best.basis} R',earlier_n=int(best.closed),earlier_mean_r=float(best.mean_r),
                later_n=int(later.closed),later_mean_r=float(later.mean_r) if pd.notna(later.mean_r) else None,
                later_random_r=float(later.random_mean_r) if pd.notna(later.random_mean_r) else None,
                baseline_later_mean_r=float(baseline.mean_r) if pd.notna(baseline.mean_r) else None,
                later_mean_bp=float(later.mean_bp) if pd.notna(later.mean_bp) else None,**uncertainty))
    # Holm across the actually selected later tests, not an uncorrected search winner claim.
    tested=sorted([(i,r['unadjusted_one_sided_p']) for i,r in enumerate(selected) if 'unadjusted_one_sided_p' in r],key=lambda x:x[1])
    previous=0.
    for rank,(i,p) in enumerate(tested):
        previous=max(previous,min(1.,p*(len(tested)-rank)));selected[i]['holm_p']=previous
    summary=dict(configurations=18,symbols=len(completed),signal_rows=int(all_trades.population.eq('signal').sum()),
        unique_new_framework_positions=sum(d['new_positions'] for d in completed),
        reused_unique_positions=sum(d['reused_positions'] for d in completed),
        baseline_signal_checks=sum(d['baseline_checks'] for d in completed),selection=selected,
        limitations=cfg['limitations'],training_eligible=False,production_eligible=False)
    dump(EXP/'summary.json',summary);dump(EXP/'coverage.json',completed)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--workers',type=int,default=3);ap.add_argument('--symbols',nargs='*');ap.add_argument('--summarize',action='store_true');args=ap.parse_args()
    cfg=json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:raise RuntimeError('Commit builder before results')
    completed=EXP/'completion.json'
    if completed.exists() and not args.summarize:
        receipt=json.loads(completed.read_text())
        if receipt['config']!=cfg:raise RuntimeError('Completed study configuration changed; use a new study directory')
        for stamp in receipt['code']+receipt['inputs']:
            if file_stamp(stamp['path'])!=stamp:raise RuntimeError('Completed study dependency changed; preserve prior results')
        if receipt['versions']!={p:version(p) for p in receipt['versions']}:
            raise RuntimeError('Completed study runtime changed')
        result=json.loads((EXP/'summary-entry-week.json').read_text())
        print(json.dumps(dict(status='cached_complete',canonical_summary=str(EXP/'summary-entry-week.json'),
                              symbols=result['symbols'],configurations=result['configurations'],
                              new_positions_replayed=0),ensure_ascii=False),flush=True)
        return
    if args.summarize:summarize(cfg);return
    jobs,_=old.jobs(cfg)
    if args.symbols:jobs=[j for j in jobs if j[0] in args.symbols]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending={pool.submit(process,job,cfg):job[0] for job in jobs}
        for future in as_completed(pending):
            done=future.result();print(json.dumps(done,ensure_ascii=False),flush=True)
    if not args.symbols:summarize(cfg)


if __name__=='__main__':main()

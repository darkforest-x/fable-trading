"""Single-variable 1h body-hold study; immutable old results are the reference.

First compare new body N3 versus old wick N3. Then vary only new-rule N1..5.
New unique geometries run once through the existing native VectorBT adapter;
identical source/entry/side/stop/target reuse prior fills. Actual-entry NY week
controls, independent R, chronological selection and fees follow the prior study.
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
from yoyo.evaluation import monday_vectorbt_research as previous
from yoyo.evaluation import monday_parameter_analysis as analysis
from yoyo.evaluation import monday_parameter_signals as signals
from yoyo.evaluation.monday_body_hold import prepare_body, excursions
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp

EXP = ROOT/'experiments/active/exp-monday-1h-body-hold-vectorbt-20261024-v1'
CODE = previous.CODE + ['yoyo/evaluation/monday_body_hold.py','yoyo/evaluation/monday_body_study.py',
    'yoyo/evaluation/monday_parameter_analysis.py','tests/evaluation/test_monday_body_hold.py']


def process(job, cfg):
    warnings.filterwarnings('ignore', category=pd.errors.Pandas4Warning)
    symbol, paths = job
    dest = EXP/'symbols'/symbol
    dest.mkdir(parents=True, exist_ok=True)
    prior = previous.EXP/'symbols'/symbol
    old_identity = json.loads((prior/'identity.json').read_text())
    stamps = [file_stamp(p) for p in paths]
    if stamps != old_identity['inputs']:
        raise RuntimeError(f'Frozen input changed: {symbol}')
    ledger_path = prior/'trades.csv.gz'
    identity = dict(inputs=stamps, config=cfg, code=[file_stamp(ROOT/p) for p in CODE],
        prior_ledger=file_stamp(ledger_path) if ledger_path.exists() else None,
        versions={p:version(p) for p in ('vectorbt','numpy','pandas','numba')})
    if (dest/'done.json').exists():
        if json.loads((dest/'identity.json').read_text()) != identity:
            raise RuntimeError(f'Completed new-study dependency changed: {symbol}')
        return json.loads((dest/'done.json').read_text())
    start = time.monotonic(); blocked = set()
    if symbol == 'XAUUSD':
        raw,_ = previous.gold_clock.read_minutes(paths)
        f,q = previous.quality.clean_minutes(raw)
        blocked = previous.quality.invalid_monday_weeks(q)
        dump(dest/'data-quality.json',q)
    else:
        f = previous.volume.load(paths); f['quality_segment'] = 0
    old = pd.read_csv(ledger_path, float_precision='round_trip') if ledger_path.exists() else pd.DataFrame()
    rows=[]; fills=[]; reused=new_count=0; strict_checks=0
    for segment,chunk in f.groupby('quality_segment',sort=True):
        old_seg = old[old.source_segment.eq(segment)].copy() if len(old) else old.copy()
        cache = {r['trade_key']:{k:r.get(k,np.nan) for k in previous.OUTCOME} for r in old_seg.to_dict('records')}
        base = signals.base_context(chunk,symbol,blocked)
        ctx = prepare_body(base)
        decimals = max(2,min(9,int(np.ceil(-np.log10(chunk[previous.old.OHLC].to_numpy().min())))+7))
        variants=[]; controls_cache={}
        old3 = old_seg[old_seg['case'].eq(3)].copy() if len(old_seg) else old_seg.copy()
        if len(old3):
            for _,g in old3.groupby('config_id'):
                for r in g[g.population.eq('signal')].itertuples():
                    controls_cache[r.trade_key] = g[g.population.eq('random') & g.pair_id.eq(r.pair_id)].to_dict('records')
            ref = old3[old3.confirmations.eq(3)].copy()
            if len(ref):
                ref['config_id']='strict-n3'; ref['rule']='full_range_inside'; variants.append(ref)
        expected = set(old3[old3.confirmations.eq(3) & old3.population.eq('signal')].trade_key) if len(old3) else set()
        for n in (3,1,2,4,5):
            generated = signals.candidates(ctx,n,20,cfg)
            if len(generated):
                for col in ('trade_id','pair_id'):
                    generated[col] = f'q{segment}-'+generated[col]
                generated = previous.normalized(generated,decimals)
            if n == 3:
                actual = set(generated[generated.population.eq('signal')].trade_key) if len(generated) else set()
                if not expected <= actual:
                    raise AssertionError(f'Body relaxation lost strict signal: {symbol} {segment}')
                strict_checks += 1
            if generated.empty: continue
            ss = generated[generated.population.eq('signal')].copy(); controls=[]
            rng = np.random.default_rng(cfg['seed'])
            for r in ss.itertuples():
                actual_week = base['week5'][r.entry_i]
                cached = controls_cache.get(r.trade_key)
                valid = cached is not None and len(cached)==3 and all(base['week5'][int(c['entry_i'])]==actual_week and int(c['bucket'])==r.bucket for c in cached)
                if not valid:
                    pool = np.asarray(ctx['pools'].get((actual_week,r.bucket),[]),dtype=int)
                    pool = pool[pool != r.entry_i]
                    cached=[]
                    if len(pool)>=3:
                        for i in rng.choice(pool,3,replace=False):
                            e=float(chunk.open.iloc[i]); scale=e/r.entry
                            cached.append(dict(symbol=symbol,population='random',entry_i=int(i),side=r.side,
                                entry=e,stop=e-r.side*r.risk*scale,target_price=e+(r.target_price-r.entry)*scale,
                                risk=r.risk*scale,week=actual_week,bucket=r.bucket,entry_time=str(chunk.index[i]),
                                segment=int(chunk.index[i]>=pd.Timestamp(cfg['split']))))
                    controls_cache[r.trade_key]=cached
                for k,c in enumerate(cached):
                    c=c.copy(); c.update(pair_id=r.pair_id,trade_id=f'{r.pair_id}-c{k}')
                    controls.append(c)
            g=previous.normalized(pd.concat([ss,pd.DataFrame(controls)],ignore_index=True),decimals)
            g['config_id']=f'body-n{n}'; g['rule']='body_inside_inclusive';g['confirmations']=n
            g['case']=3;g['quality_segment']=int(segment);g['source_segment']=int(segment)
            variants.append(g)
        if not variants: continue
        cand=pd.concat(variants,ignore_index=True).drop(columns=previous.OUTCOME,errors='ignore')
        unique=cand.drop_duplicates('trade_key'); new=unique[~unique.trade_key.isin(cache)]
        result,native_fills=previous.execute(chunk,new,symbol,cfg)
        for r in result.to_dict('records'):
            cache[r['trade_key']]={k:r[k] for k in previous.OUTCOME}
        if len(native_fills): fills.append(native_fills)
        new_count+=len(new);reused+=len(unique)-len(new)
        merged=pd.concat([cand.reset_index(drop=True),pd.DataFrame([cache[k] for k in cand.trade_key])],axis=1)
        # Excursions are needed for signal diagnostics, never to choose controls.
        audited=excursions(chunk,merged[merged.population.eq('signal')])
        rows.append(pd.concat([audited,merged[merged.population.eq('random')]],ignore_index=True))
    count=0
    if rows:
        all_rows=pd.concat(rows,ignore_index=True)
        all_rows['crosses_selection_cut']=(pd.to_datetime(all_rows.entry_time,utc=True)<pd.Timestamp(cfg['split'])) & (pd.to_datetime(all_rows.exit_interval_end,utc=True)>pd.Timestamp(cfg['split']))
        all_rows.to_csv(dest/'trades.csv.gz',index=False)
        count=int(all_rows.population.eq('signal').sum())
    if fills: pd.concat(fills,ignore_index=True).to_csv(dest/'native-fills.csv.gz',index=False)
    done=dict(symbol=symbol,source_bars=len(f),first=str(f.index[0]),last=str(f.index[-1]),
        new_positions=new_count,reused_positions=reused,signal_rows=count,strict_subset_checks=strict_checks,seconds=time.monotonic()-start)
    dump(dest/'identity.json',identity);dump(dest/'done.json',done)
    return done


def detail(g,basis):
    d=analysis.describe(g,basis)
    a=g[g.population.eq('signal') & g.closed]; r=a[f'{basis}_r']
    wins=r[r>0]; losses=r[r<0]; sl=a[a.reason.eq(1)]
    mean_win=float(wins.mean()) if len(wins) else None
    mean_loss=float(losses.mean()) if len(losses) else None
    d.update(winners=len(wins),losers=len(losses),flat=int(r.eq(0).sum()),
        mean_win_r=mean_win,mean_loss_r=mean_loss,
        realized_payoff_ratio=mean_win/-mean_loss if mean_win is not None and mean_loss is not None else None,
        planned_rr_mean=float(a.planned_rr.mean()),planned_rr_median=float(a.planned_rr.median()),
        risk_bp_median=float(a.risk_bp.median()),fee_r_mean=float(a.fee_r.mean()),
        tp=int(a.reason.eq(4).sum()),sl=len(sl),tp_net_losers=int((a.reason.eq(4)&r.le(0)).sum()),
        holding_hours_median=float(a.holding_hours.median()),holding_hours_p90=float(a.holding_hours.quantile(.9)),
        holding_hours_max=float(a.holding_hours.max()),mfe_r_median=float(a.mfe_r.median()),
        mae_r_median=float(a.mae_r.median()),ambiguous=int(a.ambiguous_bar.sum()),
        sl_after_positive_mfe=int(sl.mfe_r.gt(1e-10).sum()),
        sl_after_positive_mfe_prior_bar=int(sl.mfe_before_exit_bar_r.gt(1e-10).sum()),
        sl_after_positive_net_mfe=int(sl.mfe_net_taker_r.gt(1e-10).sum()) if basis=='net' else None,
        first_entry=str(a.entry_time.min()) if len(a) else None,last_entry=str(a.entry_time.max()) if len(a) else None)
    for threshold,label in [(0.5,'half'),(1.,'one'),(2.,'two')]:
        d[f'sl_after_{label}_r']=int(sl.mfe_r.ge(threshold).sum())
        d[f'sl_after_{label}_r_prior_bar']=int(sl.mfe_before_exit_bar_r.ge(threshold).sum())
    return d


def summarize(cfg):
    f=analysis.add_actual_week(pd.concat([pd.read_csv(p,float_precision='round_trip') for p in (EXP/'symbols').glob('*/trades.csv.gz')],ignore_index=True))
    f.to_csv(EXP/'all-trades.csv.gz',index=False)
    ss=f[f.population.eq('signal')].copy()
    ss.to_csv(EXP/'signal-trades.csv.gz',index=False)
    ss[ss.config_id.eq('body-n3')].to_csv(EXP/'body-n3-trades.csv',index=False)
    rows=[]; split_rows=[]; lookup={}
    for config,g0 in f.groupby('config_id'):
        n=int(g0.confirmations.iloc[0])
        for group,g in analysis.group_data(g0).items():
            if g.empty: continue
            basis='gross' if group in ('XAUUSD','GROUP1_GROSS') else 'net'
            for period in ('all','earlier','later'):
                x=g if period=='all' else g[g.segment.eq(int(period=='later')) & ~g.crosses_selection_cut]
                row=dict(config_id=config,confirmations=n,group=group,period=period,basis=basis,**detail(x,basis))
                rows.append(row);lookup[(config,group,period)]=(row,x)
            if config in ('body-n3','strict-n3'):
                for axis in ('side','year'):
                    values=g.side if axis=='side' else pd.to_datetime(g.entry_time,utc=True).dt.year
                    for value,x in g.groupby(values):
                        split_rows.append(dict(config_id=config,group=group,basis=basis,axis=axis,value=int(value),**detail(x,basis)))
    metrics=pd.DataFrame(rows);metrics.to_csv(EXP/'metrics.csv',index=False)
    pd.DataFrame(split_rows).to_csv(EXP/'year-direction.csv',index=False)
    selections=[]
    for group,e in metrics[metrics.period.eq('earlier') & metrics.config_id.str.startswith('body')].groupby('group'):
        e=e[e.closed.gt(0)].sort_values(['mean_r','confirmations'],ascending=[False,True])
        if e.empty: continue
        best=e.iloc[0];later,x=lookup[(best.config_id,group,'later')]
        baseline,_=lookup[('body-n3',group,'later')]
        selections.append(dict(group=group,basis=best.basis,confirmations=int(best.confirmations),
            earlier_n=int(best.closed),earlier_mean_r=float(best.mean_r),later=later,baseline_later=baseline,
            inference=analysis.bootstrap(analysis.strict_pairs(x,best.basis),best.basis,cfg['seed'])))
    tests=sorted([(i,s['inference']['p_raw']) for i,s in enumerate(selections) if 'p_raw' in s['inference']],key=lambda x:x[1])
    adjusted=0.
    for rank,(i,p) in enumerate(tests):
        adjusted=max(adjusted,min(1.,p*(len(tests)-rank)));selections[i]['inference']['holm_p']=adjusted
    done=[json.loads(p.read_text()) for p in (EXP/'symbols').glob('*/done.json')]
    summary=dict(symbols=len(done),configurations=6,signal_rows=len(ss),
        new_positions=sum(r['new_positions'] for r in done),reused_positions=sum(r['reused_positions'] for r in done),
        strict_subset_checks=sum(r['strict_subset_checks'] for r in done),selection=selections,
        baseline_comparison=[r for r in rows if r['period']=='all' and r['config_id'] in ('body-n3','strict-n3')],
        limitations=cfg['limitations'],production_eligible=False,training_eligible=False)
    dump(EXP/'summary.json',summary);dump(EXP/'coverage.json',done)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('selection','baseline_comparison','limitations')}),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--workers',type=int,default=3);ap.add_argument('--symbols',nargs='*');ap.add_argument('--summarize',action='store_true');args=ap.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:
        raise RuntimeError('Commit source before results')
    cfg=json.loads((EXP/'config.json').read_text())
    if args.summarize: summarize(cfg);return
    jobs,_=previous.old.jobs(cfg)
    if args.symbols: jobs=[j for j in jobs if j[0] in args.symbols]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(process,j,cfg) for j in jobs]):
            print(json.dumps(future.result()),flush=True)
    if not args.symbols: summarize(cfg)


if __name__=='__main__':main()

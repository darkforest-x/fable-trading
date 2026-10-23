"""Owner-authorized 50/50 Monday TP1 exits with one fee-breakeven toggle.

Frozen case3 body N3 entries and matched controls are reused. No new signals,
filters, stop distances or targets. Hold-stop partial outcomes are exact linear
compositions of existing native near/far fills. Before TP1 fills, the BE arm is
identical and reuses those results; only parents with a native cached TP1 fill
need new Nautilus execution. Each parent has two equal HEDGING legs. A native
near-leg fill protects its far sibling using actual-fill fee breakeven.

Old far exits bound retained execution windows only: a tightened stop cannot
extend that original path. Every bar inside those windows is retained; the old
outcome never selects admissions or decides a new exit. No per-bar PnL oracle.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import time
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT,dump,file_stamp
from yoyo.evaluation import monday_5m_wick_engulf as source
from yoyo.evaluation import monday_15m_volume_rejection as volume
from yoyo.evaluation import monday_5m_wick_gold_clock as clock
from yoyo.evaluation import monday_5m_wick_gold as quality

EXP=ROOT/'experiments/active/exp-monday-tp1-breakeven-20261024-v1'
PRIOR=ROOT/'experiments/active/exp-monday-near-far-targets-vectorbt-20261024-v1'
CODE=['yoyo/evaluation/monday_tp1_study.py','yoyo/layers/l3_backtest/nautilus_independent.py',
      'tests/evaluation/test_monday_tp1_breakeven.py','yoyo/evaluation/range_framework.py',
      'yoyo/evaluation/monday_5m_wick_engulf.py','yoyo/evaluation/monday_15m_volume_rejection.py',
      'yoyo/evaluation/monday_5m_wick_gold_clock.py','yoyo/evaluation/monday_5m_wick_gold.py']
META=['trade_id','pair_id','population','symbol','side','entry_i','entry','stop','risk',
      'week','bucket','segment','source_segment','pin_time','entry_time']


def finite(v):
    if isinstance(v,dict):return {k:finite(x) for k,x in v.items()}
    if isinstance(v,list):return [finite(x) for x in v]
    if isinstance(v,float) and not np.isfinite(v):return None
    return v


def paired(raw):
    a=raw[raw.config_id.eq('near-n3')].set_index('trade_id',drop=False)
    b=raw[raw.config_id.eq('far-n3')].set_index('trade_id',drop=False).loc[a.index]
    for c in META:assert a[c].equals(b[c]),c
    return a,b


def compose(a,b,config):
    """Use equal original-size legs; fees already charged on their own fills."""
    out=a[META].copy()
    out['config_id']=config
    for c in ['gross_r','net_r','fee_r','gross_bp','net_bp']:
        out[c]=.5*a[c]+.5*b[c]
    out['tp1_price']=a.target_price;out['tp2_price']=b.target_price
    out['tp1_filled']=a.reason.eq(4)
    out['tp1_exit_original_i']=a.exit_original_i
    out['tp1_exit_price']=a.exit_price
    out['tp1_time']=a.exit_interval_start
    out['runner_reason']=b.reason
    out['runner_exit_price']=b.exit_price
    out['exit_original_i']=b.exit_original_i
    out['exit_interval_start']=b.exit_interval_start
    out['exit_interval_end']=b.exit_interval_end
    out['closed']=a.closed & b.closed
    out['be_trigger_i']=-1;out['be_effective_i']=-1
    out['be_price']=np.nan;out['deferred_closes']=0
    out['engine_origin']='cached_native_equal_weight_legs'
    return out


def execute_needed(chunk,a,b,symbol,cfg):
    """New sibling-order semantics execute once; complete paths from cached bounds."""
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy,run_native
    prices=chunk[source.OHLC].to_numpy(float)
    decimals=max(2,min(9,int(np.ceil(-np.log10(prices.min())))+7));tick=10.**(-decimals)
    prices=np.round(prices,decimals)
    keep=np.zeros(len(chunk),bool)
    for i,j in zip(a.entry_i,b.exit_original_i):keep[int(i):int(j)+1]=True
    indices=np.flatnonzero(keep);inverse=np.full(len(chunk),-1,int);inverse[indices]=np.arange(len(indices))*2
    actual=prices[indices];expanded=np.empty((2*len(indices),4))
    expanded[::2]=np.repeat(actual[:,0,None],4,axis=1);expanded[1::2]=actual
    times=np.empty(len(expanded),np.int64)
    times[::2]=chunk.index.as_unit('ns').asi8[indices]+1
    times[1::2]=chunk.index.as_unit('ns').asi8[indices]+300_000_000_000
    frame=pd.DataFrame(expanded,columns=source.OHLC,index=pd.to_datetime(times,utc=True))
    prepared=[]
    for k,(tid,r) in enumerate(a.iterrows()):
        for leg,target in [('near',r.target_price),('far',b.loc[tid,'target_price'])]:
            prepared.append(dict(trade_id=f'p{k}-{leg}',parent_id=f'p{k}',source_id=tid,leg=leg,
                entry_i=int(inverse[int(r.entry_i)]),entry=float(r.entry),side=int(r.side),
                stop=float(r.stop),risk=float(r.risk),target_price=float(target),
                end_i=int(inverse[int(b.loc[tid,'exit_original_i'])]+1)))
    gold=symbol=='XAUUSD'
    rows,fills,audit=run_native(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),pd.DataFrame(prepared),
        ExitPolicy(target_from_entry=True,target_breakeven=True,opposite_hour_exit=False,
            round_trip_cost=0,fill_maker_fee=0 if gold else cfg['maker_fee'],
            fill_taker_fee=0 if gold else cfg['taker_fee']),tick=tick,symbol='MONDAY')
    rows['exit_original_i']=indices[(rows.exit_i//2).astype(int)]
    for name in ['be_trigger_i','be_effective_i']:
        rows[name]=[int(indices[int(v)//2]) if v>=0 else -1 for v in rows[name]]
    rows['exit_interval_start']=[str(chunk.index[i]) for i in rows.exit_original_i]
    rows['exit_interval_end']=[str(chunk.index[i]+pd.Timedelta(minutes=5)) for i in rows.exit_original_i]
    rows['closed']=rows.reason.ne(0);rows['gross_bp']=rows.gross_r*rows.risk/rows.entry*10000
    rows['net_bp']=rows.net_notional_bp
    near=rows[rows.leg.eq('near')].set_index('source_id').loc[a.index]
    far=rows[rows.leg.eq('far')].set_index('source_id').loc[a.index]
    # The partial near leg must reproduce the immutable native TP1 receipt.
    assert near.reason.eq(4).all()
    np.testing.assert_array_equal(near.exit_original_i,a.exit_original_i)
    np.testing.assert_allclose(near.exit_price,a.exit_price,rtol=1e-10,atol=tick*.25)
    np.testing.assert_allclose(near.gross_r,a.gross_r,rtol=1e-8,atol=1e-8)
    if not gold:np.testing.assert_allclose(near.net_r,a.net_r,rtol=1e-7,atol=1e-7)
    assert (far.exit_original_i<=b.exit_original_i).all()
    assert (far.be_effective_i.lt(0)|far.be_effective_i.ge(near.exit_original_i)).all()
    assert far[b.closed].closed.all()
    if gold:
        for c in ['net_r','net_bp','fee_r']:near[c]=np.nan;far[c]=np.nan
    # Preserve original source metadata; merge native outcomes by immutable ID.
    out=compose(a,b,'split_be')
    for c in ['gross_r','net_r','fee_r','gross_bp','net_bp']:out[c]=.5*near[c]+.5*far[c]
    for c in ['exit_original_i','exit_interval_start','exit_interval_end','closed',
              'be_trigger_i','be_effective_i','be_price','deferred_closes']:out[c]=far[c]
    out['runner_reason']=far.reason;out['runner_exit_price']=far.exit_price
    out['engine_origin']='nautilus_1.231.0_native_tp1_sibling_be'
    fills=fills.merge(rows[['trade_id','source_id','leg']],on='trade_id',validate='many_to_one')
    fills['original_bar_i']=indices[(fills.bar_i//2).astype(int)]
    fills['is_open_point']=fills.bar_i.mod(2).eq(0)
    audit.update(parents=len(a),tp1_cache_checks=len(a),retained_original_bars=len(indices),
                 original_source_bars=len(chunk),bounded_by_cached_far_exit=True)
    return out,rows,fills,audit


def process(job,cfg):
    symbol,paths=job;dest=EXP/'symbols'/symbol;dest.mkdir(parents=True,exist_ok=True)
    prior=PRIOR/'symbols'/symbol;ledger=prior/'trades.csv.gz'
    identity=dict(inputs=[file_stamp(p) for p in paths],config=cfg,
        prior=file_stamp(ledger) if ledger.exists() else None,code=[file_stamp(ROOT/p) for p in CODE],
        versions={p:version(p) for p in ['nautilus_trader','pandas','numpy']})
    assert identity['inputs']==json.loads((prior/'identity.json').read_text())['inputs']
    if (dest/'done.json').exists():
        assert json.loads((dest/'identity.json').read_text())==identity,'Changed cached study'
        return json.loads((dest/'done.json').read_text())
    started=time.monotonic();count=dict(parents=0,signals=0,new_native_parents=0,reused_without_tp1=0,native_fills=0)
    if ledger.exists():
        raw=pd.read_csv(ledger,float_precision='round_trip');a,b=paired(raw)
        if len(a):
            hold=compose(a,b,'split_hold');be=compose(a,b,'split_be')
            need=a.reason.eq(4);count.update(parents=len(a),signals=int(a.population.eq('signal').sum()),
                reused_without_tp1=int((~need).sum()))
            audits=[];native_rows=[];native_fills=[]
            if need.any():
                if symbol=='XAUUSD':
                    minutes,_=clock.read_minutes(paths);frame,_=quality.clean_minutes(minutes)
                else:frame=volume.load(paths);frame['quality_segment']=0
                for seg,chunk in frame.groupby('quality_segment',sort=True):
                    chosen=a[need & a.source_segment.eq(seg)]
                    if chosen.empty:continue
                    result,rr,ff,audit=execute_needed(chunk,chosen,b.loc[chosen.index],symbol,cfg)
                    be.loc[result.index,result.columns]=result
                    rr['source_segment']=seg;ff['source_segment']=seg
                    native_rows.append(rr);native_fills.append(ff);audits.append(audit)
                    count['new_native_parents']+=len(chosen);count['native_fills']+=len(ff)
            # References retain all original evidence but only these columns enter the comparison.
            refs=[]
            for name,x in [('near_full',a),('far_full',b)]:
                r=compose(x,x,name);r['runner_reason']=x.reason
                for c in ['tp1_price','tp1_filled','tp1_exit_original_i','tp1_exit_price','tp1_time']:
                    r[c]=hold[c]
                r['tp2_price']=hold.tp2_price
                refs.append(r)
            all_rows=pd.concat([*refs,hold,be],ignore_index=True)
            all_rows['holding_hours']=(pd.to_datetime(all_rows.exit_interval_start,utc=True)-pd.to_datetime(all_rows.entry_time,utc=True)).dt.total_seconds()/3600
            all_rows['crosses_selection_cut']=(pd.to_datetime(all_rows.entry_time,utc=True)<pd.Timestamp(cfg['split']))&(pd.to_datetime(all_rows.exit_interval_end,utc=True)>pd.Timestamp(cfg['split']))
            all_rows.to_csv(dest/'trades.csv.gz',index=False)
            if native_rows:pd.concat(native_rows,ignore_index=True).to_csv(dest/'native-legs.csv.gz',index=False)
            if native_fills:pd.concat(native_fills,ignore_index=True).to_csv(dest/'native-fills.csv.gz',index=False)
            dump(dest/'audit.json',audits)
    done=dict(symbol=symbol,seconds=time.monotonic()-started,**count)
    dump(dest/'identity.json',identity);dump(dest/'done.json',done)
    return done


def summarize(cfg):
    # Existing statistical utilities run in the isolated VectorBT environment;
    # this phase reads completed fills and never executes a second backtest.
    from yoyo.evaluation import monday_parameter_analysis as stats
    f=stats.add_actual_week(pd.concat([pd.read_csv(p,float_precision='round_trip') for p in (EXP/'symbols').glob('*/trades.csv.gz')],ignore_index=True))
    f.to_csv(EXP/'all-trades.csv.gz',index=False)
    ss=f[f.population.eq('signal')];ss.to_csv(EXP/'signal-trades.csv.gz',index=False)
    tables=[];changes=[];splits=[]
    for arm,g0 in f.groupby('config_id'):
        for group,g in stats.group_data(g0).items():
            basis='gross' if group in ['XAUUSD','GROUP1_GROSS'] else 'net'
            for period in ['all','earlier','later']:
                part=g if period=='all' else g[g.segment.eq(int(period=='later'))&~g.crosses_selection_cut]
                a=part[part.population.eq('signal')&part.closed];r=a[f'{basis}_r'];w=r[r>0];l=r[r<0]
                pairs=stats.strict_pairs(part,basis)
                row=dict(config_id=arm,group=group,period=period,basis=basis,signals=int(part.population.eq('signal').sum()),
                    closed=len(a),censored=int((part.population.eq('signal')&~part.closed).sum()),winners=len(w),
                    win_rate=float((r>0).mean()),mean_r=float(r.mean()),total_r=float(r.sum()),
                    mean_bp=float(a[f'{basis}_bp'].mean()),profit_factor=float(w.sum()/-l.sum()) if len(l) else None,
                    payoff_ratio=float(w.mean()/-l.mean()) if len(l) and len(w) else None,
                    mean_win_r=float(w.mean()),mean_loss_r=float(l.mean()),paired=len(pairs),
                    paired_signal_r=float(pairs[f'{basis}_r'].mean()) if len(pairs) else None,
                    paired_random_r=float(pairs[f'control_{basis}_r'].mean()) if len(pairs) else None,
                    tp1_filled=int(a.tp1_filled.sum()),runner_be_stops=int(a.runner_reason.eq(5).sum()),
                    runner_far_tp=int(a.runner_reason.eq(4).sum()),holding_hours_median=float(a.holding_hours.median()),
                    deferred_parents=int(a.deferred_closes.gt(0).sum()),first_entry=str(a.entry_time.min()),last_entry=str(a.entry_time.max()))
                tables.append(row)
            if arm in ['split_hold','split_be']:
                for year,a in g[g.population.eq('signal')&g.closed].groupby(pd.to_datetime(g[g.population.eq('signal')&g.closed].entry_time,utc=True).dt.year):
                    splits.append(dict(config_id=arm,group=group,year=int(year),n=len(a),mean_r=float(a[f'{basis}_r'].mean()),win_rate=float(a[f'{basis}_r'].gt(0).mean())))
    for group,g in stats.group_data(f).items():
        basis='gross' if group in ['XAUUSD','GROUP1_GROSS'] else 'net'
        for period in ['all','earlier','later']:
            # Both policies must lie on the same chronological side for deltas.
            part=g if period=='all' else g[g.segment.eq(int(period=='later'))&~g.crosses_selection_cut]
            a=part[part.config_id.eq('split_hold')].set_index('trade_id',drop=False)
            b=part[part.config_id.eq('split_be')].set_index('trade_id',drop=False)
            ids=a.index.intersection(b.index);a=a.loc[ids];b=b.loc[ids]
            both=a.closed & b.closed;sig=a.population.eq('signal')
            x=a[both&sig];y=b[both&sig];delta=y[f'{basis}_r']-x[f'{basis}_r']
            old=part[part.config_id.eq('far_full')].set_index('trade_id')
            saved=(y[f'{basis}_r']>x[f'{basis}_r']+1e-9)
            damaged=(y[f'{basis}_r']<x[f'{basis}_r']-1e-9)
            changes.append(dict(group=group,period=period,basis=basis,common_closed=len(x),
                mean_delta_r=float(delta.mean()),total_delta_r=float(delta.sum()),improved=int(saved.sum()),worsened=int(damaged.sum()),
                gain_on_improved=float(delta[saved].sum()),loss_on_worsened=float(delta[damaged].sum()),
                loss_to_win=int(((x[f'{basis}_r']<=0)&(y[f'{basis}_r']>0)).sum()),
                win_to_loss=int(((x[f'{basis}_r']>0)&(y[f'{basis}_r']<=0)).sum()),
                original_far_tp_cut=int((old.loc[x.index].runner_reason.eq(4)&y.runner_reason.eq(5)).sum()),
                original_far_sl_saved=int((old.loc[x.index].runner_reason.eq(1)&y.runner_reason.eq(5)).sum()),
                unresolved_resolved=int((sig&~a.closed&b.closed).sum()),
                inference=stats.bootstrap(stats.strict_pairs(part[part.config_id.eq('split_be')],basis),basis,cfg['seed'])))
    pd.DataFrame(tables).to_csv(EXP/'metrics.csv',index=False)
    pd.DataFrame(splits).to_csv(EXP/'yearly.csv',index=False)
    pd.DataFrame([{k:v for k,v in c.items() if k!='inference'} for c in changes]).to_csv(EXP/'be-changes.csv',index=False)
    done=[json.loads(p.read_text()) for p in (EXP/'symbols').glob('*/done.json')]
    summary=dict(symbols=len(done),parents=sum(x['parents'] for x in done),signal_entries=sum(x['signals'] for x in done),
        new_native_parents=sum(x['new_native_parents'] for x in done),native_fills=sum(x['native_fills'] for x in done),
        all_period=[x for x in tables if x['period']=='all'],later=[x for x in tables if x['period']=='later'],
        changes=changes,limitations=cfg['limitations'],training_eligible=False,production_eligible=False)
    dump(EXP/'summary.json',finite(summary));dump(EXP/'coverage.json',done)
    print(json.dumps({k:v for k,v in summary.items() if not isinstance(v,list)}),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--workers',type=int,default=3)
    ap.add_argument('--symbols',nargs='*');ap.add_argument('--summarize',action='store_true');args=ap.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:raise RuntimeError('Commit builder first')
    cfg=json.loads((EXP/'config.json').read_text())
    if args.summarize:summarize(cfg);return
    jobs,_=source.jobs(cfg)
    if args.symbols:jobs=[j for j in jobs if j[0] in args.symbols]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for fut in as_completed([pool.submit(process,j,cfg) for j in jobs]):print(json.dumps(fut.result()),flush=True)


if __name__=='__main__':main()

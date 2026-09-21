"""Frozen 5R follow-through experiments over every original V9 long request.

Only new research files are written. Native cached OHLC/ATR and close
SMA/EMA20/60/120 feed decisions; future bars are outcomes only. Delays retain
the ORIGINAL signal-defined stop price, reprice actual next-open initial R,
and cancel on a completed intervening stop breach, gap or raw reverse signal.
Exit formulas and 20bp costs stay frozen. Each path is independent, never a
compound or simultaneous portfolio. Two additions reuse the existing 100U,
1U gross risk, 1x cash-backed structural policy with its zero-add comparator.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from functools import lru_cache
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_10r_search as search
from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v9_full_replay import prepare_v9
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.winner_pyramiding import replay_path
from yoyo.evaluation.spike_5r_context_features import build_context_features

EXP = Path('experiments/active/exp-spike-5r-followthrough-20260921-v6')
DATASET = search.EXP/'dataset_v1'
SOURCE = Path('experiments/active/exp-spike-v9-full-backtest-20260915-v1/results/full_v1/streams')
ARMS = ('original', 'wait1', 'confirm1', 'wait2', 'confirm2', 'retest2', 'structure0', 'structure2')
LAGS = dict(original=0,wait1=1,confirm1=1,wait2=2,confirm2=2,retest2=2,structure0=0,structure2=0)
MAS = ['s20','e20','s60','e60','s120','e120']
ECON = ['entry_time','exit_time','entry_price','initial_stop','initial_risk','gross_return','net_return','gross_r','net_r','censored','exit_reason']
META = ['event_key','stream_key','venue','symbol','asset','timeframe_min','available_at','signal_i','local_i']
DEPS = [Path(__file__),Path('yoyo/evaluation/spike_5r_context_features.py'),
        Path('tests/evaluation/test_spike_5r_followthrough.py'),Path('tests/evaluation/test_spike_5r_context_features.py'),
        EXP/'config.json',EXP/'PROJECT_PLAN.md']


def path_bounds(frame, row):
    """OHLC bounds on fee-adjusted peak before actual original exit.

Survived bars before exit contribute highs. An opening/reverse exit contributes
only its opening fill, never the remainder of that candle. For an intrabar
stop, exit-candle high is an UPPER bound: stop/high order is unknown. Censored
paths remain unknown, even if a prefix touched a threshold.
"""
    if not bool(row['valid_entry']) or bool(row['censored']):
        return dict(path_group='unknown',net_peak_lower=np.nan,net_peak_upper=np.nan)
    start = int(frame.index.get_loc(pd.Timestamp(row['entry_time'])))
    end = int(frame.index.get_loc(pd.Timestamp(row['exit_time'])))
    prior = frame.iloc[start:end]
    entry,risk = float(row['entry_price']),float(row['initial_risk'])
    known = max(entry,float(row['exit_price']),float(frame.open.iloc[end]),float(prior.high.max()) if len(prior) else entry)
    upper = known
    reason = str(row['exit_reason'])
    if 'stop' in reason and not reason.endswith('_gap'):
        upper = max(upper,float(frame.high.iloc[end]))
    lo,hi = ((p-entry-.002*entry)/risk for p in (known,upper))
    group = ('realized_gt5' if float(row['net_r'])>5 else 'gaveback_confirmed' if lo>5
             else 'exit_bar_ambiguous' if hi>5 else 'never_observed_gt5')
    return dict(path_group=group,net_peak_lower=lo,net_peak_upper=hi)


def confirmation_masks(p):
    """Read signal six-MA ceiling and 1/2 subsequent completed native bars.

These masks are known at signal+lag close, not at the original signal close.
Waiting-only comparators isolate delay from added pattern confirmation.
"""
    c=pd.Series(p.close);h=pd.Series(p.high);lo=pd.Series(p.low)
    ceiling=p.frame[MAS].max(axis=1,skipna=False).reset_index(drop=True)
    one=(c.shift(-1)>=c)&(c.shift(-1)>=ceiling)
    two=(c.shift(-1)>=ceiling)&(c.shift(-2)>=ceiling)&(c.shift(-2)>=c)
    retest=(lo.shift(-1)<=ceiling)&(c.shift(-1)>=ceiling)&(c.shift(-2)>h.shift(-1))&(c.shift(-2)>=c)
    yes=np.ones(len(c),dtype=bool)
    return {a:(one if a=='confirm1' else two if a=='confirm2' else retest if a=='retest2' else yes).to_numpy(bool)
            if a in ('confirm1','confirm2','retest2') else yes.copy() for a in ARMS}


def initial_at(p, i, lag):
    """Original closed-five-bar stop, later fill; no new stop multiplier."""
    j=i+lag+1
    if j>=len(p.frame) or p.gap[i+1:j+1].any():
        return None
    initial=base._initial_position_fast(p.frame.index,p.open,p.high,p.low,p.close,p.atr,p.gap,i,1,p.spec)
    if initial is None:
        return None
    stop=float(initial['initial_stop'])
    if lag and (np.any(p.low[i+1:j]<=stop) or np.any(p.raw_side[i+1:j]==-1)):
        return None
    entry=float(p.open[j]);risk=entry-stop
    if not np.isfinite(entry) or risk<=0 or not (base.START<=p.frame.index[j]<base.END):
        return None
    initial.update(entry_i=j,entry_time=p.frame.index[j],entry_price=entry,initial_risk=risk,initial_risk_frac=risk/entry)
    return initial


def outcome(p, i, arm, masks):
    if not masks[arm][i]:
        return None
    initial=initial_at(p,i,LAGS[arm])
    if initial is None:
        return None
    if arm in ('structure0','structure2'):
        result,_=replay_path(p.frame,p.raw_side==-1,p.gap,int(initial['entry_i']),float(initial['initial_stop']),p.spec.tick,
            0 if arm=='structure0' else 2,capital=100.,gross_risk_budget=1.,leverage_cap=1.,bar_minutes=p.context.minutes,events=False)
        return result
    return engine.replay_fixed_entry(p.context,pd.Series(initial),arm='v8',enable_be=False,prepared=p)


@lru_cache(maxsize=9)
def btc_source(key, sha):
    if key is None:
        return None
    folder=base.SOURCE_STREAMS/key
    if search.digest(folder/'control_cache.pkl.gz')!=sha:
        raise ValueError('BTC source cache drift')
    return base.load_verified_stream(folder).cache['bars']


def pick_control(options, token):
    if not len(options):
        return None
    import hashlib
    return int(options[int(hashlib.sha256(token.encode()).hexdigest(),16)%len(options)])


def one(args):
    key,rows,benchmark,outdir,identity=args
    folder=Path(outdir)/'streams'/key
    if (folder/'completion.json').exists():
        receipt=json.loads((folder/'completion.json').read_text())
        if receipt['identity']!=identity or any(search.digest(folder/n)!=s for n,s in receipt['files'].items()):
            raise ValueError('resumed stream differs')
        return receipt
    ds=json.loads((DATASET/'streams'/key/'completion.json').read_text())
    if search.digest(SOURCE/key/'completion.json')!=ds['source_completion_sha256']:
        raise ValueError('source completion drift')
    src=json.loads((SOURCE/key/'completion.json').read_text());raw=base.SOURCE_STREAMS/key
    if search.digest(raw/'completion.json')!=src['raw_completion_sha256'] or search.digest(raw/'control_cache.pkl.gz')!=src['cache_sha256']:
        raise ValueError('raw source drift')
    context=base.load_verified_stream(raw);p,_=prepare_v9(engine.prepare_arm(context,arm='v8'))
    indices={int(r['local_i']) for r in rows}
    if indices!=set(np.flatnonzero(p.allowed & (p.raw_side==1))):
        raise ValueError('original candidate membership drift')
    btc=btc_source(benchmark['key'],benchmark['sha256']) if benchmark else None
    features=build_context_features(p.frame,context.minutes,btc_frame=btc,btc_minutes=context.minutes if benchmark else None)
    masks=confirmation_masks(p);n=len(p.frame);delta=pd.Timedelta(minutes=context.minutes)
    ready=p.frame.ready.fillna(False).to_numpy(bool)&~p.gap&np.isfinite(p.atr)&(p.atr>0)
    ready &= ~(p.allowed & (p.raw_side==1))
    bins=np.searchsorted([.005,.01,.02,.05,.1],p.atr/p.close,side='left')
    pools={};cache={};paths=[];controls=[];fact_rows=[];bounds=[]
    # Pool membership uses close-known pattern/market facts; never outcomes.
    for arm in ARMS:
        lag=LAGS[arm];times=p.frame.index+delta*(lag+1)
        months=np.asarray(times.strftime('%Y-%m'));fold=(times>=base.SPLIT).astype(int)
        ok=ready&masks[arm]&(times>=base.START)&(times<base.END)&(np.arange(n)+lag+1<n)
        for bucket in set(zip(months[ok],bins[ok],fold[ok])):
            m,b,f=bucket;pools[(arm,m,int(b),int(f))]=np.flatnonzero(ok&(months==m)&(bins==b)&(fold==f))
    for row in rows:
        meta={k:row[k] for k in META};i=int(row['local_i'])
        fact_rows.append({**meta,**features.iloc[i].to_dict()})
        bounds.append({**meta,**path_bounds(p.frame,row)})
        for arm in ARMS:
            result={k:row.get(k,np.nan) for k in ECON} if arm=='original' else outcome(p,i,arm,masks)
            valid=bool(row['valid_entry']) if arm=='original' else result is not None
            item={**meta,'arm':arm,'decision_at':p.frame.index[i]+delta*(LAGS[arm]+1),'valid_entry':valid,
                  'censored':True,'net_r':np.nan,'net_return':np.nan,'gross_r':np.nan,'gross_return':np.nan,
                  'entry_time':pd.NaT,'exit_time':pd.NaT,'reject_reason':'' if valid else 'confirmation_or_wait_invalid'}
            if result is not None:item.update({k:result.get(k,np.nan) for k in ECON+['adds_count','net_usd','initial_risk_usd']})
            paths.append(item)
            control={**meta,'arm':arm,'matched':False,'control_censored':True,'control_entry_time':pd.NaT,'control_exit_time':pd.NaT,
                     'control_net_r':np.nan,'control_net_return':np.nan,'control_gross_return':np.nan,'control_i':np.nan}
            if valid:
                time=item['decision_at'];options=pools.get((arm,time.strftime('%Y-%m'),int(bins[i]),int(time>=base.SPLIT)),np.array([],int))
                options=options[options!=i]
                chosen=pick_control(options,f'921621|{key}|{row["event_key"]}|{arm}')
                if chosen is not None:
                    ck=(arm,chosen)
                    if ck not in cache:cache[ck]=outcome(p,chosen,arm,masks)
                    value=cache[ck]
                    control['control_i']=chosen
                    if value is not None:
                        control.update(matched=True,control_censored=bool(value['censored']),control_entry_time=value['entry_time'],
                            control_exit_time=value['exit_time'],control_net_r=value['net_r'],control_net_return=value['net_return'],control_gross_return=value['gross_return'])
            controls.append(control)
    if search.digest(raw/'control_cache.pkl.gz')!=src['cache_sha256']:
        raise ValueError('cache changed during replay')
    folder.mkdir(parents=True,exist_ok=True)
    for name,data in [('features',fact_rows),('bounds',bounds),('paths',paths),('controls',controls)]:
        pd.DataFrame(data).to_csv(folder/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    receipt=dict(key=key,identity=identity,candidates=len(rows),source_completion_sha256=ds['source_completion_sha256'],
        cache_sha256=src['cache_sha256'],benchmark=benchmark,files={p.name:search.digest(p) for p in folder.glob('*.csv.gz')})
    search.dump(folder/'completion.json',receipt)
    return receipt


def run(output,workers=4):
    if not _committed(DEPS):raise ValueError('commit builder, features, tests and plan before run')
    candidates,_,receipt=search.load_candidates(DATASET)
    config=json.loads((EXP/'config.json').read_text())
    if search.digest(DATASET/'receipt.json')!=config['source_receipt_sha256']:raise ValueError('source receipt drift')
    srcmanifest=json.loads((DATASET/'manifest.json').read_text())
    benchmarks={}
    for key in srcmanifest['stream_completion_sha256']:
        with (base.SOURCE_STREAMS/key/'receipt.csv').open() as f:row=next(csv.DictReader(f))
        if row['asset']=='BTC':
            sha=json.loads((base.SOURCE_STREAMS/key/'completion.json').read_text())['cache_sha256']
            k=(row['venue'],int(row['minutes']))
            if k in benchmarks:raise ValueError('ambiguous same-venue BTC source')
            benchmarks[k]=dict(key=key,sha256=sha)
    deps={str(p):search.digest(p) for p in DEPS}
    for p in [Path(engine.__file__),Path(base.__file__),Path('yoyo/evaluation/winner_pyramiding.py')]:deps[str(p)]=search.digest(p)
    import hashlib
    identity=hashlib.sha256(json.dumps(deps,sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True,exist_ok=True)
    tasks=[(key,g.to_dict('records'),benchmarks.get((g.venue.iloc[0],int(g.timeframe_min.iloc[0]))),str(output),identity) for key,g in candidates.groupby('stream_key')]
    receipts=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(one,t):t[0] for t in tasks}
        for future in as_completed(futures):
            receipts.append(future.result())
            if len(receipts)%100==0:print(f'completed {len(receipts)}/{len(tasks)} streams',flush=True)
    for name in ['features','bounds','paths','controls']:
        frames=[pd.read_csv(output/'streams'/r['key']/f'{name}.csv.gz') for r in sorted(receipts,key=lambda x:x['key'])]
        pd.concat(frames,ignore_index=True).to_csv(output/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    total=sum(r['candidates'] for r in receipts)
    assert total==49207
    manifest=dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),dependencies=deps,
        identity=identity,candidates=total,streams=len(receipts),source_streams=3531,zero_candidate_streams=3531-len(receipts),
        benchmark_sources={str(k):v for k,v in benchmarks.items()},source_receipt_sha256=search.digest(DATASET/'receipt.json'),
        completed=True,receipts={r['key']:search.digest(output/'streams'/r['key']/'completion.json') for r in receipts},
        files={p.name:search.digest(p) for p in output.glob('*.csv.gz')},training_eligible=False,production_eligible=False)
    search.dump(output/'manifest.json',manifest)
    print(json.dumps(dict(candidates=total,streams=len(receipts),completed=True)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();run(args.output,args.workers)

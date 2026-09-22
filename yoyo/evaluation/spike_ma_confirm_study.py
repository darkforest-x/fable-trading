"""Owner-authorized MA close/body confirmation and fixed gross-2R ablation.

Features use only completed H1 SMA60 at each 15m OPEN, chart SMA120 from the
last 120 closed closes, and signal ATR14. Future bars score exits only.
This reuses the authenticated 29-symbol V9/H1 admission pool, not native
V12 bk geometry. Structural R is a reference distance, never a hard loss cap.
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess, time
from pathlib import Path
from dataclasses import replace
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_ma_stop_study as prior
from yoyo.evaluation import spike_ma_confirm as kernel
from yoyo.evaluation import spike_v10_4_study as source
from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation import spike_v1_v8_be05 as legacy
from yoyo.evaluation.spike_v9_htf_sma import side_gate
from yoyo.evaluation.spike_v9_htf_sma_study import build_prepared
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-ma-confirm-20260922-v1')
PARENT = prior.EXP/'run_v1'
STOP_MODES = ('original', 'htf_touch', 'htf_close', 'htf_body')
PROFIT_MODES = ('trail', 'tp2')
ARMS = tuple(f'{s}__{p}' for p in PROFIT_MODES for s in STOP_MODES)
BASELINE = 'original__trail'
# Each edge changes either a stop rule or the profit rule, never both.
COMPARATORS = {'htf_touch__trail':BASELINE, 'htf_close__trail':'htf_touch__trail',
    'htf_body__trail':'htf_close__trail', 'original__tp2':BASELINE,
    'htf_touch__tp2':'htf_touch__trail', 'htf_close__tp2':'htf_close__trail',
    'htf_body__tp2':'htf_body__trail'}
SEED, REPS = 92226, 10000


def transform(p, feature, arm):
    """Freeze entry reference risk; body exits later use the visible H1 line."""
    return prior.transformer(p, feature, 'baseline' if arm.startswith('original__') else 'htf_sma60')


def score(p, feature, i, side, arm):
    row=prior.initial(p,i,side)
    if row is None: return None
    row=transform(p,feature,arm)(row,i,side)
    if row is None: return None
    return kernel.replay_fixed(p,pd.Series(row),policy=arm,higher=feature.sma_60.to_numpy(float))


def controls(p, feature, trades):
    """Shared event draw, same causal strata and policy; never redraw outcomes."""
    f=p.frame;clock=f.index+pd.Timedelta(minutes=15)
    months=np.asarray(clock.strftime('%Y-%m'));folds=np.where(clock<source.SPLIT,'earlier','later')
    bins=np.searchsorted(source.VOL_BINS,p.atr/p.close,side='left')
    finite=np.isfinite(feature[['sma120','sma_60']]).all(axis=1).to_numpy()
    ready=(f.ready.fillna(False).to_numpy(bool)&finite&source.in_window(f.index,15)
           &np.isfinite(p.atr)&(p.atr>0)&(clock.dayofweek!=6)&~p.gap)
    pools,draws,cache,rows={},{},{},[]
    for t in trades.itertuples(index=False):
        i,side=int(t.signal_i),int(t.side);strata=(months[i],folds[i],int(bins[i]),side)
        if strata not in pools:
            same=ready&(months==strata[0])&(folds==strata[1])&(bins==strata[2])
            same &= side_gate(p.close,np.full(len(f),side),feature.sma_60)
            pools[strata]=np.flatnonzero(same)
        if t.event_key not in draws:
            options=pools[strata][pools[strata]!=i]
            token=int(hashlib.sha256(f'{SEED}|{t.event_key}'.encode()).hexdigest(),16)
            draws[t.event_key]=int(options[token%len(options)]) if len(options) else None
        j=draws[t.event_key];result=original=None
        if j is not None:
            key=(j,side,t.arm)
            if key not in cache: cache[key]=score(p,feature,j,side,t.arm)
            result=cache[key];original=prior.initial(p,j,side)
        matched=result is not None and not result['censored'] and not t.censored
        rows.append({'event_key':t.event_key,'arm':t.arm,'matched':matched,
          'reason':'matched' if matched else 'target_censored' if t.censored else 'no_pool' if j is None else 'invalid_or_censored',
          'control_signal_i':j,'control_signal_time':None if j is None else f.index[j],
          'control_exit_time':None if result is None else result['exit_time'],
          'control_net_r':result['net_r'] if matched else np.nan,
          'control_net_return':result['net_return'] if matched else np.nan,
          'control_net_original_r':result['net_return']/original['initial_risk_frac'] if matched else np.nan})
    return pd.DataFrame(rows,columns=['event_key','arm','matched','reason','control_signal_i','control_signal_time',
        'control_exit_time','control_net_r','control_net_return','control_net_original_r'])


def run_one(args):
    symbol,info,output,identity_hash=args
    started=time.monotonic();folder=Path(output)/'streams'/symbol
    if (folder/'completion.json').exists():
        receipt=json.loads((folder/'completion.json').read_text());assert receipt['identity_hash']==identity_hash
        for n,h in receipt['files'].items(): assert source.digest(folder/n)==h
        return receipt
    path=Path(info['path']);assert source.digest(path)==info['sha256']
    base=inc.guarded_5m(path,source.START-pd.Timedelta(days=source.WARMUP_BARS))
    facts=source.v9_facts(v11.bars_for(base,15),15,info['meta']['asset'],info['meta']['tick'])
    p=build_prepared(facts,symbol,info['meta'],'15m',15);feature=prior.features(base,p)
    p=replace(p,allowed=p.allowed&side_gate(p.close,p.raw_side,feature.sma_60))
    parts,fillparts,eventparts,rejected=[],[],[],[]
    for arm in ARMS:
        apply=transform(p,feature,arm)
        def guarded(row,i,side):
            changed=apply(row,i,side)
            if changed is None: rejected.append({'arm':arm,'symbol':symbol,'signal_i':i,'side':side,'reason':'invalid_ma_stop'})
            return changed
        t,f,e=kernel.replay_serial(p,policy=arm,higher=feature.sma_60.to_numpy(float),initial_transform=guarded)
        t=prior.annotate(t,p,feature,arm);parts.append(t)
        f['arm']=arm;fillparts.append(f)
        e['arm']=arm;eventparts.append(e)
    trades=pd.concat(parts,ignore_index=True)
    parent=pd.read_csv(PARENT/'streams'/symbol/'trades.csv.gz')
    for arm,oldarm in ((BASELINE,'baseline'),('htf_touch__trail','htf_sma60')):
        a=trades.loc[trades.arm.eq(arm)];b=parent.loc[parent.arm.eq(oldarm)]
        pd.testing.assert_frame_equal(a[legacy.KEY+['censored']].reset_index(drop=True),
            b[legacy.KEY+['censored']].reset_index(drop=True),check_dtype=False,rtol=1e-9,atol=1e-9)
    original=trades.loc[trades.arm.eq(BASELINE)];fixed=[]
    for t in original.itertuples(index=False):
        for arm in ARMS:
            result=score(p,feature,int(t.signal_i),int(t.side),arm)
            item={'event_key':t.event_key,'arm':arm,'symbol':symbol,'side':t.side,'signal_i':t.signal_i,
                'signal_bar_open':t.signal_bar_open,'baseline_net_r':t.net_r,'baseline_net_return':t.net_return,
                'baseline_censored':t.censored,'baseline_risk_frac':t.initial_risk_frac,
                'status':'invalid_ma_stop' if result is None else 'censored' if result['censored'] else 'closed'}
            if result is not None:
                item.update(result);item['net_original_r']=result['net_return']/t.initial_risk_frac
                item['position_multiplier']=t.initial_risk_frac/result['initial_risk_frac']
                if arm==BASELINE:
                    for k in ('exit_i','exit_reason','censored'): assert result[k]==getattr(t,k)
                    np.testing.assert_allclose(result['net_r'],t.net_r,atol=1e-10)
            fixed.append(item)
    tables={'trades':trades,'fills':pd.concat(fillparts,ignore_index=True),
      'events':pd.concat(eventparts,ignore_index=True),'fixed':pd.DataFrame(fixed) if fixed else pd.DataFrame(columns=['event_key','arm','symbol','side','signal_i','signal_bar_open','baseline_net_r','baseline_net_return','baseline_censored','baseline_risk_frac','status']),
      'controls':controls(p,feature,trades),
      'rejected':pd.DataFrame(rejected,columns=['arm','symbol','signal_i','side','reason'])}
    folder.mkdir(parents=True,exist_ok=True)
    for name,t in tables.items(): t.to_csv(folder/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    assert source.digest(path)==info['sha256']
    receipt={'symbol':symbol,'identity_hash':identity_hash,'input_sha256':info['sha256'],
      'baseline_parity':True,'previous_htf_touch_parity':True,'baseline_fixed_parity':True,
      'baseline_trades':len(original),'chart_bars':len(p.frame),'candidates':int(p.allowed.sum()),
      'rejected':len(rejected),'files':{f'{n}.csv.gz':source.digest(folder/f'{n}.csv.gz') for n in tables},
      'seconds':round(time.monotonic()-started,2)}
    (folder/'completion.json').write_text(json.dumps(receipt,indent=2)+'\n');return receipt


def run(output,workers=3,symbols=None):
    spec=source.ExecutionSpec()
    assert (spec.stop_bars,spec.stop_buffer_atr,spec.risk_floor_atr,spec.arm_r,spec.trail_atr,spec.round_trip_cost)==(5,.2,2.,2.,4.,.002)
    old=json.loads((PARENT/'identity.json').read_text());done=json.loads((PARENT/'completion.json').read_text())
    assert hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest()==done['identity_hash']
    keys=sorted(symbols or old['inputs']);assert set(keys)<=set(old['inputs'])
    parents={}
    for s in keys:
        rp=PARENT/'streams'/s/'completion.json';receipt=json.loads(rp.read_text())
        assert source.digest(rp)==done['receipts'][s]
        assert receipt['identity_hash']==done['identity_hash'] and receipt['input_sha256']==old['inputs'][s]['sha256']
        assert source.digest(rp.parent/'trades.csv.gz')==receipt['files']['trades.csv.gz']
        parents[s]=source.digest(rp)
    code=_local_transitive_python((Path(__file__),Path('yoyo/evaluation/spike_ma_confirm.py')))
    declared=(*code,Path('yoyo/evaluation/spike_ma_confirm_report.py'),EXP/'PROJECT_PLAN.md',
              Path('tests/evaluation/test_spike_ma_confirm.py'),Path('tests/evaluation/test_spike_ma_confirm_study.py'))
    assert _committed(declared),'Commit executable closure, tests and plan before replay'
    identity={'schema':'spike-ma-confirm-v1','source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
      'arms':list(ARMS),'comparators':COMPARATORS,'timeframe':'15m','start':str(source.START),'split':str(source.SPLIT),
      'end':str(inc.DATA_END),'inputs':{s:old['inputs'][s] for s in keys},'prior_identity_sha256':source.digest(PARENT/'identity.json'),
      'prior_receipts':parents,'metadata_sha256':source.digest(source.EXCHANGE_INFO),
      'declared':{str(p):source.digest(p) for p in declared},'seed':SEED,'reps':REPS,
      'training_eligible':False,'production_eligible':False}
    # Parent binds the same archived metadata via its prior identity.
    grand_path=prior.PRIOR/'identity.json';assert source.digest(grand_path)==old['prior_identity_sha256']
    grand=json.loads(grand_path.read_text());assert identity['metadata_sha256']==grand['metadata_sha256']
    identity_hash=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    output=Path(output);output.mkdir(parents=True,exist_ok=True);ip=output/'identity.json'
    if ip.exists(): assert json.loads(ip.read_text())==identity,'Use new output after source changes'
    else: ip.write_text(json.dumps(identity,indent=2)+'\n')
    receipts=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs=[pool.submit(run_one,(s,identity['inputs'][s],str(output),identity_hash)) for s in keys]
        for job in as_completed(jobs):
            r=job.result();receipts.append(r)
            print(json.dumps({'completed':len(receipts),'total':len(keys),'symbol':r['symbol'],
                'baseline':r['baseline_trades'],'seconds':r['seconds']}),flush=True)
    for name in ('trades','fixed','controls','rejected'):
        t=pd.concat([pd.read_csv(output/'streams'/s/f'{name}.csv.gz') for s in keys],ignore_index=True)
        t.to_csv(output/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    final={'identity_hash':identity_hash,'symbols':keys,'complete':True,
      'receipts':{s:source.digest(output/'streams'/s/'completion.json') for s in keys},
      'files':{f'{n}.csv.gz':source.digest(output/f'{n}.csv.gz') for n in ('trades','fixed','controls','rejected')}}
    (output/'completion.json').write_text(json.dumps(final,indent=2)+'\n')

if __name__=='__main__':
    cli=argparse.ArgumentParser();cli.add_argument('--output',type=Path,default=EXP/'run_v1')
    cli.add_argument('--workers',type=int,default=3);cli.add_argument('--symbols',nargs='+')
    a=cli.parse_args();run(a.output,a.workers,a.symbols)

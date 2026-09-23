"""Replay current SPIKE raw long/short entries with frozen morphology gates.

See exp-spike-ma-launch-gate-20260923-v1/PROJECT_PLAN.md. Only entry eligibility
changes; all arms use the published two-sided serial engine and unfiltered
opposite exits. Features read confirmed chart bars through each decision.
There is no model training, price fetching, future winner filter or live write.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v126_htf_recheck as v126
from yoyo.evaluation import spike_v9_htf_sma_study as shared
from yoyo.evaluation import ma_dense_launch_v1_reference as rules
from yoyo.evaluation.spike_ma_launch_gate import evaluate_gate
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python

EXP=Path('experiments/active/exp-spike-ma-launch-gate-20260923-v1')
CONFIG=EXP/'config.json'
ARMS=('baseline','ma_density_only','ma_hard','ma_grade_a')
TABLES=('decisions','trades','controls','fills')
source=shared.source


def dump(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')


def execute_arm(prepared, mask, arm):
    """Do not filter an existing ledger or change raw reversal signals."""
    p=replace(prepared,allowed=np.asarray(mask,bool))
    trades,fills,_=shared.engine.replay_serial(p.context,arm='v8',enable_be=False,prepared=p)
    for table in (trades,fills):
        table['arm']=arm
    trades['event_key']=[f'{p.context.key}|{pd.Timestamp(t).isoformat()}|{s}'
                         for t,s in zip(trades.signal_bar_open,trades.side)]
    trades['timeframe']='15m'
    if len(trades):
        closed=trades.loc[~trades.censored]
        np.testing.assert_allclose(closed.gross_return-closed.net_return,.002,atol=1e-12)
    return trades,fills


def run_stream(base,symbol,meta,cfg,pack):
    """Recompute current raw SPIKE facts once; replay four independent paths."""
    start=pd.Timestamp(cfg['start']); minutes=cfg['minutes']
    bars,partial=v126.complete_bars(base.loc[base.index>=start-pd.Timedelta(minutes=cfg['chart_warmup_bars']*minutes)],minutes)
    facts=v126.pine_facts(bars,base,meta['asset'],float(meta['tick']))
    p=shared.build_prepared(facts,symbol,meta,'15m',minutes)
    f=p.frame
    ma=rules.add_features(bars)
    if not ma.index.equals(f.index):
        raise ValueError('SPIKE/MA feature clocks differ')
    candidates=np.flatnonzero(p.allowed)
    masks={arm:np.zeros(len(f),bool) for arm in ARMS}
    masks['baseline']=p.allowed.copy()
    evidence=[]
    for i in candidates:
        result=evaluate_gate(ma,int(i),int(p.raw_side[i]),pack,minutes)
        for arm in ARMS[1:]:
            masks[arm][i]=result[arm]
        cores=result.pop('cores')
        evidence.append({'symbol':symbol,'signal_i':int(i),'side':int(p.raw_side[i]),
                         'signal_bar_open':f.index[i],**result,
                         'cores_json':json.dumps(cores,default=lambda v:v.item() if isinstance(v,np.generic) else str(v))})
    assert not np.any(masks['ma_grade_a'] & ~masks['ma_hard'])
    trades,fills=[],[]
    for arm in ARMS:
        t,fl=execute_arm(p,masks[arm],arm)
        t['symbol']=symbol;trades.append(t);fills.append(fl)
    t=pd.concat(trades,ignore_index=True)
    controls=shared.controls(p,t,cfg)
    decisions=pd.DataFrame(evidence,columns=['symbol','signal_i','side','signal_bar_open','ma_known','ma_density_only','ma_hard','ma_grade_a','selected_core_bars','stage1_distance','quality_score','reason','decision_close','cores_json'])
    summary={'bars':len(f),'partial_buckets_excluded':partial,'candidates':len(candidates),
             'passed':{arm:int(mask.sum()) for arm,mask in masks.items()},
             'entries':{arm:int((t.arm==arm).sum()) for arm in ARMS},
             'unknown_ma':int((~decisions.ma_known.astype(bool)).sum()) if len(decisions) else 0}
    return {'decisions':decisions,'trades':t,'fills':pd.concat(fills,ignore_index=True),'controls':controls},summary


def validate_receipt(directory,run_hash,input_sha):
    r=json.loads((directory/'receipt.json').read_text())
    if r['run_identity']!=run_hash or r['input_sha256']!=input_sha:
        raise ValueError('receipt identity drift')
    for name,expected in r['files'].items():
        if source.digest(directory/name)!=expected:
            raise ValueError('receipt artifact drift: '+name)
    return r


def worker(args):
    symbol,path,meta,out,run_hash,input_sha,cfg=args
    directory=Path(out)/'streams'/symbol
    if (directory/'receipt.json').exists():
        return validate_receipt(directory,run_hash,input_sha)
    if directory.exists():
        raise ValueError('incomplete output '+str(directory))
    before=time.perf_counter()
    if source.digest(Path(path))!=input_sha:
        raise ValueError('source SHA changed')
    raw=pd.read_csv(path,usecols=['ts','open','high','low','close','volume'])
    index=pd.DatetimeIndex(pd.to_datetime(raw.ts,unit='ms',utc=True))
    if not index.is_unique or not index.is_monotonic_increasing or (index.asi8%pd.Timedelta(minutes=5).value!=0).any():
        raise ValueError('invalid native clock')
    base=raw[['open','high','low','close','volume']].set_axis(index)
    since=pd.Timestamp(cfg['start'])-pd.Timedelta(minutes=cfg['higher_warmup_bars']*60)
    base=base.loc[(base.index>=since)&(base.index+pd.Timedelta(minutes=5)<=pd.Timestamp(cfg['end']))]
    pack=json.loads(Path(cfg['reference_pack']).read_text())
    parts,summary=run_stream(base,symbol,meta,cfg,pack)
    directory.mkdir(parents=True)
    for name,frame in parts.items():
        frame.to_csv(directory/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    receipt={'symbol':symbol,'run_identity':run_hash,'input_sha256':input_sha,'summary':summary,
             'files':{f'{name}.csv.gz':source.digest(directory/f'{name}.csv.gz') for name in TABLES},
             'wall_seconds':time.perf_counter()-before,'status':'complete'}
    dump(directory/'receipt.json',receipt)
    return receipt


def run(out,workers=4,symbols=None):
    cfg=json.loads(CONFIG.read_text())
    declared=(*_local_transitive_python((Path(__file__),)),CONFIG,EXP/'PROJECT_PLAN.md',
              Path(cfg['reference_pack']),Path('tests/evaluation/test_spike_ma_launch_gate.py'),
              Path('tests/evaluation/test_spike_ma_launch_study.py'))
    if not shared.engine._committed(tuple(declared)):
        raise ValueError('commit builder, dependencies, plan and tests before replay')
    spec=source.ExecutionSpec()
    if (spec.round_trip_cost,spec.arm_r,spec.trail_atr)!=(.002,2.,4.):
        raise ValueError('execution contract drift')
    if cfg['arms']!=list(ARMS) or cfg['minutes']!=15:
        raise ValueError('arm contract drift')
    if any(pd.Timestamp(cfg[k])!=v for k,v in [('start',source.START),('split',source.SPLIT),('end',source.DATA_END)]):
        raise ValueError('time contract drift')
    files=source.series_files();parent=json.loads(Path(cfg['parent_input_identity']).read_text())
    if len(files)!=cfg['expected_symbols'] or set(files)!=set(parent['inputs']):
        raise ValueError('archive universe drift')
    if source.digest(source.EXCHANGE_INFO)!=parent['exchange_info']['sha256']:
        raise ValueError('exchange metadata drift')
    chosen=sorted(files if symbols is None else symbols)
    if not chosen or not set(chosen)<=set(files):
        raise ValueError('invalid requested symbols')
    hashes={s:source.digest(files[s]) for s in chosen}
    if any(hashes[s]!=parent['inputs'][s] for s in chosen):
        raise ValueError('archive differs from frozen parent')
    identity={'config':cfg,'inputs':hashes,'input_paths':{s:str(files[s]) for s in chosen},
              'source':{str(p):source.digest(p) for p in declared},'symbols':chosen,'subset':symbols is not None,
              'metadata_sha256':source.digest(source.EXCHANGE_INFO),'numpy':np.__version__,'pandas':pd.__version__}
    run_hash=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    out=Path(out)
    if (out/'identity.json').exists():
        if json.loads((out/'identity.json').read_text())!=identity:
            raise ValueError('resume identity drift')
    elif out.exists():
        raise ValueError('unidentified output exists')
    else:
        out.mkdir(parents=True);dump(out/'identity.json',identity)
        dump(out/'started.json',{'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                                'run_identity':run_hash,'started_at':str(pd.Timestamp.now(tz='UTC'))})
    meta=source.symbol_meta();receipts=[];errors=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(worker,(s,str(files[s]),meta[s],str(out),run_hash,hashes[s],cfg)):s for s in chosen}
        for future in as_completed(pending):
            try:
                r=future.result();receipts.append(r)
                print(json.dumps({'completed':len(receipts),'total':len(chosen),'symbol':r['symbol'],
                                  'passed':r['summary']['passed'],'seconds':round(r['wall_seconds'],2)}),flush=True)
            except Exception as exc:
                errors.append({'symbol':pending[future],'error':repr(exc)});print(json.dumps(errors[-1]),flush=True)
    manifest={'complete':len(receipts)==len(chosen) and not errors,'run_identity':run_hash,'symbols':chosen,
              'receipts':{r['symbol']:source.digest(out/'streams'/r['symbol']/'receipt.json') for r in receipts},
              'errors':errors,'training_eligible':False,'production_eligible':False,'native_pine_parity':False}
    dump(out/'manifest.json',manifest)
    if errors:
        raise RuntimeError(f'{len(errors)} failed streams')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=EXP/'run_v1')
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--symbols',nargs='+')
    args=parser.parse_args();run(args.out,args.workers,args.symbols)

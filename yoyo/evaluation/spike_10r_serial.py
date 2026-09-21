"""Original V9 serial execution check of frozen earlier-selected 10R rules.

Only long admission changes. Preserved original raw reverse exits, shorts,
next-open fills, initial stops, 2R-close/4ATR protection and 20bp roundtrip cost.
Receipt-bound precomputed decisions came from point-in-time feature thresholds;
no outcome column participates in the entry mask. Independent-candidate random
controls are reused by event identity after exact target economics checks.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v9_full_replay import prepare_v9
from yoyo.evaluation.spike_v8_six_filters import assert_baseline_parity, _committed
from yoyo.evaluation.spike_six_filter_statistics import strict_bool
from yoyo.evaluation import spike_10r_search as search

EXP=search.EXP
SOURCE=Path('experiments/active/exp-spike-v9-full-backtest-20260915-v1')


def apply_gate(prepared, lookup):
    """Read only original ordinal keys and boolean close-known admissions."""
    allowed=prepared.allowed.copy()
    for i in np.flatnonzero(allowed & (prepared.raw_side==1)):
        ordinal=prepared.ordinal.get(prepared.frame.index[i])
        if ordinal is None:
            raise ValueError('candidate original ordinal missing')
        key=f'{prepared.context.key}:{ordinal}:1'
        value=lookup.get(key,False)
        if not isinstance(value,(bool,np.bool_)):
            raise ValueError('nonboolean causal admission')
        allowed[i]=bool(value)
    return replace(prepared,allowed=allowed)


def serial_retention(part, baseline):
    """Count retained original winners by identity, separating newly opened wins."""
    def winners(frame):
        return set(frame.loc[frame.valid_entry & ~frame.censored & frame.net_r.gt(10),'event_key'])
    old,new=winners(baseline),winners(part)
    return dict(retained_gt10=len(old & new),lost_gt10=len(old-new),gained_gt10=len(new-old),
                recall=len(old & new)/len(old) if old else np.nan,
                gt10_count_ratio=len(new)/len(old) if old else np.nan)


def one(args):
    source, gates, output, identity=args
    key=source['key']
    out=Path(output)/'streams'/key
    if out.exists():
        receipt=json.loads((out/'completion.json').read_text())
        if receipt['identity']!=identity:
            raise ValueError('resumed serial identity mismatch')
        for name,sha in receipt['files'].items():
            if search.digest(out/name)!=sha:
                raise ValueError('serial output drift')
        return receipt
    source_dir=SOURCE/'results/full_v1/streams'/key
    if search.digest(source_dir/'completion.json')!=source['receipt_sha256']:
        raise ValueError('source receipt drift')
    src=json.loads((source_dir/'completion.json').read_text())
    raw=base.SOURCE_STREAMS/key
    if (search.digest(raw/'completion.json')!=src['raw_completion_sha256'] or
        search.digest(raw/'control_cache.pkl.gz')!=src['cache_sha256'] or
        search.digest(source_dir/'v9.trades.csv.gz')!=src['files']['v9.trades.csv.gz']):
        raise ValueError('raw/source input drift')
    context=base.load_verified_stream(raw)
    prepared,_=prepare_v9(engine.prepare_arm(context,arm='v8'))
    original=pd.read_csv(source_dir/'v9.trades.csv.gz')
    tables=[]
    for rule,lookup in [('original_all',None),*gates.items()]:
        p=prepared if lookup is None else apply_gate(prepared,lookup)
        t,_,_=engine.replay_serial(context,arm='v8',enable_be=False,prepared=p)
        if lookup is None:
            assert_baseline_parity(t,original)
        t=t.loc[t.side.eq(1)].copy()
        t['rule']=rule
        t['event_key']=t.stream_key.astype(str)+':'+t.signal_i.astype(int).astype(str)+':1'
        t['valid_entry']=True
        t['available_at']=pd.to_datetime(t.signal_bar_open,utc=True)+pd.Timedelta(minutes=context.minutes)
        tables.append(t)
    if search.digest(raw/'control_cache.pkl.gz')!=src['cache_sha256']:
        raise ValueError('rawcache changed while serial replaying')
    out.mkdir(parents=True)
    pd.concat(tables,ignore_index=True).to_csv(out/'trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    receipt=dict(key=key,identity=identity,source_receipt_sha256=source['receipt_sha256'],cache_sha256=src['cache_sha256'],
                 baseline_parity=True,files={'trades.csv.gz':search.digest(out/'trades.csv.gz')})
    search.dump(out/'completion.json',receipt)
    return receipt


def run(dataset,analysis,output,workers,adaptive=None):
    dependencies=[Path(__file__),Path('tests/evaluation/test_spike_10r_serial.py'),Path(search.__file__),
                  EXP/'PROJECT_PLAN.md',EXP/'config.json',Path(engine.__file__),Path(base.__file__),
                  Path('yoyo/evaluation/spike_v9_full_replay.py'),Path('yoyo/evaluation/spike_v7_fast.py'),
                  Path('yoyo/evaluation/spike_v8_replay.py'),Path('yoyo/evaluation/spike_high_r_entry_report.py')]
    if adaptive is not None:
        from yoyo.evaluation import spike_10r_adaptive as adaptive_engine
        dependencies.extend([Path(adaptive_engine.__file__),adaptive_engine.EXP/'config.json',adaptive_engine.EXP/'PROJECT_PLAN.md'])
    if not _committed(dependencies):
        raise ValueError('commit serial builder before replay')
    receipt=json.loads((analysis/'evaluation_receipt.json').read_text())
    for name in ('decisions.csv.gz','selection.json'):
        if search.digest(analysis/name)!=receipt['files'][name]:
            raise ValueError('frozen causal decisions changed')
    selection=json.loads((analysis/'selection.json').read_text())
    if (selection['search_code_sha256']!=search.digest(Path(search.__file__)) or
            selection['config_sha256']!=search.digest(EXP/'config.json')):
        raise ValueError('selection builder or config drift')
    candidates,controls,_=search.load_candidates(dataset)
    if selection['dataset_receipt_sha256']!=search.digest(dataset/'receipt.json'):
        raise ValueError('dataset differs from selection')
    d=pd.read_csv(analysis/'decisions.csv.gz')
    names=sorted({x['choice']['rule'] for x in selection['selected'] if x['choice']})
    if d.event_key.duplicated().any() or set(d.event_key)!=set(candidates.event_key):
        raise ValueError('decision universe mismatch')
    if adaptive is not None:
        ar=json.loads((adaptive/'receipt.json').read_text())
        if (ar['dataset_receipt_sha256']!=search.digest(dataset/'receipt.json') or
                ar['parent_evaluation_receipt_sha256']!=search.digest(analysis/'evaluation_receipt.json')):
            raise ValueError('adaptive source differs')
        for name,sha in ar['files'].items():
            if search.digest(adaptive/name)!=sha:
                raise ValueError('adaptive artifact drift')
        for path,sha in ar['dependencies'].items():
            if search.digest(Path(path))!=sha:
                raise ValueError('adaptive builder drift')
        ad=pd.read_csv(adaptive/'decisions.csv.gz')
        if ad.event_key.duplicated().any() or set(ad.event_key)!=set(candidates.event_key):
            raise ValueError('adaptive candidate universe mismatch')
        d=d.merge(ad.rename(columns={'admitted':adaptive_engine.NAME}),on='event_key',validate='one_to_one')
        names.append(adaptive_engine.NAME)
    for name in names:
        d[name]=strict_bool(d[name])
    pin=SOURCE/'statistics/full_v1/statistics_receipt.json'
    cfg=json.loads((EXP/'config.json').read_text())
    if search.digest(pin)!=cfg['statistics_receipt_sha256']:
        raise ValueError('frozen source list mismatch')
    sources=json.loads(pin.read_text())['source_receipts']
    if len(sources)!=3531 or len({s['key'] for s in sources})!=3531:
        raise ValueError('serial stream coverage mismatch')
    dependency_hashes={str(p):search.digest(p) for p in dependencies}
    import hashlib
    identity=hashlib.sha256((search.digest(analysis/'evaluation_receipt.json')+
        (search.digest(adaptive/'receipt.json') if adaptive is not None else '')+json.dumps(dependency_hashes,sort_keys=True)).encode()).hexdigest()
    output.mkdir(parents=True,exist_ok=True)
    if (output/'receipt.json').exists():
        raise ValueError('refuse to overwrite completed serial check')
    tasks=[]
    by={key:part for key,part in d.groupby('stream_key')}
    for src in sources:
        part=by.get(src['key'],d.iloc[:0])
        gates={name:dict(zip(part.event_key,part[name])) for name in names}
        tasks.append((src,gates,str(output),identity))
    done=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(one,t) for t in tasks]
        for f in as_completed(futures):
            done.append(f.result())
            if len(done)%250==0:
                print(f'serial {len(done)}/3531',flush=True)
    frames=[pd.read_csv(output/'streams'/r['key']/'trades.csv.gz') for r in sorted(done,key=lambda r:r['key'])]
    trades=pd.concat(frames,ignore_index=True)
    for name in ('entry_time','exit_time','signal_bar_open','available_at'):
        trades[name]=pd.to_datetime(trades[name],utc=True)
    trades['censored']=strict_bool(trades.censored)
    trades['valid_entry']=strict_bool(trades.valid_entry)
    if trades.duplicated(['rule','event_key']).any():
        raise ValueError('duplicate serial event')
    # Changed admission must never change a retained candidate economic path.
    joined=trades.merge(candidates[['event_key','net_r','net_return','censored']],on='event_key',how='left',validate='many_to_one',suffixes=('','_candidate'))
    if (not np.allclose(joined.net_r,joined.net_r_candidate,equal_nan=True) or
        not np.allclose(joined.net_return,joined.net_return_candidate,equal_nan=True) or
        not joined.censored.eq(joined.censored_candidate).all()):
        raise ValueError('independent/serial target economic parity failed')
    baseline=trades.loc[trades.rule.eq('original_all')]
    rows=[]
    for period,bclock in search.periods(baseline):
        u=baseline.loc[bclock]
        for name,part in trades.groupby('rule'):
            clock=dict(search.periods(part))[period]
            s=part.loc[clock]
            rows.append(dict(period=period,rule=name,**search.metrics(s,u),
                        **search.control_statistics(s,controls,period),**search.rate_interval(u,s),**serial_retention(s,u)))
    table=pd.DataFrame(rows)
    later=table.period.eq('later') & ~table.rule.eq('original_all')
    for what in ('tail','net'):
        table.loc[later,'random_'+what+'_holm_p']=search.holm(table.loc[later,'random_'+what+'_p'])
    table.loc[later,'historical_gate_passed']=(table.loc[later,'precision_ci_low'].gt(0) &
        table.loc[later,'random_tail_holm_p'].lt(.01) & table.loc[later,'random_net_holm_p'].lt(.01) &
        table.loc[later,'mean_net_bp'].gt(0) & table.loc[later,'recall'].ge(.1))
    table.to_csv(output/'comparison.csv',index=False)
    trades.to_csv(output/'trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    search.dump(output/'receipt.json',dict(complete=True,streams=len(done),identity=identity,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        generated_at=pd.Timestamp.now(tz='UTC').isoformat(),baseline_parity=True,candidate_economic_parity=True,
        dependencies=dependency_hashes,source_statistics_sha256=search.digest(pin),evaluation_receipt_sha256=search.digest(analysis/'evaluation_receipt.json'),
        stream_receipts={r['key']:search.digest(output/'streams'/r['key']/'completion.json') for r in done},
        files={name:search.digest(output/name) for name in ('comparison.csv','trades.csv.gz')}))
    print(table.loc[table.period.eq('later')].to_string(index=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--analysis',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workers',type=int,default=6)
    p.add_argument('--adaptive',type=Path)
    a=p.parse_args()
    run(a.dataset,a.analysis,a.output,a.workers,a.adaptive)

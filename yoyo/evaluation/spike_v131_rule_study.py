"""Generate receipt-bound V13.1 rule-ablation and causal state opportunity paths.

Owner approved rules and startup/overextension study on 2026-10-19. All arms
retain rebreak entry, original stop/risk, 2R activation, timeframe trailing,
20bp costs and ORIGINAL raw opposite events. A union of event identities is
evaluated once; each policy gets a separate queue downstream. No downloads,
model training, production mutation or content hashes.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import shutil
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v131_entry_stages import parent,attempt,draws,dump,read
from yoyo.evaluation.spike_v131_excursions import provenance
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v131_rule_facts import ARMS,FEATURES,rules,state_features

EXP=Path('experiments/active/exp-spike-v131-rule-state-20261019-v1')
CONFIG=EXP/'config.json'
BASE=Path('experiments/active/exp-spike-v131-entry-stages-20261019-v1/results_v1')
BTC={}


def load_base(source,cfg):
    """Read the registered path, checking metadata and real OHLCV, no hashing."""
    raw=pd.read_csv(source['path'],usecols=['ts','open','high','low','close','volume'])
    assert len(raw)==source['rows'] and raw.ts.is_monotonic_increasing and not raw.ts.duplicated().any()
    assert int(raw.ts.iloc[0])==source['first_ms'] and int(raw.ts.iloc[-1])==source['last_ms']
    assert np.isfinite(raw.to_numpy(float)).all() and (raw.volume>=0).all()
    assert ((raw.low>0)&(raw.low<=raw[['open','close']].min(axis=1))&(raw.high>=raw[['open','close']].max(axis=1))).all()
    base=raw.drop(columns='ts').set_axis(pd.to_datetime(raw.ts.to_numpy(),unit='ms',utc=True))
    return base[(base.index>=pd.Timestamp(cfg['warmup_start'])) & (base.index+pd.Timedelta(minutes=5)<=pd.Timestamp(cfg['end']))]


def init_worker(btcsource,cfg):
    global BTC
    base=load_base(btcsource,cfg)
    BTC={m:parent.complete_bars(base,m)[0].close for m in cfg['timeframes']}


def worker(args):
    source,meta,cfg,root=args;start=time.perf_counter();symbol=source['symbol']
    before=provenance(source['path']);base=load_base(source,cfg)
    folder=Path(root)/'streams'/symbol;folder.mkdir(parents=True,exist_ok=False)
    receipts=[]
    for minutes in cfg['timeframes']:
        bars,partial=parent.complete_bars(base,minutes)
        facts=parent.facts_for(bars,base,meta['asset'],float(meta['tick']),minutes) if minutes in (15,60) else parent._generic_facts(bars,meta['asset'],float(meta['tick']),minutes)
        frame=facts['frame'];clock=frame.index+pd.Timedelta(minutes=minutes)
        masks,raw,duration=rules(frame,facts['gap'],meta['asset'],minutes,facts['h1'].sma_60.to_numpy() if minutes==15 else None)
        np.testing.assert_array_equal(raw,facts['side'])
        np.testing.assert_array_equal(masks['baseline',1]|masks['baseline',-1],facts['v9'])
        # A complete candle ending at END is observed data, but not a new
        # decision opportunity in the half-open [start,END) research window.
        for key in masks:masks[key] &= np.asarray(clock<pd.Timestamp(cfg['end']))
        feat=state_features(frame,facts['gap'],BTC[minutes],duration,minutes)
        identity=dict(symbol=symbol,timeframe_min=minutes,asset=meta['asset'],venue='binance_um')
        p=parent.source.prepared_arm(frame,facts['gap'],raw,f'binance_um:{symbol}:{minutes}m',identity,minutes,float(meta['tick']))
        if minutes==60:p=replace(p,spec=replace(p.spec,trail_atr=2.))
        memberships={}
        for ai,arm in enumerate(ARMS):
            for side in (1,-1):
                for i in np.flatnonzero(masks[arm,side]):
                    memberships[int(i),side]=memberships.get((int(i),side),0)|(1<<ai)
        anchors=[]
        for (i,side),bits in sorted(memberships.items()):
            key=f'binance_um:{symbol}:{minutes}m:v9_both:{clock[i].isoformat()}'
            # Preserve old identities; disambiguate only a nonbaseline opposite.
            if (i,-side) in memberships and (raw[i]!=side if raw[i]!=0 else side==-1):key+=f':side{side}'
            anchors.append(dict(anchor_i=i,side=side,trade_key=key,members=bits))
        assert len({a['trade_key'] for a in anchors})==len(anchors)
        chosen=draws(p,anchors,cfg)
        old=read(BASE/'streams'/symbol/f'{minutes}m.csv.gz')
        old=old[old.policy.eq('rebreak')] if len(old) else old
        oldsignal=old[old.cohort.eq('signal')] if len(old) else old
        baseline_keys={a['trade_key'] for a in anchors if a['members']&1}
        assert baseline_keys==set(oldsignal.trade_key if len(oldsignal) else [])
        oldmap={(r['trade_key'],r['cohort']):r for r in old.to_dict('records')}
        rows=[];cache={};checks=0;drawchecks=0
        for a in anchors:
            key=a['trade_key'];ci=chosen[key];pair=ci is not None and clock[ci].dayofweek!=6
            for cohort,i in [('signal',a['anchor_i']),('control',ci)]:
                if i is None:continue
                ck=(int(i),a['side'])
                if ck not in cache:cache[ck]=attempt(p,int(i),a['side'],'rebreak')
                r=dict(cache[ck]);d=int(r['decision_i']);x=int(r.get('exit_i',d))
                r.update(trade_key=key,cohort=cohort,members=a['members'],pair_eligible=pair,
                    symbol=symbol,timeframe_min=minutes,anchor_time=clock[i].isoformat(),
                    decision_time=clock[d].isoformat(),mature_time=clock[x if r['filled'] else d].isoformat(),
                    entry_time=frame.index[int(r['entry_i'])].isoformat() if r['filled'] else None,
                    exit_time=frame.index[x].isoformat() if r['filled'] else None)
                for name in FEATURES:
                    v=float(feat[name].iloc[i]);r[name]=v if name=='compression_run60' else a['side']*v
                rows.append(r)
                if a['members']&1 and (key,cohort) in oldmap:
                    prior=oldmap[key,cohort]
                    assert r['anchor_i']==int(prior['anchor_i']);drawchecks+=cohort=='control'
                    for field in ('status','decision_i','filled'):assert r[field]==prior[field],(key,field)
                    checks+=3
                    if r['filled']:
                        for field in ('entry_i','exit_i','entry_price','initial_stop','initial_risk','exit_price','net_r'):
                            assert np.isclose(r[field],prior[field],atol=1e-8,rtol=1e-10,equal_nan=True),(key,field,r[field],prior[field])
                            checks+=1
        ledger=pd.DataFrame(rows)
        if not len(ledger):ledger=pd.DataFrame(columns=['trade_key','cohort','members','pair_eligible','symbol','timeframe_min',*FEATURES])
        ledger.to_csv(folder/f'{minutes}m.csv.gz',index=False)
        # Prefixes independently recompute ALL raw detectors and new features.
        prefix_checks=0
        if len(anchors) and symbol in cfg['prefix_symbols']:
            test_ids=sorted(set(a['anchor_i'] for a in [anchors[len(anchors)//3],anchors[-1]]))
            for i in test_ids:
                short=frame.iloc[:i+1];g=facts['gap'][:i+1]
                sm,sr,sd=rules(short,g,meta['asset'],minutes,facts['h1'].sma_60.to_numpy()[:i+1] if minutes==15 else None)
                sf=state_features(short,g,BTC[minutes].iloc[:BTC[minutes].index.searchsorted(short.index[-1],side='right')],sd,minutes)
                for arm in ARMS:
                    for side in (1,-1):
                        np.testing.assert_array_equal(sm[arm,side],masks[arm,side][:i+1]);prefix_checks+=1
                np.testing.assert_allclose(sf.to_numpy(),feat.iloc[:i+1].to_numpy(),equal_nan=True);prefix_checks+=3
        receipts.append(dict(timeframe_min=minutes,bars=len(frame),partial=partial,rows=len(rows),union_anchors=len(anchors),
            baseline_anchors=len(baseline_keys),anchor_counts={arm:int(sum(masks[arm,s].sum() for s in (1,-1))) for arm in ARMS},
            baseline_scalar_checks=checks,draw_identity_checks=drawchecks,prefix_checks=prefix_checks))
    assert before==provenance(source['path'])
    receipt=dict(symbol=symbol,seconds=time.perf_counter()-start,input=before,streams=receipts)
    dump(folder/'receipt.json',receipt);return receipt


def reuse_complete(prior,root,source,cfg):
    """Reuse only complete, input-identical streams unaffected by the end fix.

    Compatibility is deliberately restricted to builder448ec8998e, whose only
    numerical difference is masked undefined distance arithmetic; the only
    candidate change is excluding anchors at END. Any such stream is rebuilt.
    No hashing, no rewriting old outputs and no trust in partial receipts.
    """
    folder=Path(prior)/'streams'/source['symbol'];receipt=folder/'receipt.json'
    if not receipt.exists():return None
    old=json.loads(receipt.read_text())
    if old['input']!=provenance(source['path']):return None
    if sorted(s['timeframe_min'] for s in old['streams'])!=sorted(cfg['timeframes']):return None
    for stream in old['streams']:
        f=read(folder/f"{stream['timeframe_min']}m.csv.gz",['cohort','anchor_time'])
        if len(f)!=stream['rows']:return None
        if len(f) and (pd.to_datetime(f.loc[f.cohort.eq('signal'),'anchor_time'],utc=True)>=pd.Timestamp(cfg['end'])).any():return None
    destination=Path(root)/'streams'/source['symbol'];shutil.copytree(folder,destination)
    old['reused_from']=str(folder);old['reuse_reason']='Complete old stream has no END anchor and identical source path/size/mtime; no output semantics change.'
    dump(destination/'receipt.json',old)
    return old


def run(root,workers,symbols,reuse=None):
    cfg=json.loads(CONFIG.read_text());assert cfg['arms']==list(ARMS) and cfg['features']==list(FEATURES)
    paths=sorted(set([Path(__file__),CONFIG,EXP/'PROJECT_PLAN.md',*_local_transitive_python((Path(__file__),))]),key=str)
    for p in paths:subprocess.run(['git','ls-files','--error-unmatch',str(p)],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['git','diff','--exit-code','HEAD','--',*map(str,paths)],check=True)
    manifest=json.loads(Path(cfg['input_manifest']).read_text());assert manifest['complete'] and not manifest['errors']
    sources=manifest['streams'];assert len(sources)==638
    btc=next(r for r in sources if r['symbol']=='BTCUSDT')
    if symbols:sources=[r for r in sources if r['symbol'] in symbols];assert len(sources)==len(symbols)
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    meta=parent.source.symbol_meta()
    dump(root/'identity.json',dict(generated_at=datetime.now(timezone.utc).isoformat(),config=cfg,subset=bool(symbols),
        symbols=[r['symbol'] for r in sources],builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        code=[provenance(p) for p in paths],manifest=provenance(cfg['input_manifest']),metadata=provenance(parent.source.EXCHANGE_INFO),btc=provenance(btc['path'])))
    receipts=[];errors=[]
    if reuse:
        oldid=json.loads((Path(reuse)/'identity.json').read_text())
        assert oldid['builder_commit'].startswith('448ec8998e') and oldid['config']==cfg
        pending=[]
        for source in sources:
            cached=reuse_complete(reuse,root,source,cfg)
            if cached is None:pending.append(source)
            else:receipts.append(cached)
        sources=pending
        dump(root/'reuse.json',dict(source=str(reuse),original_builder=oldid['builder_commit'],reused=[r['symbol'] for r in receipts],fresh=[r['symbol'] for r in sources]))
        print(json.dumps(dict(reused=len(receipts),fresh=len(sources))),flush=True)
    with ProcessPoolExecutor(max_workers=workers,initializer=init_worker,initargs=(btc,cfg)) as pool:
        fs={pool.submit(worker,(r,meta[r['symbol']],cfg,str(root))):r['symbol'] for r in sources}
        for n,f in enumerate(as_completed(fs),1):
            try:receipts.append(f.result())
            except Exception as exc:errors.append(dict(symbol=fs[f],error=repr(exc)))
            if n==1 or n%10==0 or n==len(fs):print(json.dumps(dict(done=n,total=len(fs),errors=errors[-3:])),flush=True)
    dump(root/'receipt.json',dict(complete=not errors,errors=errors,receipts=receipts))
    if errors:raise RuntimeError(str(errors[:5]))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=EXP/'paths_v1')
    parser.add_argument('--workers',type=int,default=4);parser.add_argument('--symbols',nargs='+');parser.add_argument('--reuse',type=Path)
    a=parser.parse_args();run(a.output,a.workers,a.symbols,a.reuse)

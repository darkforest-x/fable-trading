"""Receipt-bound Stoch/WVF entry study on the registered 638-symbol archive.

All entries are long, closed-bar decisions and next-open fills. Original
Stoch5/3/3, 20 threshold, WVF22/20/50 and 0.85-of-max remain unchanged.
Standalone admissions require prepared history and 50 continuous chart bars.
Spike arms keep original anchor geometry and deadline. This builder never
downloads, hashes inputs, mutates historical results or touches live services.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_stoch_entry import SPIKE_POLICIES, STOCH_POLICIES, timed_attempt, timed_decision, excursions
from yoyo.evaluation.spike_fanshen_exit import compute_signals
from yoyo.evaluation.spike_v131_entry_stages import parent, attempt, draws, mark_serial, dump
from yoyo.evaluation.spike_v131_rule_study import load_base, BASE
from yoyo.evaluation.spike_v131_rule_summary import read
from yoyo.evaluation.spike_v131_excursions import provenance
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python

EXP = Path('experiments/active/exp-spike-stoch-wvf-entry-20261019-v1')
CONFIG = EXP/'config.json'


def worker(args):
    source, meta, cfg, root = args
    began = time.perf_counter(); symbol = source['symbol']
    before = provenance(source['path']); base = load_base(source, cfg)
    folder = Path(root)/'streams'/symbol; folder.mkdir(parents=True, exist_ok=False)
    receipts = []
    for minutes in cfg['timeframes']:
        bars, partial = parent.complete_bars(base, minutes)
        facts = parent.facts_for(bars,base,meta['asset'],float(meta['tick']),minutes) if minutes in (15,60) else parent._generic_facts(bars,meta['asset'],float(meta['tick']),minutes)
        frame = facts['frame']; clock = frame.index+pd.Timedelta(minutes=minutes)
        identity = dict(symbol=symbol,timeframe_min=minutes,asset=meta['asset'],venue='binance_um')
        p = parent.source.prepared_arm(frame,facts['gap'],facts['side'],f'binance_um:{symbol}:{minutes}m',identity,minutes,float(meta['tick']))
        if minutes == 60: p = replace(p,spec=replace(p.spec,trail_atr=2.))
        sig = compute_signals(frame)
        arrow = sig.arrow_long.to_numpy(bool); green = sig.wvf_green.to_numpy(bool)
        # Historical input gaps cannot manufacture a fresh cross across missing bars.
        continuous = pd.Series(p.gap.astype(int)).rolling(50,min_periods=50).sum().eq(0).to_numpy()
        arrow = arrow & continuous; combined = arrow & green
        valid = (frame.ready.fillna(False).to_numpy(bool) & continuous &
                 np.isfinite(p.atr) & (p.atr>0) & (clock.dayofweek!=6) &
                 (clock>=pd.Timestamp(cfg['start'])) & (clock<pd.Timestamp(cfg['end'])))
        anchors = []
        for group, mask in [('spike',facts['v9'] & (facts['side']==1) & (clock<pd.Timestamp(cfg['end']))),
                            ('standalone',arrow & valid)]:
            for i in np.flatnonzero(mask):
                key = f'binance_um:{symbol}:{minutes}m:' + ('v9_both:' if group=='spike' else 'stoch533:') + clock[i].isoformat()
                anchors.append(dict(anchor_i=int(i),side=1,trade_key=key,group=group))
        chosen = draws(p,anchors,cfg)
        prior = read(BASE/'streams'/symbol/f'{minutes}m.csv.gz')
        prior = prior[prior.side.eq(1) & prior.policy.isin(['pullback','rebreak'])] if len(prior) else prior
        oldmap = {(r['trade_key'],r['cohort'],r['policy']):r for r in prior.to_dict('records')}
        assert {a['trade_key'] for a in anchors if a['group']=='spike'} == set(prior.loc[prior.cohort.eq('signal'),'trade_key'] if len(prior) else [])
        rows = []; cache = {}; checks = 0; prefix_checks = 0
        for a in anchors:
            ai = a['anchor_i']; ci = chosen[a['trade_key']]
            pair = ci is not None and clock[ci].dayofweek!=6
            if a['group']=='standalone': pair = pair and bool(valid[ci]) if ci is not None else False
            policies = SPIKE_POLICIES if a['group']=='spike' else STOCH_POLICIES
            for cohort,i in [('signal',ai),('control',ci)]:
                if i is None: continue
                for policy in policies:
                    engine_policy = 'immediate' if policy in STOCH_POLICIES else policy
                    ck = (int(i),engine_policy)
                    if ck not in cache:
                        r = timed_attempt(p,int(i),policy,arrow if policy=='pullback_stoch' else combined) if policy.startswith('pullback_') else attempt(p,int(i),1,engine_policy)
                        cache[ck] = r | excursions(p,r)
                    r = dict(cache[ck]); d = int(r['decision_i']); x = int(r.get('exit_i',d))
                    r.update(policy=policy,group=a['group'],trade_key=a['trade_key'],cohort=cohort,
                        symbol=symbol,timeframe_min=minutes,eligible=bool(green[ai]) if policy=='stoch_wvf' else True,
                        pair_eligible=bool(pair),state_pair=bool(pair and green[ai] and green[ci]) if ci is not None else False,
                        anchor_green=bool(green[i]),anchor_stoch=bool(arrow[i]),
                        decision_green=bool(green[d]),decision_stoch=bool(arrow[d]),
                        anchor_time=clock[i].isoformat(),decision_time=clock[d].isoformat(),mature_time=clock[x if r['filled'] else d].isoformat(),
                        entry_time=frame.index[int(r['entry_i'])].isoformat() if r['filled'] else None,
                        exit_time=frame.index[x].isoformat() if r['filled'] else None,
                        serial_all=False,serial_pair=False,serial_state=False)
                    rows.append(r)
                    if a['group']=='spike' and policy in ('pullback','rebreak'):
                        old = oldmap[a['trade_key'],cohort,policy]
                        for k in ('anchor_i','decision_i','status','filled'):
                            assert r[k]==old[k], (symbol,minutes,a['trade_key'],k); checks+=1
                        if r['filled']:
                            for k in ('entry_i','exit_i','entry_price','initial_stop','initial_risk','exit_price','net_r'):
                                assert np.isclose(r[k],old[k],atol=1e-8,rtol=1e-10,equal_nan=True),(symbol,minutes,k); checks+=1
        for group, policies in [('spike',SPIKE_POLICIES),('standalone',STOCH_POLICIES)]:
            for policy in policies:
                for cohort in ('signal','control'):
                    subset = [r for r in rows if r['group']==group and r['policy']==policy and r['cohort']==cohort and r['eligible']]
                    mark_serial(subset,'serial_all',len(frame))
                    mark_serial([r for r in subset if r['pair_eligible']],'serial_pair',len(frame))
                    if group=='standalone':mark_serial([r for r in subset if r['state_pair']],'serial_state',len(frame))
        if anchors and symbol in cfg['prefix_symbols']:
            for i in sorted(set(a['anchor_i'] for a in (anchors[len(anchors)//2],anchors[-1]))):
                short = compute_signals(frame.iloc[:i+1])
                for k in sig.columns:
                    np.testing.assert_allclose(short[k].to_numpy(),sig[k].iloc[:i+1].to_numpy(),equal_nan=True);prefix_checks+=1
            from types import SimpleNamespace
            for r in [z for z in rows if z['cohort']=='signal' and z['policy'].startswith('pullback_') and 'initial_stop' in z][:20]:
                d=r['decision_i']; q=SimpleNamespace(**{k:getattr(p,k)[:d+1] for k in ('high','low','close','gap','raw_side')})
                trigger=arrow if r['policy']=='pullback_stoch' else combined
                status,di=timed_decision(p,r['anchor_i'],r['initial_stop'],trigger)
                # Expiry at a configured deadline is known even on a truncated prefix.
                got=timed_decision(q,r['anchor_i'],r['initial_stop'],trigger[:d+1])
                assert di==got[1] and (status==got[0] or status=='expired' and got[0]=='pending_boundary')
                prefix_checks+=1
        ledger = pd.DataFrame(rows)
        ledger.to_csv(folder/f'{minutes}m.csv.gz',index=False)
        receipts.append(dict(timeframe_min=minutes,bars=len(frame),partial=partial,rows=len(rows),
            spike_anchors=sum(a['group']=='spike' for a in anchors),stoch_anchors=sum(a['group']=='standalone' for a in anchors),
            original_path_checks=checks,prefix_checks=prefix_checks,
            independently_traced_closed_paths=sum(r['status']=='closed' for r in cache.values())))
    assert before==provenance(source['path'])
    receipt=dict(symbol=symbol,seconds=time.perf_counter()-began,input=before,streams=receipts)
    dump(folder/'receipt.json',receipt);return receipt


def run(root,workers,symbols):
    cfg=json.loads(CONFIG.read_text())
    paths=sorted(set([Path(__file__),CONFIG,EXP/'PROJECT_PLAN.md',*_local_transitive_python((Path(__file__),))]),key=str)
    for p in paths:subprocess.run(['git','ls-files','--error-unmatch',str(p)],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['git','diff','--exit-code','HEAD','--',*map(str,paths)],check=True)
    manifest=json.loads(Path(cfg['input_manifest']).read_text());assert manifest['complete'] and not manifest['errors']
    sources=manifest['streams'];assert len(sources)==638
    if symbols:sources=[r for r in sources if r['symbol'] in symbols];assert len(sources)==len(symbols)
    root=Path(root);root.mkdir(parents=True,exist_ok=False);meta=parent.source.symbol_meta()
    dump(root/'identity.json',dict(generated_at=datetime.now(timezone.utc).isoformat(),config=cfg,subset=bool(symbols),
        symbols=[r['symbol'] for r in sources],builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        code=[provenance(p) for p in paths],manifest=provenance(cfg['input_manifest']),metadata=provenance(parent.source.EXCHANGE_INFO)))
    receipts=[];errors=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        fs={pool.submit(worker,(r,meta[r['symbol']],cfg,str(root))):r['symbol'] for r in sources}
        for n,f in enumerate(as_completed(fs),1):
            try:receipts.append(f.result())
            except Exception as exc:errors.append(dict(symbol=fs[f],error=repr(exc)))
            if n==1 or n%10==0 or n==len(fs):print(json.dumps(dict(done=n,total=len(fs),errors=errors[-3:])),flush=True)
    dump(root/'receipt.json',dict(complete=not errors,errors=errors,receipts=receipts))
    if errors:raise RuntimeError(str(errors[:5]))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=EXP/'paths_v1')
    p.add_argument('--workers',type=int,default=4);p.add_argument('--symbols',nargs='+')
    a=p.parse_args();run(a.output,a.workers,a.symbols)

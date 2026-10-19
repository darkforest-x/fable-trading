"""Independent bounded price-path replay for extreme rule/state research trades.

Selection is diagnostic only. Per-timeframe/per-policy maximum and minimum net
trades are deduplicated and checked against real OHLCV, scalar original stop
construction, closed-bar rebreak chronology and an independent exit interpreter.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v131_rule_study import CONFIG,parent,load_base
from yoyo.evaluation.spike_v131_rule_facts import ARMS,rules
from yoyo.evaluation.spike_v131_excursions import provenance
from yoyo.evaluation.spike_v128_retest_state import confirmation


def exit_trace(o,h,l,c,atr,gap,opposite,side,entry,stop,entry_i,tick,trail):
    """Prior protection wins; close2R ratchets next bar, opposite exits next open."""
    risk=side*(entry-stop);armed=False;reverse=False;protection=stop
    for i in range(entry_i,len(c)):
        if gap[i]:return None
        if side*(o[i]-protection)<=0:return i,float(o[i])
        if reverse:return i,float(o[i])
        touched=l[i]<=protection if side==1 else h[i]>=protection
        if touched:return i,float(protection)
        if side*(c[i]-entry)>=2*risk:armed=True
        if armed and math.isfinite(atr[i]) and atr[i]>0:
            candidate=c[i]-side*trail*atr[i]
            candidate=(math.floor(candidate/tick) if side==1 else math.ceil(candidate/tick))*tick
            protection=max(protection,candidate) if side==1 else min(protection,candidate)
        reverse=opposite[i]==-side
    return None


def worker(args):
    source,meta,targets,cfg=args;before=provenance(source['path']);base=load_base(source,cfg)
    grouped=defaultdict(list)
    for r in targets:grouped[int(r['timeframe_min'])].append(r)
    output=[];tick=float(meta['tick'])
    for m,group in grouped.items():
        bars,_=parent.complete_bars(base,m)
        facts=parent.facts_for(bars,base,meta['asset'],tick,m) if m in (15,60) else parent._generic_facts(bars,meta['asset'],tick,m)
        f=facts['frame'];masks,raw,_=rules(f,facts['gap'],meta['asset'],m,facts['h1'].sma_60.to_numpy() if m==15 else None)
        assert np.array_equal(raw,facts['side'])
        arrays={k:f[k].to_numpy(float) for k in ('open','high','low','close','atr')}
        for r in group:
            a,d,e,x=[int(float(r[k])) for k in ('anchor_i','decision_i','entry_i','exit_i')];s=int(r['side'])
            policy=r['audit_policy'];arm=policy if policy in ARMS else 'baseline'
            assert masks[arm,s][a],(r['trade_key'],arm,'anchor')
            atr=arrays['atr'][a];extreme=min(arrays['low'][a-4:a+1]) if s==1 else max(arrays['high'][a-4:a+1])
            stopraw=min(extreme-.2*atr,arrays['close'][a]-2*atr) if s==1 else max(extreme+.2*atr,arrays['close'][a]+2*atr)
            stop=(math.floor(stopraw/tick) if s==1 else math.ceil(stopraw/tick))*tick
            assert math.isclose(stop,float(r['initial_stop']),abs_tol=1e-9,rel_tol=1e-10)
            # Independent retest interpreter reads only through decision close.
            answer=confirmation(arrays['open'][:d+1],arrays['high'][:d+1],arrays['low'][:d+1],arrays['close'][:d+1],
                                facts['gap'][:d+1],raw[:d+1],a,s,stop,24)
            assert answer['confirmation_i']==d and e==d+1
            entry=arrays['open'][e];risk=s*(entry-stop)
            result=exit_trace(arrays['open'],arrays['high'],arrays['low'],arrays['close'],arrays['atr'],facts['gap'],raw,
                              s,entry,stop,e,tick,2 if m==60 else 4)
            assert result is not None and result[0]==x,(r['trade_key'],result,x)
            assert math.isclose(result[1],float(r['exit_price']),rel_tol=1e-10,abs_tol=1e-9)
            net=(s*(result[1]-entry)-.002*entry)/risk
            assert math.isclose(net,float(r['net_r']),abs_tol=1e-8,rel_tol=1e-10)
            output.append(dict(trade_key=r['trade_key'],timeframe_min=m,net_r=net,status='passed'))
    assert before==provenance(source['path'])
    return dict(source=before,cases=output)


def run(root,name='analysis_v1'):
    root=Path(root);out=root/name;cfg=json.loads(CONFIG.read_text());raw=json.loads((out/'extreme_cases.json').read_text())
    unique={r['trade_key']:r for r in raw};groups=defaultdict(list)
    for r in unique.values():groups[r['symbol']].append(r)
    sources={r['symbol']:r for r in json.loads(Path(cfg['input_manifest']).read_text())['streams']};meta=parent.source.symbol_meta()
    with ProcessPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(worker,[(sources[s],meta[s],rows,cfg) for s,rows in groups.items()]))
    result=dict(status='passed',selections=len(raw),unique_trades=len(unique),receipts=results)
    (out/'extreme_verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status='passed',selections=len(raw),unique_trades=len(unique))),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--name',default='analysis_v1')
    a=p.parse_args();run(a.root,a.name)

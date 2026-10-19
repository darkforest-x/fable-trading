"""Rebuild closed prefixes for extreme stage trades and independently trace exits.

Audit targets are selected after results solely for error detection, never to
fit admission. Uses the legacy confirmation trace to identify first breakout /
retest and a separate bounded path interpreter to check exits and price PnL.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as parent
from yoyo.evaluation.spike_v128_retest_state import confirmation
from yoyo.evaluation.spike_v131_excursions import provenance, trace
from yoyo.evaluation.spike_v131_entry_stages import CONFIG, dump, read


def worker(args):
    source,meta,targets,cfg=args
    before=provenance(source['path']);raw=pd.read_csv(source['path'])
    base=raw.drop(columns='ts').set_axis(pd.to_datetime(raw.ts,unit='ms',utc=True))
    base=base[(base.index>=pd.Timestamp(cfg['warmup_start'])) & (base.index+pd.Timedelta(minutes=5)<=pd.Timestamp(cfg['end']))]
    outputs=[];tick=float(meta['tick'])
    for minutes,group in targets.groupby('timeframe_min'):
        bars,_=parent.complete_bars(base,int(minutes))
        full=parent.features(bars)
        arrays={k:full[k].to_numpy(float) for k in ['open','high','low','close','atr']}
        for r in group.to_dict('records'):
            a,d,e,x=[int(r[k]) for k in ['anchor_i','decision_i','entry_i','exit_i']];s=int(r['side'])
            prefix=bars.iloc[:d+1];close_time=prefix.index[-1]+pd.Timedelta(minutes=int(minutes))
            rawprefix=base[base.index+pd.Timedelta(minutes=5)<=close_time]
            facts=(parent.facts_for(prefix,rawprefix,meta['asset'],tick,int(minutes)) if minutes in (15,60)
                   else parent._generic_facts(prefix,meta['asset'],tick,int(minutes)))
            f=facts['frame'];assert facts['v9'][a] and facts['side'][a]==s
            atr=f.atr.iloc[a];extreme=f.low.iloc[a-4:a+1].min() if s==1 else f.high.iloc[a-4:a+1].max()
            rawstop=min(extreme-.2*atr,f.close.iloc[a]-2*atr) if s==1 else max(extreme+.2*atr,f.close.iloc[a]+2*atr)
            stop=(math.floor(rawstop/tick) if s==1 else math.ceil(rawstop/tick))*tick
            assert math.isclose(stop,r['initial_stop'],abs_tol=1e-9,rel_tol=1e-10)
            if r['policy']!='immediate':
                legacy=confirmation(f.open.to_numpy(),f.high.to_numpy(),f.low.to_numpy(),f.close.to_numpy(),
                    facts['gap'],facts['side'],a,s,stop,24)
                key={'breakout':'breakout_i','pullback':'retest_i','rebreak':'confirmation_i'}[r['policy']]
                assert legacy[key]==d
            else:assert d==a
            assert e==d+1 and math.isclose(arrays['open'][e],r['entry_price'],abs_tol=1e-10)
            outcome=trace(side=s,entry=r['entry_price'],stop0=stop,tick=tick,
                o=arrays['open'],h=arrays['high'],l=arrays['low'],c=arrays['close'],atr=arrays['atr'],
                e=e,x=x,opposite=str(r['exit_reason']).startswith('opposite'),trail=2 if minutes==60 else 4)
            assert outcome['actual_exit_i']==x
            assert math.isclose(outcome['actual_exit_price'],r['exit_price'],abs_tol=1e-9,rel_tol=1e-10)
            net=(s*(outcome['actual_exit_price']-r['entry_price'])-.002*r['entry_price'])/r['initial_risk']
            assert math.isclose(net,r['net_r'],abs_tol=1e-8)
            outputs.append(dict(trade_key=r['trade_key'],policy=r['policy'],selection=r['audit_selection'],
                decision_time=r['decision_time'],net_r=r['net_r'],status='passed'))
    assert before==provenance(source['path'])
    return dict(source=before,cases=outputs)


def run(root):
    root=Path(root);cfg=json.loads(CONFIG.read_text());selected=[]
    for minutes in cfg['timeframes']:
        f=read(root/f'events_{minutes}m.csv.gz')
        f=f[f.cohort.eq('signal') & f.serial_all & f.status.eq('closed')]
        for policy,g in f.groupby('policy'):
            for label,i in [('max_net_r',g.net_r.idxmax()),('min_net_r',g.net_r.idxmin())]:
                selected.append(g.loc[i].to_dict()|dict(audit_selection=label))
    targets=pd.DataFrame(selected);meta=parent.source.symbol_meta()
    sources={r['symbol']:r for r in json.loads(Path(cfg['input_manifest']).read_text())['streams']}
    args=[(sources[s],meta[s],g,cfg) for s,g in targets.groupby('symbol')]
    with ProcessPoolExecutor(max_workers=4) as pool:results=list(pool.map(worker,args))
    out=dict(status='passed',cases=len(targets),receipts=results)
    dump(root/'prefix_verification.json',out)
    print(json.dumps(dict(status='passed',cases=len(targets))))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);run(p.parse_args().root)

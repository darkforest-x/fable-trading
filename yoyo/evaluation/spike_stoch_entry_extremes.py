"""Read raw prices for selected tail returns/MAE and validate causal triggers.

Selections are prespecified max/min netR and max MAE per policy/timeframe.
Independent local oscillator arithmetic verifies entry triggers. Frozen replay
and a second excursion trace verify timestamps, geometry and actual exit.
This is research validation, not a new policy or a native TradingView check.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
import json
import math
from pathlib import Path

import numpy as np

from yoyo.evaluation.spike_stoch_entry import timed_attempt, excursions
from yoyo.evaluation.spike_stoch_entry_study import CONFIG
from yoyo.evaluation.spike_fanshen_exit import compute_signals
from yoyo.evaluation.spike_v131_rule_study import load_base
from yoyo.evaluation.spike_v131_entry_stages import parent, attempt, dump
from yoyo.evaluation.spike_v131_rule_summary import read


def scalar_cross(high,low,close,i):
    """Recompute a K/D close using raw small-window arithmetic, no rolling API.

    The 20-bar prefix is sufficient if stochastic windows are all nonzero;
    illiquid flat-window cases fall back to the entire causal prefix.
    """
    start=max(0,i-20)
    if any(max(high[max(0,j-4):j+1])==min(low[max(0,j-4):j+1]) for j in range(start,i+1)):
        start=0
    stoch=[];ks=[];last=[]
    for j in range(start,i+1):
        hi=max(high[max(0,j-4):j+1]);lo=min(low[max(0,j-4):j+1])
        if hi!=lo:stoch.append((close[j]-lo)/(hi-lo)*100)
        k=sum(stoch[-3:])/3 if len(stoch)>=3 else math.nan
        if math.isfinite(k):ks.append(k)
        d=sum(ks[-3:])/3 if len(ks)>=3 else math.nan
        last.append((k,d))
    k,d=last[-1];pk,pd=last[-2]
    return k,d,k>d and pk<=pd and k<20 and d<20


def scalar_green(close,low,i):
    """Current WVF against20 population-SD and50 maximum; prior/current only."""
    w=[]
    for j in range(max(0,i-49),i+1):
        highclose=max(close[max(0,j-21):j+1])
        w.append((highclose-low[j])/highclose*100)
    mean=sum(w[-20:])/20
    sd=math.sqrt(sum((v-mean)**2 for v in w[-20:])/20)
    return w[-1],w[-1]>=mean+2*sd or w[-1]>=max(w)*.85


def run(root,name):
    root=Path(root);out=root/name;cfg=json.loads(CONFIG.read_text())
    trades=read(out/'trades.csv.gz');selected=[]
    for (m,policy),g in trades.groupby(['timeframe_min','policy']):
        for label,index in [('max_net',g.net_r.idxmax()),('min_net',g.net_r.idxmin()),('max_mae',g.mae_r.idxmax())]:
            r=trades.loc[index].to_dict();r['selection']=label;selected.append(r)
    targets={ (r['symbol'],int(r['timeframe_min']),r['policy'],r['trade_key']):r for r in selected }
    sources={x['symbol']:x for x in json.loads(Path(cfg['input_manifest']).read_text())['streams']}
    meta=parent.source.symbol_meta();checks=[]
    for symbol in sorted(set(k[0] for k in targets)):
        base=load_base(sources[symbol],cfg);info=meta[symbol]
        for m in sorted(set(k[1] for k in targets if k[0]==symbol)):
            bars,_=parent.complete_bars(base,m)
            facts=parent.facts_for(bars,base,info['asset'],float(info['tick']),m) if m in (15,60) else parent._generic_facts(bars,info['asset'],float(info['tick']),m)
            f=facts['frame'];p=parent.source.prepared_arm(f,facts['gap'],facts['side'],'extreme',{},m,float(info['tick']))
            if m==60:p=replace(p,spec=replace(p.spec,trail_atr=2.))
            s=compute_signals(f);arrow=s.arrow_long.to_numpy(bool);combined=s.alert_long.to_numpy(bool)
            for key,old in targets.items():
                if key[:2]!=(symbol,m):continue
                policy=old['policy'];i=int(old['anchor_i']);d=int(old['decision_i'])
                if policy in ('stoch','stoch_wvf','pullback_stoch','pullback_wvf'):
                    k,v,cross=scalar_cross(p.high,p.low,p.close,d);wvf,green=scalar_green(p.close,p.low,d)
                    np.testing.assert_allclose([k,v,wvf],[s.k.iloc[d],s.d.iloc[d],s.wvf.iloc[d]],atol=1e-8,rtol=1e-8)
                    assert cross
                    if policy in ('stoch_wvf','pullback_wvf'):assert green
                if policy.startswith('pullback_'):
                    r=timed_attempt(p,i,policy,arrow if policy=='pullback_stoch' else combined)
                else:r=attempt(p,i,1,'immediate' if policy.startswith('stoch') else policy)
                r.update(excursions(p,r))
                for field in ('entry_i','exit_i','entry_price','initial_stop','initial_risk','exit_price','net_r','mae_r','mae_bp','mfe_lower_r','mfe_upper_r'):
                    assert np.isclose(r[field],old[field],atol=1e-8,rtol=1e-10),(key,field)
                assert r['decision_i']==d and r['status']==old['status']
                checks.append(dict(symbol=symbol,timeframe_min=m,policy=policy,trade_key=old['trade_key'],passed=True))
    dump(out/'extreme_verification.json',dict(status='passed',selections=len(selected),unique_trades=len(targets),checks=checks))
    print(json.dumps(dict(extreme_selections=len(selected),unique_trades=len(targets),status='passed')),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--name',default='analysis_v1')
    a=p.parse_args();run(a.root,a.name)
